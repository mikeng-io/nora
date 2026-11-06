"""Generated normal Dreamer population plus held elections and foreground SQL."""
import asyncio
from contextlib import AsyncExitStack

import sqlalchemy as sa

from nora.agent.dream_store import model_phase, store_phase, store_unit
from nora.infra.sql.engine import create_sql_engine


async def test_background_population_reserves_connections_for_foreground(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    slots = asyncio.Semaphore(2)
    entered = asyncio.Queue()
    release = asyncio.Event()
    model = asyncio.Event()
    tasks = []
    try:
        async with AsyncExitStack() as stack:
            # Four intentionally long-lived reader-election sessions.
            for i in range(4):
                conn = await stack.enter_async_context(engine.connect())
                assert await conn.scalar(sa.text('SELECT pg_try_advisory_lock(:key)'),
                                         {'key': i + 700})

            async def unit(index):
                async with store_unit(slots) as lease:
                    async with asyncio.timeout(5) as deadline:
                        lease.deadline = deadline
                        async with engine.connect() as conn:
                            assert await conn.scalar(sa.text('SELECT 1')) == 1
                            await entered.put(index)
                            await release.wait()
                        async with model_phase():
                            await model.wait()
                            async with store_phase(), engine.connect() as conn:
                                assert await conn.scalar(sa.text('SELECT 2')) == 2

            tasks = [asyncio.create_task(unit(i)) for i in range(72)]
            assert {await entered.get(), await entered.get()} == {0, 1}
            assert engine.pool.checkedout() == 6
            # Live ingestion can actually check out and execute while both
            # background phases and all four election sessions hold connections.
            async with engine.connect() as foreground:
                assert await foreground.scalar(sa.text('SELECT 42')) == 42
                assert engine.pool.checkedout() == 7
            tasks[0].cancel()
            await asyncio.gather(tasks[0], return_exceptions=True)
            assert await entered.get() == 2
            release.set()
            for _ in range(69):
                await entered.get()
            # All units reached their model wait without occupying store slots.
            async with engine.connect() as foreground:
                assert await foreground.scalar(sa.text('SELECT 43')) == 43
            model.set()
            await asyncio.gather(*tasks[1:])
            assert slots._value == 2
            assert engine.pool.checkedout() == 4
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()
