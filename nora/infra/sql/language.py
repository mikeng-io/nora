"""The SQL cursor for incremental lesson evidence."""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.schema import CONSOLIDATION_WATERMARK

log = logging.getLogger(__name__)




class SqlLanguageStore:
    """The Dreamer's consolidation cursor — `nora.app.runtime
    ._ConsolidationCursor`'s six verbs, and nothing else. Every scheduled
    adaptor reads and advances it, so this store is TIER-NEUTRAL despite the
    module's inherited name (see the module docstring)."""

    def __init__(self, engine: AsyncEngine) -> None:
        """`metrics` is INJECTED, never imported — the same discipline
        `SqlRecordStore.__init__` documents: `nora.infra` sits BELOW
        `nora.obs` in import-linter's layers contract, so importing
        `nora.obs.metrics` here (even lazily, inside a method) breaks that
        contract. `Any` rather than `Metrics` for the same reason; `None`
        (the default) means this store simply does not count, which is
        correct for every unit test that does not care."""
        self._engine = engine

    async def watermark_for(self, *, consolidator_name: str, scope_id: str) -> int:
        stmt = sa.select(CONSOLIDATION_WATERMARK.c.last_message_id).where(
            CONSOLIDATION_WATERMARK.c.consolidator_name == consolidator_name,
            CONSOLIDATION_WATERMARK.c.scope_id == scope_id,
        )
        async with self._engine.connect() as conn:
            val = (await conn.execute(stmt)).scalar_one_or_none()
        return int(val) if val is not None else 0

    async def advance_watermark(
        self, *, consolidator_name: str, scope_id: str, last_message_id: int
    ) -> None:
        stmt = (
            pg.insert(CONSOLIDATION_WATERMARK)
            .values(
                consolidator_name=consolidator_name,
                scope_id=scope_id,
                last_message_id=last_message_id,
                updated_at=sa.func.now(),
            )
            .on_conflict_do_update(
                index_elements=[
                    CONSOLIDATION_WATERMARK.c.consolidator_name,
                    CONSOLIDATION_WATERMARK.c.scope_id,
                ],
                set_={"last_message_id": last_message_id, "updated_at": sa.func.now()},
            )
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
