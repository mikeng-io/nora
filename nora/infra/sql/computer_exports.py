"""`ComputerExportStore` over SQLAlchemy Core: insert-only, like
`nora.infra.sql.computer_events`. The database enforces it too (migration
`0083_computer_exports`'s trigger refuses UPDATE and DELETE).

The three Artifacts reads (ADR 0023) put the viewer's rooms in the WHERE of the
statement that reads the row (`_shared`), the way `nora.infra.sql.documents`'s
`_visible` does: a row outside them is never fetched, so nothing above the store
can leak it by forgetting a check.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.computer_exports import (
    SHARED_DESTINATIONS,
    ComputerExport,
    SharedPage,
    normalise_export,
)
from nora.domain.documents import RoomKey, normalize_query
from nora.infra.sql.schema import COMPUTER_EXPORTS

_E = COMPUTER_EXPORTS
# Bounded here as well as by the caller: an unbounded OFFSET is a DataError.
_MAX_LIMIT = 1_000
_MAX_OFFSET = 2**31 - 1

_FIELDS = (
    "id", "created_at", "platform", "conversation_id", "scope", "path", "destination",
    "sha256", "size_bytes", "media_type", "title", "tag_id", "operation_id",
    "artifact_id", "url", "revised", "blob_key", "session_id",
)


def _row_to_export(r: Row[Any]) -> ComputerExport:
    return normalise_export(ComputerExport(**{f: getattr(r, f) for f in _FIELDS}))


def _shared(rooms: frozenset[RoomKey]) -> sa.ColumnElement[bool]:
    """A row sent to one of `rooms` (`chat` or `link`, never `keep`). The rooms are a
    literal list, so no rooms matches nothing."""
    return sa.and_(
        _E.c.destination.in_(SHARED_DESTINATIONS),
        sa.tuple_(_E.c.platform, _E.c.conversation_id).in_(sorted(rooms)),
    )


class SqlComputerExportStore:
    """Satisfies `nora.domain.computer_exports.ComputerExportStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(self, export: ComputerExport) -> None:
        row = normalise_export(export)
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(COMPUTER_EXPORTS).values(**{f: getattr(row, f) for f in _FIELDS})
            )

    async def for_artifact(self, *, artifact_id: str) -> ComputerExport | None:
        stmt = (
            sa.select(COMPUTER_EXPORTS)
            .where(COMPUTER_EXPORTS.c.artifact_id == artifact_id)
            .order_by(COMPUTER_EXPORTS.c.created_at, COMPUTER_EXPORTS.c.id)
            .limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
        return None if row is None else _row_to_export(row)

    async def list_shared(
        self, *, rooms: frozenset[RoomKey], limit: int, offset: int, query: str = "",
    ) -> SharedPage:
        if not rooms:
            return SharedPage(items=(), total=0)
        # One line per link (its newest revision) and one per attachment: a link's rows
        # share an `artifact_id`, an attachment is its own row. Ranked over what the viewer
        # may see only, so a row of another room can never be the "newest" of anything.
        rank = sa.func.row_number().over(
            partition_by=sa.func.coalesce(_E.c.artifact_id, sa.cast(_E.c.id, sa.Text)),
            order_by=(_E.c.created_at.desc(), _E.c.id.desc()),
        ).label("rank")
        latest = sa.select(_E, rank).where(_shared(rooms)).subquery()
        where = latest.c.rank == 1
        needle = normalize_query(query)
        if needle:
            where = sa.and_(where, latest.c.title.icontains(needle, autoescape=True))
        limit = min(max(limit, 0), _MAX_LIMIT)
        offset = min(max(offset, 0), _MAX_OFFSET)
        count = sa.select(sa.func.count()).select_from(latest).where(where)
        page = (
            sa.select(*(latest.c[f] for f in _FIELDS))
            .where(where)
            .order_by(latest.c.created_at.desc(), latest.c.id)
            .limit(limit)
            .offset(offset)
        )
        async with self._engine.connect() as conn:
            total = (await conn.execute(count)).scalar_one()
            rows = (await conn.execute(page)).all() if limit > 0 else []
        return SharedPage(items=tuple(_row_to_export(r) for r in rows), total=int(total))

    async def shared_link(
        self, *, artifact_id: str, rooms: frozenset[RoomKey],
    ) -> ComputerExport | None:
        if not rooms:
            return None
        stmt = (
            sa.select(_E)
            .where(_shared(rooms), _E.c.destination == "link", _E.c.artifact_id == artifact_id)
            .order_by(_E.c.created_at.desc(), _E.c.id.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
        return None if row is None else _row_to_export(row)

    async def shared_attachment(
        self, *, export_id: UUID, rooms: frozenset[RoomKey],
    ) -> ComputerExport | None:
        if not rooms:
            return None
        stmt = sa.select(_E).where(
            _shared(rooms), _E.c.destination == "chat", _E.c.id == export_id,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
        return None if row is None else _row_to_export(row)
