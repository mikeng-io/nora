"""`SqlTensionStore` — tension rows: a lesson the doctrine check refused, and the
law it pulled against.

Append-only. **Operator-only:** `nora lessons tensions` reads it; nothing a model
holds does (no read tool, no block, pinned by
`tests/unit/test_doctrine_check.py::test_no_model_surface_reads_a_tension_row`).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.doctrine_check import Tension
from nora.infra.sql.schema import SELF_TENSION

__all__ = ["SqlTensionStore"]


def _tension(row: sa.Row[Any]) -> Tension:
    return Tension(
        id=row.id, unit=row.unit, source=row.source, doctrine_file=row.doctrine_file,
        doctrine_line=row.doctrine_line, doctrine_text=row.doctrine_text,
        would_have_said=row.would_have_said, citations=tuple(row.citations),
        why=row.why, written_at=row.written_at, established_by=row.established_by,
        instruction_sha256=row.instruction_sha256, model=row.model,
        check_model=row.check_model,
    )


class SqlTensionStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(self, tension: Tension) -> str:
        """Append one row; returns its id."""
        values = {
            "unit": tension.unit, "source": tension.source,
            "doctrine_file": tension.doctrine_file, "doctrine_line": tension.doctrine_line,
            "doctrine_text": tension.doctrine_text,
            "would_have_said": tension.would_have_said,
            "citations": list(tension.citations), "why": tension.why,
            "written_at": tension.written_at, "established_by": tension.established_by,
            "instruction_sha256": tension.instruction_sha256, "model": tension.model,
            "check_model": tension.check_model,
        }
        async with self._engine.begin() as conn:
            return str((await conn.execute(
                sa.insert(SELF_TENSION).values(**values).returning(SELF_TENSION.c.id)
            )).scalar_one())

    async def recent(self, *, unit: str | None = None, limit: int = 50) -> tuple[Tension, ...]:
        """The newest `limit` rows, newest first, of one unit or all."""
        stmt = sa.select(SELF_TENSION).order_by(
            SELF_TENSION.c.written_at.desc(), SELF_TENSION.c.id).limit(limit)
        if unit is not None:
            stmt = stmt.where(SELF_TENSION.c.unit == unit)
        async with self._engine.connect() as conn:
            return tuple(_tension(r) for r in (await conn.execute(stmt)).all())
