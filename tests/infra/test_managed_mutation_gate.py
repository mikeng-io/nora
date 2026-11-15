"""The real database gate serializes processes without exhausting the work pool."""

import asyncio
from types import MappingProxyType

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from nora.domain.managed import OperationJournalState, OperationRecord, OutcomeStatus
from nora.infra.sql.managed_identity import MANAGED_MUTATION_LOCK, SqlBindingStore


@pytest.fixture
async def database(pg_module_url):
    engine = create_async_engine(pg_module_url, pool_size=2, max_overflow=0, pool_timeout=1)
    async with engine.begin() as connection:
        await connection.execute(sa.text("TRUNCATE managed_operation"))
    try:
        yield engine
    finally:
        await engine.dispose()


async def try_gate(engine):
    async with engine.begin() as connection:
        return await connection.scalar(sa.select(sa.func.pg_try_advisory_xact_lock(
            sa.func.hashtextextended(MANAGED_MUTATION_LOCK, 0),
        )))


async def test_mutation_gate_holds_a_database_wide_lock_and_releases_it(database):
    store = SqlBindingStore(database)
    assert await try_gate(database) is True
    async with store.mutation():
        assert await try_gate(database) is False
    assert await try_gate(database) is True


async def test_a_hundred_queued_mutations_leave_a_connection_for_the_work(database):
    store = SqlBindingStore(database)

    async def write(index):
        async with store.mutation():
            record = OperationRecord(f"generated-mutation-{index}", "grant_access",
                OperationJournalState.PENDING, MappingProxyType({"authority": "pending"}))
            return await store.record_operation(record)

    async with asyncio.timeout(20):
        results = await asyncio.gather(*(write(index) for index in range(100)))
    assert all(result.status is OutcomeStatus.COMPLETE for result in results)
    assert len(await store.incomplete_operations()) == 100
