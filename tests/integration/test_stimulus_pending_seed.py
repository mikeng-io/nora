"""First-use pending projection and append share one transaction boundary."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
import sqlalchemy as sa

from nora.domain.stimuli import NewStimulus
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.stimuli import SqlStimulusStore


@pytest.mark.parametrize("first", ["append", "seed"])
async def test_first_seed_and_append_serialize_without_losing_a_row(clean_pg_url, first):
    engine = create_sql_engine(clean_pg_url)
    paused, release, connected = asyncio.Event(), asyncio.Event(), asyncio.Event()
    second_pid = None

    class ObservedEngine:
        def __init__(self, *, hold: bool):
            self.hold = hold

        @asynccontextmanager
        async def begin(self):
            async with engine.begin() as conn:
                nonlocal second_pid
                owner = self
                writes = 0
                if not self.hold:
                    second_pid = await conn.scalar(sa.text("SELECT pg_backend_pid()"))
                    connected.set()

                class ObservedConnection:
                    async def execute(self, stmt: Any, *args: Any, **kwargs: Any):
                        nonlocal writes
                        result = await conn.execute(stmt, *args, **kwargs)
                        if (owner.hold and
                                getattr(getattr(stmt, "table", None), "name", None)
                                == "stimulus_pending"):
                            writes += 1
                            if writes == (1 if first == "append" else 2):
                                paused.set()
                                await release.wait()
                        return result

                yield ObservedConnection()

    held = SqlStimulusStore(ObservedEngine(hold=True))
    other = SqlStimulusStore(ObservedEngine(hold=False))
    stimulus = NewStimulus(
        unit="generated", kind="message", item="message",
        ref="generated:message", at_ms=0,
    )

    async def append(store):
        return await store.append(stimulus=stimulus, recorded_at_ms=0)

    async def seed(store):
        return await store.view(unit="generated", mode="live", now_ms=100, limit=1)

    operation, concurrent = (append, seed) if first == "append" else (seed, append)
    first_task = asyncio.create_task(operation(held))
    second_task = None
    async def blocked_on_lock():
        await connected.wait()
        async with engine.connect() as conn:
            async with asyncio.timeout(5):
                while True:
                    if await conn.scalar(sa.text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted)"
                    ), {"pid": second_pid}):
                        return True

    lock_task = asyncio.create_task(blocked_on_lock())
    try:
        await asyncio.wait_for(paused.wait(), 5)
        second_task = asyncio.create_task(concurrent(other))
        await asyncio.wait({second_task, lock_task}, timeout=5,
                           return_when=asyncio.FIRST_COMPLETED)
        serialized = lock_task.done() and lock_task.result() is True
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first_task, second_task), 5)
        sid = results[0] if first == "append" else results[1]
        view = await SqlStimulusStore(engine).view(
            unit="generated", mode="live", now_ms=100, limit=1,
        )
        assert [row.id for row in view.pending] == [sid]
        assert serialized, "the competing transaction did not wait on the unit lock"
    finally:
        release.set()
        await asyncio.gather(first_task, *([second_task] if second_task else []),
                             return_exceptions=True)
        lock_task.cancel()
        await asyncio.gather(lock_task, return_exceptions=True)
        await engine.dispose()
