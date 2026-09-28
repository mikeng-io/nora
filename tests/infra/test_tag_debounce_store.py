"""`SqlTagDebounceStore` — R-9's rate limiter, across processes (0056).

Single-store file, MODULE-scoped database per `test_tag_trace_store.py`.

The property under test is the one that makes this table bounded without a
sweep loop: `prune_before` drops exactly the rows whose window has already
elapsed, so the deletion is lossless by arithmetic rather than by a retention
judgement.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.tag_debounce import SqlTagDebounceStore

T0 = 1_757_000_000_000


@pytest.fixture(scope="module")
def _debounce_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_tag_debounce_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture
async def store(_debounce_db: str) -> AsyncIterator[SqlTagDebounceStore]:
    engine = create_sql_engine(_debounce_db)
    try:
        yield SqlTagDebounceStore(engine)
    finally:
        async with engine.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate tag_debounce_state"))
        await engine.dispose()


async def test_an_empty_table_reads_as_nothing(store: SqlTagDebounceStore) -> None:
    assert await store.read_all() == []


async def test_a_tag_round_trips(store: SqlTagDebounceStore) -> None:
    await store.record_allowed(platform="signal", conversation_id="gA", at_ms=T0)
    assert await store.read_all() == [("signal", "gA", T0)]


async def test_a_second_tag_upserts_rather_than_raising(
    store: SqlTagDebounceStore,
) -> None:
    """The second tag in a conversation is the ordinary case."""
    await store.record_allowed(platform="signal", conversation_id="gA", at_ms=T0)
    await store.record_allowed(platform="signal", conversation_id="gA", at_ms=T0 + 9_000)
    assert await store.read_all() == [("signal", "gA", T0 + 9_000)]


async def test_conversations_are_independent_rows(
    store: SqlTagDebounceStore,
) -> None:
    await store.record_allowed(platform="signal", conversation_id="gA", at_ms=T0)
    await store.record_allowed(platform="signal", conversation_id="gB", at_ms=T0 + 1)
    await store.record_allowed(platform="discord", conversation_id="gA", at_ms=T0 + 2)
    assert sorted(await store.read_all()) == [
        ("discord", "gA", T0 + 2),
        ("signal", "gA", T0),
        ("signal", "gB", T0 + 1),
    ]


async def test_prune_drops_only_what_can_no_longer_decide(
    store: SqlTagDebounceStore,
) -> None:
    await store.record_allowed(platform="signal", conversation_id="old", at_ms=T0 - 1)
    await store.record_allowed(platform="signal", conversation_id="edge", at_ms=T0)
    await store.record_allowed(platform="signal", conversation_id="fresh", at_ms=T0 + 1)
    assert await store.prune_before(before_ms=T0) == 1
    assert sorted(c for _, c, _ in await store.read_all()) == ["edge", "fresh"]


async def test_prune_on_an_empty_table_is_zero(store: SqlTagDebounceStore) -> None:
    assert await store.prune_before(before_ms=T0) == 0
