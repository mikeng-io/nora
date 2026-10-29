"""Append-only Notebook revisions; the entry row serializes competing appends."""

from dataclasses import asdict
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.entries import Entry, EntryContent, EntryRevision, check_entry
from nora.infra.sql.schema import NOTEBOOK_ENTRY as E
from nora.infra.sql.schema import NOTEBOOK_ENTRY_REVISION as R


def _revision(row: Any) -> EntryRevision:
    values = dict(row.content)
    for name in ("about", "citations", "links"):
        values[name] = tuple(tuple(k) for k in values[name])
    return EntryRevision(
        row.id, row.entry_id, row.base_revision_id, row.seq, EntryContent(**values)
    )


class SqlEntryStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create(self, *, kind: str, unit: str, content: EntryContent) -> Entry:
        check_entry(kind, content)
        if not unit.strip():
            raise ValueError("unit")
        eid, rid = str(uuid4()), str(uuid4())
        async with self.engine.begin() as conn:
            await conn.execute(sa.insert(E).values(id=eid, kind=kind, unit=unit))
            await conn.execute(
                sa.insert(R).values(id=rid, entry_id=eid, seq=1, content=asdict(content))
            )
        return Entry(eid, kind, unit, EntryRevision(rid, eid, None, 1, content))

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
            rid = str(uuid4())
            await conn.execute(
                sa.insert(R).values(
                    id=rid,
                    entry_id=entry_id,
                    base_revision_id=base_revision_id,
                    seq=row.seq + 1,
                    content=asdict(content),
                )
            )
        return EntryRevision(rid, entry_id, base_revision_id, row.seq + 1, content)
