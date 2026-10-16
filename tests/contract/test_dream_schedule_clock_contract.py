"""`_ScheduleClock` has two implementations, so it gets one suite both pass —
CLAUDE.md's rule.

The properties that matter are all about the ONE thing this port exists for:
a schedule that survives the process. A store that forgot, or that invented a
timestamp for a pair it had never seen, would let a daily adaptor fire on every
tick while every test stayed green.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, Protocol
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.infra.sql.dream_schedule import SqlDreamScheduleClock
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import InMemoryDreamScheduleClock

NOW = 1_757_000_000_000


class _Clock(Protocol):
    async def last_fired(self, *, adaptor_name: str, key: str) -> int | None: ...
    async def record_fire(
        self, *, adaptor_name: str, key: str, at_ms: int
    ) -> None: ...


@pytest.fixture(scope="module")
def _clock_db(pg_admin: Any) -> Iterator[str]:
    """MODULE-scoped, per CLAUDE.md: build the schema once and TRUNCATE
    between tests. The function-scoped `pg_url` fixture runs the whole
    migration chain per test, which at suite scale degrades the container
    enough to fail unrelated wall-clock tests elsewhere."""
    dbname = "t_dreamsched_" + uuid4().hex
    with psycopg.connect(
        pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
    ) as conn:
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
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["memory", "sql"])
async def clock(request: Any) -> AsyncIterator[_Clock]:
    if request.param == "memory":
        yield InMemoryDreamScheduleClock()
        return
    engine = create_sql_engine(request.getfixturevalue("_clock_db"))
    try:
        yield SqlDreamScheduleClock(engine)
    finally:
        async with engine.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate dream_schedule_state cascade"))
        await engine.dispose()


async def test_a_pair_that_never_fired_reads_as_none(clock):
    """**The property this suite exists for.** `None` and `0` both fire on the
    next tick, so a store substituting epoch 0 for an absent row would pass
    every behavioural assertion while writing a 1970 timestamp into a table an
    operator reads to answer "when did this last run".

    Falsification: give `SqlDreamScheduleClock.last_fired` a `or 0`, or make
    the fake's `.get` take a default — this is the only test that goes red.
    """
    assert await clock.last_fired(adaptor_name="fact-adjudication", key="friends") is None


async def test_a_recorded_fire_reads_back(clock):
    await clock.record_fire(
        adaptor_name="fact-adjudication", key="friends", at_ms=NOW)
    got = await clock.last_fired(adaptor_name="fact-adjudication", key="friends")
    assert got == NOW


async def test_a_second_fire_replaces_the_first(clock):
    """The ordinary case on day two. A store that INSERTed without an upsert
    would raise on the primary key here and cost the adaptor every subsequent
    day, which is a failure the loop absorbs into `unavailable` and nobody
    waits on."""
    await clock.record_fire(
        adaptor_name="fact-adjudication", key="friends", at_ms=NOW)
    await clock.record_fire(
        adaptor_name="fact-adjudication", key="friends", at_ms=NOW + 86_400_000)
    got = await clock.last_fired(adaptor_name="fact-adjudication", key="friends")
    assert got == NOW + 86_400_000


async def test_two_keys_of_one_adaptor_do_not_share_a_clock(clock):
    """Every unit runs the tier on its own schedule. One shared row would
    make a quiet unit's fire satisfy a busy one's slot."""
    await clock.record_fire(
        adaptor_name="fact-adjudication", key="friends", at_ms=NOW)
    assert await clock.last_fired(
        adaptor_name="fact-adjudication", key="ridgeline") is None


async def test_two_adaptors_on_one_key_do_not_share_a_clock(clock):
    """The phase offset depends on this. `fact-adjudication` and
    `fact-entity-resolution` run over the SAME unit an hour apart, so a
    clock keyed on the unit alone would let the first one's fire consume
    the second one's slot — the two would collapse back onto one pass, which
    is exactly the concurrency the offsets exist to separate."""
    await clock.record_fire(
        adaptor_name="fact-adjudication", key="friends", at_ms=NOW)
    assert await clock.last_fired(
        adaptor_name="fact-entity-resolution", key="friends") is None


async def test_a_key_containing_a_colon_is_its_own_row(clock):
    """Live scope ids contain colons and slashes
    (`100000000000000201/100000000000000202`). A store keyed on a joined
    `f"{adaptor}:{key}"` string merges pairs Postgres keeps apart, and the
    two-column key is what makes that impossible."""
    await clock.record_fire(adaptor_name="a:b", key="c", at_ms=NOW)
    assert await clock.last_fired(adaptor_name="a", key="b:c") is None
