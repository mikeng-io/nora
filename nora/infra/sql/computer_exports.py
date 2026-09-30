"""`ComputerExportStore` over SQLAlchemy Core: insert-only, like
`nora.infra.sql.computer_events`. The database enforces it too (migration
`0083_computer_exports`'s trigger refuses UPDATE and DELETE).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.computer_exports import ComputerExport, normalise_export
from nora.infra.sql.schema import COMPUTER_EXPORTS

_FIELDS = (
    "id", "created_at", "platform", "conversation_id", "scope", "path", "destination",
    "sha256", "size_bytes", "media_type", "title", "tag_id", "operation_id",
    "artifact_id", "url", "revised", "blob_key", "session_id",
)


def _row_to_export(r: Row[Any]) -> ComputerExport:
    return normalise_export(ComputerExport(**{f: getattr(r, f) for f in _FIELDS}))


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
