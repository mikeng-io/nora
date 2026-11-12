"""Append-only Notebook revisions; the entry row serializes competing appends."""

from dataclasses import asdict
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.entries import Entry, EntryContent, EntryRevision, check_entry
from nora.domain.lineage import Lineage, topic_revision_lineage
from nora.domain.memory_graph import ROLLUP
from nora.domain.rollup import room_key
from nora.infra.sql.schema import MESSAGES, STIMULUS
from nora.infra.sql.schema import NOTEBOOK_ENTRY as E
from nora.infra.sql.schema import NOTEBOOK_ENTRY_REVISION as R

_MESSAGE_ID_MAX = 2**31 - 1


def _revision(row: Any) -> EntryRevision:
    values = dict(row.content)
    for name in ("about", "citations", "links"):
        values[name] = tuple(tuple(k) for k in values[name])
    return EntryRevision(
        row.id, row.entry_id, row.base_revision_id, row.seq,
        EntryContent(**values),
        written_ms=int(row.recorded_at.timestamp() * 1000),
        lineage=Lineage.from_json(row.lineage),
    )


async def _citation_rooms(
    conn: AsyncConnection, *, unit: str, citations: tuple[tuple[str, str], ...]
) -> dict[tuple[str, str], str]:
    """Each citation key's room, for `topic_revision_lineage`; the rest stay unplaced."""
    rooms: dict[tuple[str, str], str] = {}
    mids = [
        int(i)
        for k, i in set(citations)
        if k == "message" and i.isdigit() and int(i) <= _MESSAGE_ID_MAX
    ]
    if mids:
        rows = (
            await conn.execute(
                sa.select(
                    MESSAGES.c.id, MESSAGES.c.platform, MESSAGES.c.conversation_id
                ).where(MESSAGES.c.id.in_(mids), MESSAGES.c.hidden_at.is_(None))
            )
        ).all()
        rooms.update(
            (("message", str(r.id)), room_key(r.platform, r.conversation_id))
            for r in rows
        )
    sids = {
        int(i)
        for k, i in set(citations)
        if k in ROLLUP and i.isdigit()
    }
    if sids:
        rows = (
            await conn.execute(
                sa.select(
                    STIMULUS.c.id, STIMULUS.c.platform, STIMULUS.c.conversation_id
                ).where(STIMULUS.c.unit == unit, STIMULUS.c.id.in_(sids))
            )
        ).all()
        for r in rows:
            if r.platform is None or r.conversation_id is None:
                continue
            room = room_key(r.platform, r.conversation_id)
            rooms.update(
                (key, room)
                for key in set(citations)
                if key[0] in ROLLUP and key[1] == str(r.id)
            )
    return rooms


class SqlEntryStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create(self, *, kind: str, unit: str, content: EntryContent) -> Entry:
        check_entry(kind, content)
        if not unit.strip():
            raise ValueError("unit")
        eid, rid = str(uuid4()), str(uuid4())
        # The partial unique index on (unit, subject) over non-retired rows
        # makes the duplicate refusal atomic across sessions and processes.
        try:
            async with self.engine.begin() as conn:
                rooms = await _citation_rooms(conn, unit=unit, citations=content.citations)
                lineage = topic_revision_lineage(cited=content.citations, rooms=rooms)
                await conn.execute(
                    sa.insert(E).values(
                        id=eid, kind=kind, unit=unit,
                        subject=content.about[0][1].casefold(),
                    )
                )
                recorded_at = (
                    await conn.execute(
                        sa.insert(R).values(
                            id=rid, entry_id=eid, seq=1,
                            content=asdict(content), lineage=lineage.to_json(),
                        ).returning(R.c.recorded_at)
                    )
                ).scalar_one()
        except IntegrityError:
            raise ValueError("duplicate") from None
        return Entry(
            eid, kind, unit,
            EntryRevision(rid, eid, None, 1, content,
                          written_ms=int(recorded_at.timestamp() * 1000),
                          lineage=lineage),
        )

    async def get(self, *, unit: str, entry_id: str) -> Entry | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(E.c.kind, *R.c)
                    .join(R, R.c.entry_id == E.c.id)
                    .where(E.c.unit == unit, E.c.id == entry_id)
                    .order_by(R.c.seq.desc())
                    .limit(1)
                )
            ).first()
        return Entry(entry_id, row.kind, unit, _revision(row)) if row else None

    async def held(self, *, unit: str) -> tuple[Entry, ...]:
        """The unit's non-retired entries at their heads, by subject."""
        latest = (
            sa.select(R.c.entry_id, sa.func.max(R.c.seq).label("seq"))
            .group_by(R.c.entry_id)
            .subquery()
        )
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(E.c.kind, *R.c)
                    .join(R, R.c.entry_id == E.c.id)
                    .join(
                        latest,
                        sa.and_(
                            latest.c.entry_id == R.c.entry_id, latest.c.seq == R.c.seq
                        ),
                    )
                    .where(E.c.unit == unit, E.c.retired.is_(False))
                )
            ).all()
        entries = [Entry(row.entry_id, row.kind, unit, _revision(row)) for row in rows]
        entries.sort(key=lambda e: e.head.content.about[0][1].casefold())
        return tuple(entries)

    async def revisions(self, *, unit: str, entry_id: str) -> tuple[EntryRevision, ...]:
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(R)
                    .join(E, E.c.id == R.c.entry_id)
                    .where(E.c.unit == unit, E.c.id == entry_id)
                    .order_by(R.c.seq.desc())
                )
            ).all()
        return tuple(_revision(row) for row in rows)

    async def append(
        self, *, unit: str, entry_id: str, base_revision_id: str, content: EntryContent
    ) -> EntryRevision:
        async with self.engine.begin() as conn:
            entry = (
                await conn.execute(
                    sa.select(E).where(E.c.unit == unit, E.c.id == entry_id).with_for_update()
                )
            ).first()
            if entry is None:
                raise LookupError(entry_id)
            row = (
                await conn.execute(
                    sa.select(R).where(R.c.entry_id == entry_id).order_by(R.c.seq.desc()).limit(1)
                )
            ).one()
            if row.id != base_revision_id:
                raise ValueError("stale_base")
            check_entry(entry.kind, content, _revision(row).content)
            rooms = await _citation_rooms(conn, unit=unit, citations=content.citations)
            lineage = topic_revision_lineage(cited=content.citations, rooms=rooms)
            rid = str(uuid4())
            recorded_at = (
                await conn.execute(
                    sa.insert(R).values(
                        id=rid,
                        entry_id=entry_id,
                        base_revision_id=base_revision_id,
                        seq=row.seq + 1,
                        content=asdict(content),
                        lineage=lineage.to_json(),
                    ).returning(R.c.recorded_at)
                )
            ).scalar_one()
            if content.state == "retired":
                await conn.execute(
                    sa.update(E).where(E.c.id == entry_id).values(retired=True)
                )
        return EntryRevision(
            rid, entry_id, base_revision_id, row.seq + 1, content,
            written_ms=int(recorded_at.timestamp() * 1000), lineage=lineage,
        )
