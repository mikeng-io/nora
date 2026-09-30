"""Shared contract for what the wake policy reads about SESSIONS: how each
wake's session ended (`cognition_session_end`), what a closed session carried
forward (`cognition_carry`), a retracted `self` stimulus (`stimulus_retraction`),
and the self-wake state the ledger folds (review of #627, DEC-242).
Every implementation runs every test.

Two implementations of each store: SQL over real Postgres
(`SqlStimulusStore` + `SqlCognitionDryRunStore`), and the in-memory doubles,
which the wake evaluator's unit tests run on, so the double must decide the
watermark by exactly the rule the SQL does.

**The property it is about is the watermark.** A wake is the unit's watermark
unless its session FAILED (an end row that is not `closed`, or no end row past
the timeout): «a failed session does not advance it» (20 §3).

The database is built ONCE for this module and truncated between tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.ports import (
    NewCarry,
    NewCognitionPace,
    NewCognitionSession,
    NewCognitionSessionEnd,
)
from nora.domain.stimuli import NewStimulus, NewWake
from nora.infra.sql.cognition_dry_run import SqlCognitionDryRunStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_790_000_000_000
UNIT = "nora"
ALL_TABLES = (
    "stimulus, cognition_wake, cognition_carry, stimulus_retraction, "
    "cognition_session_end, cognition_session_close, cognition_self_wake, "
    "cognition_remind_mirror, cognition_would_write, cognition_session"
)


@pytest.fixture(scope="module")
def _lifecycle_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_session_lifecycle_" + uuid4().hex
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
async def stores(request: Any) -> AsyncIterator[tuple[Any, Any]]:
    if request.param == "memory":
        ledger = InMemoryCognitionDryRunStore()
        yield InMemoryStimulusStore(ledger=ledger), ledger
        return
    engine = create_sql_engine(request.getfixturevalue("_lifecycle_db"))
    try:
        yield SqlStimulusStore(engine), SqlCognitionDryRunStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text(f"truncate {ALL_TABLES} restart identity"))
        await engine.dispose()


async def _msgs(stimuli: Any, n: int) -> list[int]:
    return [
        await stimuli.append(stimulus=NewStimulus(
            unit=UNIT, kind="message", item="message", ref=f"messages:{i}", at_ms=NOW,
            platform="signal", conversation_id="g1",
        ), recorded_at_ms=NOW)
        for i in range(1, n + 1)
    ]


async def _wake(stimuli: Any, *, through: int, at: int = NOW) -> int:
    return await stimuli.record_wake(wake=NewWake(
        unit=UNIT, mode="shadow", reason="settled", from_stimulus_id=0,
        through_stimulus_id=through, stimulus_count=1, eligible_count=1,
        oldest_pending_ms=NOW, cut_cooldown=False, at_ms=at,
    ), recorded_at_ms=at)


async def _session(ledger: Any, wake_id: int, *, started: int = NOW) -> int:
    return await ledger.record_session(session=NewCognitionSession(
        unit=UNIT, mode="shadow", started_at_ms=started, from_stimulus_id=0,
        through_stimulus_id=1, situation={}, agenda={"items": []}, wake_id=wake_id,
    ), recorded_at_ms=started)


async def _end(ledger: Any, session_id: int, outcome: str, at: int = NOW + 500) -> int:
    return await ledger.record_session_end(
        end=NewCognitionSessionEnd(session_id=session_id, outcome=outcome, at_ms=at),
        recorded_at_ms=at,
    )


async def _view(stimuli: Any, **kw: Any) -> Any:
    return await stimuli.view(unit=UNIT, mode="shadow", now_ms=NOW + 10_000, limit=100, **kw)


# ---- the watermark ---------------------------------------------------------

async def test_a_closed_session_leaves_its_wake_as_the_watermark(stores):
    stimuli, ledger = stores
    await _msgs(stimuli, 3)
    wake = await _wake(stimuli, through=3)
    await _end(ledger, await _session(ledger, wake), "closed")
    view = await _view(stimuli)
    assert view.pending == ()
    assert view.mark.through_stimulus_id == 3
    assert (view.last_session.state, view.last_session.ended_at_ms) == ("closed", NOW + 500)


@pytest.mark.parametrize("outcome", ["failed", "timeout", "cancelled"])
async def test_a_session_that_did_not_close_does_not_advance_the_watermark(stores, outcome):
    stimuli, ledger = stores
    await _msgs(stimuli, 3)
    wake = await _wake(stimuli, through=3)
    await _end(ledger, await _session(ledger, wake), outcome)
    view = await _view(stimuli)
    assert [s.id for s in view.pending] == [1, 2, 3]
    assert view.mark.through_stimulus_id == 0
    assert view.last_wake is not None and view.last_wake.id == wake
    assert view.last_session.state == "failed"


async def test_a_failed_wake_falls_back_to_the_newest_wake_that_did_not_fail(stores):
    stimuli, ledger = stores
    await _msgs(stimuli, 3)
    good = await _wake(stimuli, through=1, at=NOW)
    await _end(ledger, await _session(ledger, good), "closed")
    bad = await _wake(stimuli, through=3, at=NOW + 100)
    await _end(ledger, await _session(ledger, bad, started=NOW + 100), "failed")
    view = await _view(stimuli)
    assert view.mark.through_stimulus_id == 1
    assert [s.id for s in view.pending] == [2, 3]
    assert view.last_wake.id == bad


async def test_a_wake_with_no_session_is_a_watermark(stores):
    """No hook (`session_mode = "off"`): the step-1 behaviour, unchanged."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 2)
    await _wake(stimuli, through=2)
    view = await _view(stimuli)
    assert view.pending == () and view.last_session is None


async def test_a_session_with_no_end_row_is_running_then_dead_past_the_timeout(stores):
    stimuli, ledger = stores
    await _msgs(stimuli, 2)
    wake = await _wake(stimuli, through=2)
    await _session(ledger, wake, started=NOW)
    running = await _view(stimuli, stale_before_ms=NOW - 1)
    assert running.last_session.state == "running" and running.last_session.ended_at_ms is None
    assert running.pending == ()                 # claimed while it runs
    dead = await _view(stimuli, stale_before_ms=NOW + 1)
    assert dead.last_session.state == "failed"
    assert [s.id for s in dead.pending] == [1, 2]
    unbounded = await _view(stimuli)             # no clock given: never expires
    assert unbounded.last_session.state == "running"


async def test_one_end_per_session_and_the_first_stands(stores):
    stimuli, ledger = stores
    await _msgs(stimuli, 1)
    session = await _session(ledger, await _wake(stimuli, through=1))
    first = await _end(ledger, session, "cancelled")
    second = await _end(ledger, session, "closed")
    assert first == second
    assert (await _view(stimuli)).last_session.state == "failed"


# ---- carry -----------------------------------------------------------------

async def test_a_carried_stimulus_is_pending_again_with_its_strike(stores):
    stimuli, ledger = stores
    await _msgs(stimuli, 2)
    await _end(ledger, await _session(ledger, await _wake(stimuli, through=2)), "closed")
    assert (await _view(stimuli)).pending == ()
    await stimuli.record_carry(
        carry=NewCarry(unit=UNIT, stimulus_id=1, strike=1, state="carried", waits=True),
        recorded_at_ms=NOW,
    )
    await stimuli.record_carry(
        carry=NewCarry(unit=UNIT, stimulus_id=2, strike=2, state="carried", waits=False),
        recorded_at_ms=NOW,
    )
    view = await _view(stimuli)
    assert [(s.id, s.waits) for s in view.pending] == [(1, True), (2, False)]
    assert dict(view.carry) == {1: 1, 2: 2}


async def test_the_newest_carry_row_decides_and_released_or_struck_is_not_carried(stores):
    stimuli, ledger = stores
    await _msgs(stimuli, 3)
    await _end(ledger, await _session(ledger, await _wake(stimuli, through=3)), "closed")
    for sid, states in ((1, ("carried", "released")), (2, ("carried", "struck")),
                        (3, ("released", "carried"))):
        for n, state in enumerate(states, start=1):
            await stimuli.record_carry(
                carry=NewCarry(unit=UNIT, stimulus_id=sid, strike=n, state=state),
                recorded_at_ms=NOW,
            )
    view = await _view(stimuli)
    assert [s.id for s in view.pending] == [3]
    assert dict(view.carry) == {3: 2}


async def test_a_carried_stimulus_that_is_also_pending_appears_once(stores):
    stimuli, _ledger = stores
    await _msgs(stimuli, 1)
    await stimuli.record_carry(
        carry=NewCarry(unit=UNIT, stimulus_id=1, strike=1, state="carried"), recorded_at_ms=NOW,
    )
    view = await _view(stimuli)
    assert [s.id for s in view.pending] == [1]


# ---- retraction ------------------------------------------------------------

async def test_a_retracted_stimulus_is_never_pending_and_retracting_twice_is_a_no_op(stores):
    stimuli, _ledger = stores
    sid = await stimuli.append(stimulus=NewStimulus(
        unit=UNIT, kind="self", item="w1", ref="self:w1", at_ms=NOW, due_ms=NOW + 100,
    ), recorded_at_ms=NOW)
    assert [s.id for s in (await _view(stimuli)).pending] == [sid]
    assert await stimuli.retract(unit=UNIT, stimulus_id=sid, reason="cancelled",
                                 recorded_at_ms=NOW) is True
    assert await stimuli.retract(unit=UNIT, stimulus_id=sid, reason="cancelled",
                                 recorded_at_ms=NOW) is False
    assert (await _view(stimuli)).pending == ()


# ---- the ledger's self-wake state -----------------------------------------

def _pace(**kw: Any) -> NewCognitionPace:
    base: dict[str, Any] = {
        "session_id": None, "unit": UNIT, "verb": "wake_at", "wake_ref": "w1",
        "transition": "created", "subject_rows": ("event:1",), "why": "w",
        "at_ms": NOW + 100, "outcome": "recorded", "stimulus_id": 1,
    }
    base.update(kw)
    return NewCognitionPace(**base)


async def test_pending_self_wakes_folds_transitions_and_a_daily_count_reads_created_rows(stores):
    _stimuli, ledger = stores
    for pace, at in (
        (_pace(), NOW),
        (_pace(wake_ref="w2", subject_rows=("event:2",), stimulus_id=2), NOW + 1),
        (_pace(wake_ref="w3", subject_rows=("event:3",), stimulus_id=3), NOW + 2),
        (_pace(verb="wake_cancel", transition="cancelled", wake_ref="w2", stimulus_id=None,
               at_ms=None), NOW + 3),
        (_pace(verb="wake_fire", transition="fired", wake_ref="w3", stimulus_id=None,
               at_ms=None), NOW + 4),
        (_pace(wake_ref=None, outcome="refused", refusal_reason="daily_cap",
               stimulus_id=None, at_ms=None), NOW + 5),
    ):
        await ledger.record_pace(pace=pace, recorded_at_ms=at)
    pending = await ledger.pending_self_wakes(unit=UNIT)
    assert [(p.wake_ref, p.stimulus_id, p.at_ms) for p in pending] == [("w1", 1, NOW + 100)]
    assert await ledger.count_self_wakes_created(unit=UNIT, since_ms=NOW) == 3
    assert await ledger.count_self_wakes_created(unit=UNIT, since_ms=NOW + 1) == 2
    assert await ledger.count_self_wakes_created(unit="other", since_ms=NOW) == 0
