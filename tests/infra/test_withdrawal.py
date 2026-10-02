"""The hide a platform delete does (`SqlRecordStore.hide_message`).

Module-scoped database per `test_culture_store.py`'s pattern (one migrated
database, truncated between tests)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.history import as_of
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import MESSAGES

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_withdrawal_" + uuid4().hex
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
async def engine(_db: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(_db)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            # A reset, not a write: TRUNCATE is refused on a HISTORY_TABLES
            # table unless capture is off (`nora_row_history_refuse_truncate`,
            # `test_culture_store.py`'s own precedent).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            for table in ("messages", "row_history"):
                await conn.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
        await eng.dispose()


async def _message(engine: Any, *, conversation_id: str = "g1", ts: int = 1000) -> int:
    store = SqlRecordStore(engine)
    return await store.append(
        message=NormalizedMessage(
            "signal", conversation_id, "group", "+1", "Miles", ts, "hi", {},
        ),
        person_id="miles",
    )


async def test_hide_message_hides_a_row_once_and_a_snapshot_before_it_restores_the_row(engine):
    """A platform delete hides the message (`RecordStore.hide_message`): the first call
    hides it and says so, a second keeps the first timestamp and says False, and the
    transcript read no longer returns it. A snapshot taken before the hide shows it
    unhidden (`as_of` is what `nora.infra.sql.snapshot_at.seed` builds a sandbox from)."""
    store = SqlRecordStore(engine)
    mid = await _message(engine, ts=1000)
    async with engine.connect() as conn:
        before = (await conn.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()

    assert await store.hide_message(message_id=mid, now_ms=9000) is True
    assert await store.hide_message(message_id=mid, now_ms=9500) is False
    assert await store.hide_message(message_id=987_654, now_ms=9000) is False

    async with engine.connect() as conn:
        held = (await conn.execute(
            sa.select(MESSAGES.c.hidden_at).where(MESSAGES.c.id == mid)
        )).scalar_one()
    assert held == 9000, "the second delete keeps the first hide's timestamp"
    assert await store.recent(conversation_id="g1", limit=10) == [], "a hidden row is not read"

    async with engine.connect() as conn:
        restored = [
            row for part in [p async for p in as_of(conn, "messages", before)]
            for row in part
        ]
    assert len(restored) == 1 and restored[0]["id"] == mid
    assert restored[0]["hidden_at"] is None
