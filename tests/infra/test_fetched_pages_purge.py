"""`SqlFetchedPageStore.purge_expired` against the real table: the rows past
their expiry go, and only the blob keys no other row names come back."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.fetched_page import FetchedPage
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.fetched_pages import SqlFetchedPageStore

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _page(conversation: str, url: str, key: str, *, live: bool) -> FetchedPage:
    expires = NOW + timedelta(hours=1) if live else NOW - timedelta(hours=1)
    return FetchedPage(conversation, url, key, NOW - timedelta(days=1), expires, {})


@pytest.fixture(scope="module")
def _db(pg_admin: Any, pg_template: str) -> Iterator[str]:
    dbname = "t_fetched_pages_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}" TEMPLATE "{pg_template}"')
    url = (f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
           f"@{pg_admin.host}:{pg_admin.port}/{dbname}")
    try:
        yield url
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,))
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture
async def store(_db: str) -> AsyncIterator[SqlFetchedPageStore]:
    engine = create_sql_engine(_db)
    try:
        yield SqlFetchedPageStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate fetched_pages"))
        await engine.dispose()


async def test_purge_drops_expired_rows_and_returns_only_unheld_keys(store):
    await store.put(page=_page("c1", "https://a.example", "k-old", live=False))
    await store.put(page=_page("c1", "https://b.example", "k-shared", live=False))
    await store.put(page=_page("c2", "https://c.example", "k-shared", live=True))
    await store.put(page=_page("c2", "https://d.example", "k-live", live=True))
    assert await store.purge_expired(now=NOW) == ["k-old"]
    assert await store.get(conversation_id="c2", url="https://d.example", now=NOW)
    assert await store.get(conversation_id="c2", url="https://c.example", now=NOW)
    # The expired rows are gone, not merely unreadable: a second purge finds none.
    assert await store.purge_expired(now=NOW) == []


async def test_purge_is_bounded_by_its_limit(store):
    for i in range(5):
        await store.put(page=_page("c1", f"https://{i}.example", f"k{i}", live=False))
    assert len(await store.purge_expired(now=NOW, limit=2)) == 2
    assert len(await store.purge_expired(now=NOW, limit=10)) == 3
