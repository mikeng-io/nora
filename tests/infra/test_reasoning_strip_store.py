"""`SqlReasoningStripStore` — the durable half of the reasoning-strip counter.

Single-store file, not a contract suite (there is one implementation). The
database is MODULE-scoped per `test_participation_state_store.py`'s pattern:
one `CREATE DATABASE` plus one migration chain for the whole file, truncated
between tests, because rebuilding the world per test degrades the shared
testcontainer and the cost lands on an unrelated wall-clock test elsewhere.

Written around the three properties that make this table worth existing rather
than a counter: the DENOMINATOR survives the write, an ABSENT tag is stored as
an absence rather than as a value, and an arithmetically impossible row is
refused by the database rather than trusted from the writer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from nora.domain.reasoning_strip import (
    NaiveReasoningStripDatetimeError,
    ReasoningStripObservation,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.reasoning_strip import SqlReasoningStripStore
from nora.infra.sql.schema import REASONING_STRIP_LOG

T0 = datetime(2026, 9, 17, 9, 42, 22, tzinfo=UTC)


@pytest.fixture(scope="module")
def _strip_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_reasoning_strip_" + uuid4().hex
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
async def engine(_strip_db: str) -> AsyncIterator[Any]:
    """The ENGINE is per test (cheap); the DATABASE and its migrations are
    module-scoped."""
    eng = create_sql_engine(_strip_db)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.execute(sa.text("truncate reasoning_strip_log"))
        await eng.dispose()


@pytest.fixture
def store(engine: Any) -> SqlReasoningStripStore:
    return SqlReasoningStripStore(engine)


def _observation(**overrides: Any) -> ReasoningStripObservation:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "path": "tag",
        "model": "nora",
        "reasoning_effort": "low",
        "tag_id": "d1ffb015",
        "model_calls": 3,
        "continuation_calls": 2,
        "strip_calls": 2,
        "stripped_blocks": 2,
        "created_at": T0,
    }
    fields.update(overrides)
    return ReasoningStripObservation(**fields)


async def _rows(engine: Any) -> list[Any]:
    async with engine.connect() as conn:
        return (await conn.execute(sa.select(REASONING_STRIP_LOG))).all()


async def test_the_row_carries_its_own_denominator(
    store: SqlReasoningStripStore, engine: Any
) -> None:
    """The property that makes this a measurement rather than a tally.

    `strip_calls` alone answers nothing — two strips is a catastrophe at two
    opportunities and a rounding error at two hundred, and that is exactly the
    reading a bare strip count cannot give. Both halves are on the row, so the
    rate is computable from this table alone, long after the process that
    produced it has been recreated.
    """
    await store.record_observation(_observation())
    (row,) = await _rows(engine)
    assert (row.strip_calls, row.continuation_calls) == (2, 2)
    assert row.model_calls == 3


async def test_a_dreamer_run_stores_its_absent_tag_as_absent(
    store: SqlReasoningStripStore, engine: Any
) -> None:
    """NULL, not "". A Dreamer run has no tag, and a row claiming an empty one
    is a row that joins to nothing while looking like it should — the same
    trap `participation_window_state.last_send_at` states one table over for a
    1970 instant."""
    await store.record_observation(
        _observation(path="dreamer:culture", tag_id=None, reasoning_effort="low")
    )
    (row,) = await _rows(engine)
    assert row.tag_id is None
    assert row.path == "dreamer:culture"


async def test_a_clean_multi_turn_run_is_stored_too(
    store: SqlReasoningStripStore, engine: Any
) -> None:
    """THE DENOMINATOR'S OWN ROW, and the reason the table is not
    strip-triggered. A run with opportunities and no strips is what makes a
    later zero mean «measured zero» rather than «nothing ran» — the same
    distinction the Prometheus counter buys by recording `clean` beside
    `stripped`, held here across restarts instead of only within one."""
    await store.record_observation(
        _observation(reasoning_effort="none", strip_calls=0, stripped_blocks=0)
    )
    (row,) = await _rows(engine)
    assert (row.strip_calls, row.continuation_calls) == (0, 2)
    assert row.reasoning_effort == "none", (
        "without this, a measured zero at `none` — where a strip is impossible "
        "— is indistinguishable from a measured zero at `low`, where it is news"
    )


async def test_two_runs_are_two_rows(
    store: SqlReasoningStripStore, engine: Any
) -> None:
    """Insert-only, no upsert, no key to collide on: each invocation is its own
    observation, and the ratio is a sum over rows."""
    await store.record_observation(_observation())
    await store.record_observation(
        _observation(created_at=T0 + timedelta(minutes=2), strip_calls=0,
                     stripped_blocks=0)
    )
    rows = await _rows(engine)
    assert len(rows) == 2
    assert sum(r.strip_calls for r in rows) == 2
    assert sum(r.continuation_calls for r in rows) == 4


async def test_the_database_refuses_a_row_with_no_opportunity(
    store: SqlReasoningStripStore,
) -> None:
    """`continuation_calls = 0` is a single-call turn, which has no denominator
    and must never reach the table. `ReasoningStripWatch.observation` returns
    `None` for it; this is the second lock, because the first one is a writer
    and a writer is what drifts."""
    with pytest.raises(IntegrityError):
        await store.record_observation(
            _observation(model_calls=1, continuation_calls=0, strip_calls=0,
                         stripped_blocks=0)
        )


async def test_the_database_refuses_more_strips_than_opportunities(
    store: SqlReasoningStripStore,
) -> None:
    """A row claiming more strips than continuation calls would mean the
    reasoning arrived from the PREFILL rather than from the loop — a shape no
    correct reading of this table can explain, and the one that must not be
    stored quietly while the ratio it corrupts is read off it."""
    with pytest.raises(IntegrityError):
        await store.record_observation(
            _observation(continuation_calls=1, strip_calls=2, stripped_blocks=2)
        )


async def test_the_database_refuses_strips_with_no_blocks(
    store: SqlReasoningStripStore,
) -> None:
    """`strip_calls` is how OFTEN and `stripped_blocks` is how MUCH. One being
    zero while the other is not is a writer that has drifted from the thing it
    is counting."""
    with pytest.raises(IntegrityError):
        await store.record_observation(_observation(stripped_blocks=0))


async def test_the_database_refuses_a_negative_numerator(
    store: SqlReasoningStripStore,
) -> None:
    """`sum(strip_calls) / sum(continuation_calls)` is the whole product of
    this table, and a negative numerator corrupts it silently — it does not
    look wrong in any single row, it just makes the total smaller. The
    constraint's own comment claims the counts are «refused at the database
    rather than trusted from the writer»; until this clause it trusted the
    writer on the sign."""
    with pytest.raises(IntegrityError):
        await store.record_observation(
            _observation(strip_calls=-1, stripped_blocks=1)
        )


async def test_the_database_refuses_an_empty_tag_id(
    store: SqlReasoningStripStore,
) -> None:
    """NULL is «this path has no tag»; "" is a writer that lost the tag and did
    not say so. Five places state NULL-not-empty-string, and until this clause
    the database accepted the empty one — a rule stated everywhere and enforced
    nowhere, which is the shape that rots."""
    with pytest.raises(IntegrityError):
        await store.record_observation(_observation(tag_id=""))


async def test_a_naive_timestamp_is_refused_before_it_reaches_the_database(
    store: SqlReasoningStripStore,
) -> None:
    """The column is `timestamptz`, so a naive value would be stored under
    whatever the session's timezone happens to be — a real instant, silently the
    wrong one, on the one column an operator uses to ask «did this move when
    the model did». Same rule, and the same local spelling of it, as
    `nora.domain.tool_call_log`."""
    with pytest.raises(NaiveReasoningStripDatetimeError):
        await store.record_observation(
            _observation(created_at=datetime(2026, 9, 17, 9, 42, 22))
        )


async def test_the_rate_is_computable_from_the_table_alone(
    store: SqlReasoningStripStore, engine: Any
) -> None:
    """THE QUESTION THE TABLE EXISTS FOR, asked the way `docs/runbook.md` says
    to ask it: strips per opportunity, grouped by path and by what was actually
    sent. A counter cannot answer this across a restart; this can, and the
    query is the assertion rather than a comment claiming one would work.
    """
    for _ in range(2):
        await store.record_observation(_observation())
    for _ in range(3):
        await store.record_observation(
            _observation(path="dreamer:culture", tag_id=None, strip_calls=0,
                         stripped_blocks=0)
        )
    stmt = (
        sa.select(
            REASONING_STRIP_LOG.c.path,
            REASONING_STRIP_LOG.c.model,
            REASONING_STRIP_LOG.c.reasoning_effort,
            sa.func.sum(REASONING_STRIP_LOG.c.strip_calls).label("strips"),
            sa.func.sum(REASONING_STRIP_LOG.c.continuation_calls).label("chances"),
        )
        .group_by(
            REASONING_STRIP_LOG.c.path,
            REASONING_STRIP_LOG.c.model,
            REASONING_STRIP_LOG.c.reasoning_effort,
        )
        .order_by(REASONING_STRIP_LOG.c.path)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    rate = {r.path: (int(r.strips), int(r.chances)) for r in rows}
    assert rate == {"dreamer:culture": (0, 6), "tag": (4, 4)}


async def test_stripped_blocks_separates_how_much_from_how_often(
    store: SqlReasoningStripStore, engine: Any
) -> None:
    """The column `docs/runbook.md` §6d reads beside the rate, and the only one
    that can answer «how much reasoning was lost».

    `strip_calls` counts REQUESTS the transport stripped; `stripped_blocks`
    counts the DISTINCT blocks lost. Two strips of one block (a short loop
    re-sending the same reasoning) and two strips of two blocks (a loop that
    reasoned twice and lost both) are different amounts of thinking thrown
    away, and until this query existed the column had no reader at all."""
    await store.record_observation(_observation(strip_calls=2, stripped_blocks=1))
    await store.record_observation(_observation(strip_calls=2, stripped_blocks=2))
    stmt = sa.select(
        sa.func.sum(REASONING_STRIP_LOG.c.strip_calls),
        sa.func.sum(REASONING_STRIP_LOG.c.stripped_blocks),
    )
    async with engine.connect() as conn:
        strips, blocks = (await conn.execute(stmt)).one()
    assert (int(strips), int(blocks)) == (4, 3), (
        "the two columns have collapsed into one number — `stripped_blocks` "
        "can no longer tell one block dropped twice from two blocks dropped"
    )
