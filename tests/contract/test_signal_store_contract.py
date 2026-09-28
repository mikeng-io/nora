"""Shared contract for the signal store (design/cognition-loop/20-observe.md §3).
Every implementation runs every test.

Two implementations: `nora.infra.sql.signals.SqlSignalStore` over real
Postgres, and `nora.testing.signals.InMemorySignalStore`. The evaluator's unit
tests run on the fake, so the fake must consume signals by exactly the rule
the SQL does, and this suite is what says it does.

**The property it is really about is the watermark.** A unit's watermark is
its newest wake row, never a row rewritten; a signal is consumed when its id
AND its pending-since time are both at or under that row's. The second bound
is what keeps a `self`/`time` signal due later pending across an earlier wake
(20 §3: «pending only from its due time»).

The database is built ONCE for this module and truncated between tests
(AGENTS.md, «A contract suite scopes its database to the MODULE»).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.signals import NewSignal, NewWake
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.signals import SqlSignalStore
from nora.testing.signals import InMemorySignalStore

NOW = 1_790_000_000_000
UNIT = "nora"


@pytest.fixture(scope="module")
def _signal_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_signal_" + uuid4().hex
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
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["memory", "sql"])
async def store(request: Any) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield InMemorySignalStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_signal_db"))
    try:
        yield SqlSignalStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("truncate signal, cognition_wake restart identity"))
        await engine.dispose()


def _msg(ref: str, *, unit: str = UNIT, cues: tuple[str, ...] = (), waits: bool = False,
         at: int = NOW) -> NewSignal:
    return NewSignal(
        unit=unit, kind="message", item="message", ref=ref, at_ms=at,
        platform="signal", conversation_id="g1", person_id="p1",
        cues=cues, waits=waits,
    )


def _wake(through: int, at: int, *, cut: bool = False) -> NewWake:
    return NewWake(
        unit=UNIT, mode="shadow", reason="settled", from_signal_id=0,
        through_signal_id=through, signal_count=1, eligible_count=1,
        oldest_pending_ms=NOW, cut_cooldown=cut, at_ms=at, detail={"k": "v"},
    )


async def test_an_appended_signal_is_pending_with_its_fields(store):
    sid = await store.append(signal=_msg("messages:1", cues=("datetime", "keep"), waits=True),
                             recorded_at_ms=NOW)
    view = await store.view(unit=UNIT, mode="shadow", now_ms=NOW, limit=100)
    assert [s.id for s in view.pending] == [sid]
    got = view.pending[0]
    assert (got.kind, got.item, got.ref, got.cues, got.waits, got.person_id) == (
        "message", "message", "messages:1", ("datetime", "keep"), True, "p1",
    )
    assert got.recorded_at_ms == NOW and got.due_ms is None
    assert view.last_activity_ms == NOW
    assert view.last_wake is None


async def test_ids_increase_and_pending_is_ascending(store):
    ids = [await store.append(signal=_msg(f"messages:{i}"), recorded_at_ms=NOW + i)
           for i in range(3)]
    assert ids == sorted(ids) and len(set(ids)) == 3
    view = await store.view(unit=UNIT, mode="shadow", now_ms=NOW + 10, limit=100)
    assert [s.id for s in view.pending] == ids


async def test_pending_is_scoped_to_the_unit(store):
    await store.append(signal=_msg("messages:1", unit="other"), recorded_at_ms=NOW)
    view = await store.view(unit=UNIT, mode="shadow", now_ms=NOW, limit=100)
    assert view.pending == () and view.last_activity_ms is None


async def test_a_wake_is_the_watermark_and_consumes_through_its_id(store):
    a = await store.append(signal=_msg("messages:1"), recorded_at_ms=NOW)
    b = await store.append(signal=_msg("messages:2"), recorded_at_ms=NOW + 1)
    await store.record_wake(wake=_wake(a, NOW + 5), recorded_at_ms=NOW + 5)
    view = await store.view(unit=UNIT, mode="shadow", now_ms=NOW + 10, limit=100)
    assert [s.id for s in view.pending] == [b]
    assert view.last_wake is not None
    assert (view.last_wake.through_signal_id, view.last_wake.at_ms) == (a, NOW + 5)
    assert view.last_wake.detail == {"k": "v"}


async def test_the_newest_wake_is_the_watermark(store):
    a = await store.append(signal=_msg("messages:1"), recorded_at_ms=NOW)
    b = await store.append(signal=_msg("messages:2"), recorded_at_ms=NOW + 1)
    await store.record_wake(wake=_wake(a, NOW + 5), recorded_at_ms=NOW + 5)
    await store.record_wake(wake=_wake(b, NOW + 9, cut=True), recorded_at_ms=NOW + 9)
    view = await store.view(unit=UNIT, mode="shadow", now_ms=NOW + 10, limit=100)
    assert view.pending == ()
    assert view.last_wake is not None and view.last_wake.cut_cooldown is True


async def test_a_signal_due_later_stays_pending_across_an_earlier_wake(store):
    """20 §3: a self-wake set for 19:40 does not count at 15:00, and a wake at
    15:00 does not consume it, though its id is under the watermark."""
    due = NOW + 60_000
    later = await store.append(
        signal=NewSignal(unit=UNIT, kind="self", item="self", ref="wake:1",
                         at_ms=NOW, due_ms=due),
        recorded_at_ms=NOW,
    )
    now_msg = await store.append(signal=_msg("messages:1"), recorded_at_ms=NOW + 1)
    early = await store.view(unit=UNIT, mode="shadow", now_ms=NOW + 10, limit=100)
    assert [s.id for s in early.pending] == [now_msg]          # not yet due
    await store.record_wake(wake=_wake(now_msg, NOW + 10), recorded_at_ms=NOW + 10)
    before_due = await store.view(unit=UNIT, mode="shadow", now_ms=due - 1, limit=100)
    assert before_due.pending == ()
    at_due = await store.view(unit=UNIT, mode="shadow", now_ms=due, limit=100)
    assert [s.id for s in at_due.pending] == [later]


async def test_a_time_signal_is_written_once_per_ref(store):
    sig = NewSignal(unit=UNIT, kind="time", item="floor", ref="floor:07:00@2026-09-28",
                    at_ms=NOW, due_ms=NOW)
    first = await store.append(signal=sig, recorded_at_ms=NOW)
    again = await store.append(signal=sig, recorded_at_ms=NOW + 30_000)
    other_unit = await store.append(
        signal=NewSignal(unit="other", kind="time", item="floor",
                         ref="floor:07:00@2026-09-28", at_ms=NOW, due_ms=NOW),
        recorded_at_ms=NOW)
    assert first is not None and again is None and other_unit is not None
    view = await store.view(unit=UNIT, mode="shadow", now_ms=NOW + 60_000, limit=100)
    assert [s.id for s in view.pending] == [first]


async def test_message_refs_are_not_deduplicated(store):
    """Only `time` is unique on its ref: two reactions that share a message
    are two Record items, and both are signals."""
    a = await store.append(signal=_msg("messages:1"), recorded_at_ms=NOW)
    b = await store.append(signal=_msg("messages:1"), recorded_at_ms=NOW)
    assert a is not None and b is not None and a != b


async def test_the_limit_bounds_the_pending_read(store):
    for i in range(5):
        await store.append(signal=_msg(f"messages:{i}"), recorded_at_ms=NOW)
    view = await store.view(unit=UNIT, mode="shadow", now_ms=NOW, limit=2)
    assert len(view.pending) == 2


async def test_last_activity_counts_message_signals_only(store):
    await store.append(signal=_msg("messages:1"), recorded_at_ms=NOW)
    await store.append(
        signal=NewSignal(unit=UNIT, kind="time", item="floor", ref="floor:x",
                         at_ms=NOW + 50, due_ms=NOW + 50),
        recorded_at_ms=NOW + 50,
    )
    view = await store.view(unit=UNIT, mode="shadow", now_ms=NOW + 100, limit=100)
    assert view.last_activity_ms == NOW


async def test_a_due_later_wake_does_not_resurrect_earlier_consumed_signals(store):
    """The watermark only moves forward, on both stores: a wake whose only
    pending signal is an OLD id that just became due (a self-wake appended at
    15:00, due at 19:40) records a watermark no lower than the one it read,
    so the signals an earlier wake consumed stay consumed. The through id is
    `decide`'s, exactly as the evaluator records it."""
    from nora.domain.signals import WakePolicy, decide

    policy = WakePolicy(quiet_seconds=0, cooldown_seconds=0, session_seconds=0)
    due = NOW + 60_000
    await store.append(signal=NewSignal(unit=UNIT, kind="self", item="self", ref="wake:1",
                                        at_ms=NOW, due_ms=due), recorded_at_ms=NOW)
    for i in (2, 3, 4):
        await store.append(signal=_msg(f"messages:{i}"), recorded_at_ms=NOW + i)
    for at in (NOW + 10, due + 10):
        view = await store.view(unit=UNIT, mode="shadow", now_ms=at, limit=100)
        verdict = decide(view, policy, at)
        assert verdict.reason is not None
        await store.record_wake(wake=NewWake(
            unit=UNIT, mode="shadow", reason=verdict.reason, from_signal_id=0,
            through_signal_id=verdict.through_signal_id,
            signal_count=verdict.signal_count, eligible_count=verdict.eligible_count,
            oldest_pending_ms=verdict.oldest_pending_ms, cut_cooldown=False, at_ms=at,
        ), recorded_at_ms=at)
    after = await store.view(unit=UNIT, mode="shadow", now_ms=due + 10**6, limit=100)
    assert after.pending == ()
    assert after.last_wake is not None and after.last_wake.through_signal_id >= 4


async def test_a_signal_recorded_after_the_wakes_time_bound_stays_pending_under_its_id(store):
    """A late commit: two concurrent ingests, the LOWER id stamped later.
    A wake that read through the higher id with its time bound between the
    two stamps must leave the later-stamped one pending — the evaluator's
    settle margin relies on exactly this."""
    late = await store.append(signal=_msg("messages:1"), recorded_at_ms=NOW + 100)
    early = await store.append(signal=_msg("messages:2"), recorded_at_ms=NOW)
    await store.record_wake(wake=_wake(early, NOW + 50), recorded_at_ms=NOW + 50)
    view = await store.view(unit=UNIT, mode="shadow", now_ms=NOW + 200, limit=100)
    assert [s.id for s in view.pending] == [late]
