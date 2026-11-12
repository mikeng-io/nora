"""Append-only Notebook revisions; the entry row serializes competing appends."""

from dataclasses import asdict
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.entries import Entry, EntryContent, EntryRevision, check_entry
from nora.domain.lineage import ROW, Lineage, message, topic_revision_lineage
from nora.domain.rollup import room_key
from nora.infra.sql.schema import MEMORY_NODE, MESSAGES, STIMULUS
from nora.infra.sql.schema import NOTEBOOK_ENTRY as E
from nora.infra.sql.schema import NOTEBOOK_ENTRY_REVISION as R

_MESSAGE_ID_MAX = 2**31 - 1
_STIMULUS_KEYED = ("social", "play")


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


def _room_lineage(room: str) -> Lineage:
    return Lineage.of({ROW: (message(room),)})


async def _citation_lineages(
    conn: AsyncConnection, *, unit: str, citations: tuple[tuple[str, str], ...]
) -> dict[tuple[str, str], Lineage | None]:
    """Each citation key to the cited row's own lineage; `None` where nothing resolves."""
    out: dict[tuple[str, str], Lineage | None] = dict.fromkeys(set(citations))
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
        for r in rows:
            out[("message", str(r.id))] = _room_lineage(
                room_key(r.platform, r.conversation_id)
            )
    sids = {
        int(i)
        for k, i in set(citations)
        if k in _STIMULUS_KEYED and i.isdigit()
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
            for k in _STIMULUS_KEYED:
                if (k, str(r.id)) in out:
                    out[(k, str(r.id))] = _room_lineage(
                        room_key(r.platform, r.conversation_id)
                    )
    rest = [k for k, v in out.items() if v is None]
    if rest:
        nodes = (
            await conn.execute(
                sa.select(
                    MEMORY_NODE.c.kind, MEMORY_NODE.c.source_id,
                    MEMORY_NODE.c.lineage, MEMORY_NODE.c.origin_room,
                ).where(
                    sa.tuple_(MEMORY_NODE.c.kind, MEMORY_NODE.c.source_id).in_(rest),
                )
            )
        ).all()
        for n in nodes:
            own = Lineage.from_json(n.lineage)
            if not any(es for _, es in own.sections) and n.origin_room:
                own = _room_lineage(n.origin_room)
            if any(es for _, es in own.sections):
                out[(n.kind, n.source_id)] = own
    return out


async def _topic_lineage(
    conn: AsyncConnection, *, unit: str, content: EntryContent
) -> Lineage:
    """The revision's lineage; refuses `citation_lineage` on a key that resolves to nothing."""
    lineages = await _citation_lineages(
        conn, unit=unit, citations=content.citations
    )
    if any(lineages.get(k) is None for k in set(content.citations)):
        raise ValueError("citation_lineage")
    return topic_revision_lineage(cited=content.citations, lineages=lineages)


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
                lineage = await _topic_lineage(conn, unit=unit, content=content)
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
            lineage = await _topic_lineage(conn, unit=unit, content=content)
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
