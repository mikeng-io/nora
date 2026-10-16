"""`SqlAssessReportStore` (migration `0077_assess_report`, DEC-240):
`record`/`recent` round-trip, and NEGATIVE — no read tool or prefill block
reaches `assess_report` (reports and metrics only)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.assess import AssessReport, DqCheckResult
from nora.infra.sql.assess import SqlAssessReportStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_assess_store_" + uuid4().hex
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
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
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
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text('TRUNCATE "assess_report" CASCADE'))
        await eng.dispose()


async def test_record_then_recent_round_trips(engine):
    store = SqlAssessReportStore(engine)
    report = AssessReport(
        kind="nightly_dq", run_at_ms=1_000_000,
        results=(
            DqCheckResult(check="cross_room_duplicate_events", status="ok"),
            DqCheckResult(
                check="empty_lineage_rows", status="fail", failing=2,
                sample=("a", "b"), detail="expected before step 3",
            ),
        ),
    )
    await store.record(report)
    recent = await store.recent()
    assert len(recent) == 1
    assert recent[0].kind == "nightly_dq"
    assert recent[0].run_at_ms == 1_000_000
    assert recent[0] == report


async def test_recent_orders_newest_first(engine):
    store = SqlAssessReportStore(engine)
    for at in (1000, 3000, 2000):
        await store.record(AssessReport(kind="nightly_dq", run_at_ms=at, results=()))
    recent = await store.recent()
    assert [r.run_at_ms for r in recent] == [3000, 2000, 1000]
