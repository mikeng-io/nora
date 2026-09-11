"""Shared contract for the decision-log store. Every implementation runs every
test (issue #199).

`nora.infra.sql.decision_log.SqlDecisionLogStore` over real Postgres, and
`nora.testing.InMemoryDecisionLogStore` built from `nora.domain.decision_log`
alone. AGENTS.md's rule is why this exists at all — a port never gains a second
implementation, *including a test fake*, without a suite both pass.

**The properties this suite is really about:**

  * an append-only log ORDERS: rows come back oldest-first, and the LIMIT takes
    the NEWEST rows, not the first ones the scan reached. A store that answered
    the oldest 20 of a busy room would render a block about yesterday and read
    as working;
  * the window and the limit are BOTH enforced by the store, so a hung or
    chatty store cannot put an unbounded string in a prompt;
  * `recent` is scoped to ONE conversation and `recent_for_workspace` to one
    workspace — a leak either way puts another room's decisions in this room's
    prompt, which is the whole failure the block exists to fix, inverted;
  * the tie-break is deterministic, or the two implementations answer different
    rows for the same input and a contract suite cannot see it.

**And it goes AROUND the domain type where the database is the real guard**
(`test_turn_verdict_store_contract`'s lesson): `object.__setattr__` builds a row
with an over-long reason and an unknown writer, because the CHECKs exist for a
writer that got a value some other way and a fake without them is MORE
PERMISSIVE than Postgres.

Database and migrations built ONCE per module, table truncated between tests —
AGENTS.md's contract-suite rule; the isolation needed is "no rows from the last
test", not "no schema".
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, Protocol
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.decision_log import REASON_MAX_CHARS, DecisionRow
from nora.infra.sql.decision_log import SqlDecisionLogStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import InMemoryDecisionLogStore

NOW = 1_757_000_000_000
CONV = "g1"
WS = "friends"


class _DecisionStore(Protocol):
    async def append(self, *, row: DecisionRow) -> None: ...
    async def recent(
        self, *, conversation_id: str, since_ms: int, limit: int = ...,
    ) -> tuple[DecisionRow, ...]: ...
    async def recent_for_workspace(
        self, *, workspace: str, since_ms: int, limit: int = ...,
    ) -> tuple[DecisionRow, ...]: ...


@pytest.fixture(scope="module")
def _decision_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_declog_" + uuid4().hex
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
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["memory", "sql"])
async def store(request: Any) -> AsyncIterator[_DecisionStore]:
    if request.param == "memory":
        yield InMemoryDecisionLogStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_decision_db"))
    try:
        yield SqlDecisionLogStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("truncate decision_log cascade"))
        await engine.dispose()


def _row(
    row_id: str = "d1",
    *,
    writer: Any = "gate",
    verdict: Any = "no_go",
    conversation_id: str = CONV,
    workspace: str = WS,
    note_id: str | None = None,
    reason: str = "",
    ts: int = NOW,
    platform: str = "signal",
) -> DecisionRow:
    return DecisionRow(
        id=row_id, writer=writer, platform=platform,
        conversation_id=conversation_id, workspace=workspace, note_id=note_id,
        verdict=verdict, reason=reason, ts=ts,
    )


def _smuggle(row: DecisionRow, **fields: Any) -> DecisionRow:
    """A row built AROUND `__post_init__` — the only way to reach a CHECK."""
    for name, value in fields.items():
        object.__setattr__(row, name, value)
    return row


async def test_a_room_with_no_decisions_reads_as_empty(store):
    assert await store.recent(conversation_id=CONV, since_ms=0) == ()


async def test_an_appended_row_reads_back_whole(store):
    await store.append(row=_row(reason="quiet room"))
    assert await store.recent(conversation_id=CONV, since_ms=0) == (
        _row(reason="quiet room"),
    )


async def test_a_nullable_note_id_survives_the_round_trip(store):
    """`note_id` is the one nullable column, and NULL is the ordinary case —
    only the proactive decide turn decides about a note. A store that coerced
    it to `""` would make «about note X» and «about the room» the same row."""
    await store.append(row=_row("d1"))
    await store.append(row=_row("d2", writer="proactive_decide", verdict="remind",
                                note_id="n-7", ts=NOW + 1))
    rows = await store.recent(conversation_id=CONV, since_ms=0)
    assert [r.note_id for r in rows] == [None, "n-7"]


async def test_rows_come_back_oldest_first(store):
    """The block is read top-to-bottom beside a transcript that is also
    oldest-first. A store that answered newest-first would render the loop's
    history backwards, which is the failure `render_context` already made once
    on the gate's transcript."""
    await store.append(row=_row("d3", ts=NOW + 2000, reason="third"))
    await store.append(row=_row("d1", ts=NOW, reason="first"))
    await store.append(row=_row("d2", ts=NOW + 1000, reason="second"))
    rows = await store.recent(conversation_id=CONV, since_ms=0)
    assert [r.reason for r in rows] == ["first", "second", "third"]


async def test_the_limit_takes_the_NEWEST_rows(store):
    """**The property a `LIMIT` gets wrong by default.** Order oldest-first and
    limit, and a busy room answers its OLDEST rows — a block about yesterday,
    rendered as if it were the state of the room now, with nothing saying so.
    The store must take the newest N and then present them oldest-first.

    Falsification: drop the `desc()` from `SqlDecisionLogStore.recent`'s inner
    order (or the `[-limit:]` from the fake) and this goes red while every
    other test in this file stays green.
    """
    for i in range(6):
        await store.append(row=_row(f"d{i}", ts=NOW + i, reason=f"r{i}"))
    rows = await store.recent(conversation_id=CONV, since_ms=0, limit=3)
    assert [r.reason for r in rows] == ["r3", "r4", "r5"]


async def test_rows_older_than_the_window_are_not_answered(store):
    """`since_ms` is the store's bound, not the caller's filter. A store that
    ignored it would hand a 24h-framed block rows from last month."""
    await store.append(row=_row("old", ts=NOW - 10_000, reason="last month"))
    await store.append(row=_row("new", ts=NOW + 10_000, reason="today"))
    rows = await store.recent(conversation_id=CONV, since_ms=NOW)
    assert [r.reason for r in rows] == ["today"]


async def test_the_window_boundary_is_inclusive(store):
    """A row written exactly at the boundary is IN. The two implementations
    must agree on which side, or a row flickers in and out of the block
    depending on which store answered."""
    await store.append(row=_row("edge", ts=NOW, reason="on the line"))
    assert len(await store.recent(conversation_id=CONV, since_ms=NOW)) == 1


async def test_another_rooms_rows_are_never_answered(store):
    """The inverse of the whole feature: a leak here puts one room's decisions
    into another room's prompt."""
    await store.append(row=_row("mine", conversation_id=CONV, reason="here"))
    await store.append(row=_row("theirs", conversation_id="g2", reason="elsewhere"))
    rows = await store.recent(conversation_id=CONV, since_ms=0)
    assert [r.reason for r in rows] == ["here"]


async def test_a_workspace_read_spans_its_rooms_and_no_others(store):
    await store.append(row=_row("a", conversation_id="g1", workspace=WS, reason="a"))
    await store.append(row=_row("b", conversation_id="g2", workspace=WS,
                                ts=NOW + 1, reason="b"))
    await store.append(row=_row("c", conversation_id="g3", workspace="work",
                                ts=NOW + 2, reason="c"))
    rows = await store.recent_for_workspace(workspace=WS, since_ms=0)
    assert [r.reason for r in rows] == ["a", "b"]


async def test_a_workspace_read_takes_the_newest_and_honours_the_window(store):
    for i in range(6):
        await store.append(row=_row(f"w{i}", workspace=WS, ts=NOW + i, reason=f"r{i}"))
    await store.append(row=_row("stale", workspace=WS, ts=NOW - 5, reason="stale"))
    rows = await store.recent_for_workspace(workspace=WS, since_ms=NOW, limit=2)
    assert [r.reason for r in rows] == ["r4", "r5"]


async def test_rows_in_the_same_millisecond_come_back_in_ONE_order(store):
    """Two decisions in one millisecond are reachable — the tag turn one second
    after a participation answer is issue #199's own example, and one
    millisecond is the same shape. Without a deterministic tie-break Postgres
    answers whatever the scan reached first and the double answers hash order,
    so the two implementations render DIFFERENT blocks for identical rows and
    this suite cannot see it."""
    await store.append(row=_row("b", ts=NOW, reason="b"))
    await store.append(row=_row("a", ts=NOW, reason="a"))
    once = await store.recent(conversation_id=CONV, since_ms=0)
    twice = await store.recent(conversation_id=CONV, since_ms=0)
    assert [r.id for r in once] == [r.id for r in twice] == ["a", "b"]


async def test_a_second_row_with_the_same_id_is_refused(store):
    """`id` is the primary key. This is a LOG — every decision is its own row —
    so a duplicate id means a caller reused one, and silently replacing would
    lose a decision that happened."""
    await store.append(row=_row("d1"))
    with pytest.raises(Exception):  # noqa: B017 — IntegrityError vs DuplicateDecisionError
        await store.append(row=_row("d1", verdict="go"))


async def test_an_over_long_reason_is_refused_by_the_store_itself(store):
    """Reachable only AROUND `DecisionRow.__post_init__`, which is exactly why
    the CHECK exists: the cap is a prompt budget, and a writer that got its
    string some other way must not be able to put 4KB in a block the gate reads
    on every quiet window."""
    row = _smuggle(_row("d1"), reason="x" * (REASON_MAX_CHARS + 1))
    with pytest.raises(Exception):  # noqa: B017 — IntegrityError vs ValueError
        await store.append(row=row)


async def test_an_unknown_writer_is_refused_by_the_store_itself(store):
    """`writer` is a closed vocabulary and the CHECK is what closes it for a
    caller that bypassed the type. An open column would let a future writer
    land rows the two renders silently drop — a decision recorded, never read,
    and nothing saying so."""
    row = _smuggle(_row("d1"), writer="curator")
    with pytest.raises(Exception):  # noqa: B017
        await store.append(row=row)


async def test_an_unknown_verdict_is_refused_by_the_store_itself(store):
    row = _smuggle(_row("d1"), verdict="maybe")
    with pytest.raises(Exception):  # noqa: B017
        await store.append(row=row)
