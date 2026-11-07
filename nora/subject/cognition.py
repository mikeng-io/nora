"""Eval-facing recorded Cognition wake reads from a snapshot, never a model input substitute."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from nora.domain.stimuli import UnitView
from nora.infra.sql.cognition_replay import recorded_wake


async def wake_from_snapshot(database_url: str, wake_id: int) -> tuple[int, UnitView]:
    engine = create_async_engine(database_url)
    try:
        result = await recorded_wake(engine, wake_id)
        from .eval_evidence import current
        if evidence := current():
            async with engine.connect() as conn:
                revisions = list((await conn.execute(text(
                    "SELECT version_num FROM alembic_version"))).scalars())
            evidence.record("snapshot_manifest", wake_id=wake_id,
                            migration_revision=revisions, clock_ms=result[0])
        return result
    finally:
        await engine.dispose()
