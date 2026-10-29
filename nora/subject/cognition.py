"""Eval-facing recorded Cognition wake reads from a snapshot, never a model input substitute."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine

from nora.domain.stimuli import UnitView
from nora.infra.sql.cognition_replay import recorded_wake


async def wake_from_snapshot(database_url: str, wake_id: int) -> tuple[int, UnitView]:
    engine = create_async_engine(database_url)
    try:
        return await recorded_wake(engine, wake_id)
    finally:
        await engine.dispose()
