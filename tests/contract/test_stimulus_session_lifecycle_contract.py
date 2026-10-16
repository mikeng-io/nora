"""Shared contract for what the wake policy reads about a wake's FATE: its one
outcome (`cognition_wake_outcome`), what a closed wake carried forward
(`cognition_carry`, written in the same transaction), a retracted `self`
stimulus (`stimulus_retraction`, one predicate for every read), the guarded
wake write, and the self-wake state the ledger folds (DEC-242). Every
implementation runs every test.

Two implementations of each store: SQL over real Postgres
(`SqlStimulusStore` + `SqlCognitionStore`), and the in-memory doubles,
which the wake evaluator's unit tests run on, so the double must decide the
watermark by exactly the rule the SQL does.

**The property it is about is the watermark.** A wake is the unit's watermark
unless it FAILED (an outcome that is not `closed`) or EXPIRED (tracked, no
outcome, past the timeout): «a failed session does not advance it».
`nora.domain.stimuli`'s module docstring is the state machine.

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
    NewWakeOutcome,
)
from nora.domain.stimuli import NewStimulus, NewWake, WakeGuard
from nora.infra.sql.cognition_store import SqlCognitionStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_790_000_000_000
UNIT = "nora"
ALL_TABLES = (
    "stimulus, cognition_wake, cognition_carry, stimulus_retraction, "
    "cognition_wake_outcome, cognition_session_close, cognition_self_wake, "
    "cognition_session"
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
        ledger = InMemoryCognitionStore()
        yield InMemoryStimulusStore(ledger=ledger), ledger
        return
    engine = create_sql_engine(request.getfixturevalue("_lifecycle_db"))
    try:
        yield SqlStimulusStore(engine), SqlCognitionStore(engine)
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


async def _guard_now(stimuli: Any) -> WakeGuard:
    """What a tick that just read the unit holds: its newest wake and that
    wake's outcome. A tracked wake is refused without a guard."""
    view = await stimuli.view(unit=UNIT, mode="live", now_ms=NOW + 10_000, limit=1)
    last = view.last_wake
    return WakeGuard(
        last_wake_id=None if last is None else last.id,
        last_outcome=None if view.last_session is None else view.last_session.outcome,
    )


async def _wake(
    stimuli: Any, *, through: int, at: int = NOW, tracked: bool = True, guard: Any = None,
) -> Any:
    if tracked and guard is None:
        guard = await _guard_now(stimuli)
    return await stimuli.record_wake(wake=NewWake(
        unit=UNIT, mode="live", reason="settled", from_stimulus_id=0,
        through_stimulus_id=through, stimulus_count=1, eligible_count=1,
        oldest_pending_ms=NOW, cut_cooldown=False, at_ms=at, tracked=tracked,
    ), recorded_at_ms=at, guard=guard)


async def _close(
    stimuli: Any, wake_id: int, outcome: str = "closed", *, carries: Any = (),
    at: int = NOW + 500, cause: str = "infra",
) -> Any:
    return await stimuli.record_outcome(
        outcome=NewWakeOutcome(
            wake_id=wake_id, unit=UNIT, outcome=outcome, at_ms=at, cause=cause,
        ),
        carries=tuple(carries), recorded_at_ms=at,
    )


def _carry(sid: int, strike: int = 1, state: str = "carried", waits: bool = False) -> NewCarry:
    return NewCarry(unit=UNIT, stimulus_id=sid, strike=strike, state=state, waits=waits)


async def _view(stimuli: Any, **kw: Any) -> Any:
    return await stimuli.view(unit=UNIT, mode="live", now_ms=NOW + 10_000, limit=100, **kw)


async def _self(stimuli: Any, ref: str, *, due: int = NOW + 100) -> int:
    return await stimuli.append(stimulus=NewStimulus(
        unit=UNIT, kind="self", item=ref, ref=f"self:{ref}:{due}", at_ms=NOW, due_ms=due,
    ), recorded_at_ms=NOW)


# ---- the watermark ---------------------------------------------------------

async def test_a_closed_wake_is_the_watermark(stores):
    stimuli, _ledger = stores
    await _msgs(stimuli, 3)
    wake = await _wake(stimuli, through=3)
    recorded = await _close(stimuli, wake)
    assert (recorded.outcome, recorded.written) == ("closed", True)
    view = await _view(stimuli, stale_before_ms=NOW + 1)
    assert view.pending == ()
    assert view.mark.through_stimulus_id == 3
    last = view.last_session
    assert (last.state, last.ended_at_ms, last.outcome) == ("closed", NOW + 500, "closed")


@pytest.mark.parametrize("outcome", ["failed", "timeout", "cancelled"])
async def test_a_wake_that_did_not_close_does_not_advance_the_watermark(stores, outcome):
    stimuli, _ledger = stores
    await _msgs(stimuli, 3)
    wake = await _wake(stimuli, through=3)
    await _close(stimuli, wake, outcome)
    view = await _view(stimuli)
    assert [s.id for s in view.pending] == [1, 2, 3]
    assert view.mark.through_stimulus_id == 0
    assert view.last_wake is not None and view.last_wake.id == wake
    assert (view.last_session.state, view.last_session.outcome) == ("failed", outcome)


async def test_a_failed_wake_falls_back_to_the_newest_wake_that_did_not_fail(stores):
    stimuli, _ledger = stores
    await _msgs(stimuli, 3)
    good = await _wake(stimuli, through=1, at=NOW)
    await _close(stimuli, good)
    bad = await _wake(stimuli, through=3, at=NOW + 100)
    await _close(stimuli, bad, "failed")
    view = await _view(stimuli)
    assert view.mark.through_stimulus_id == 1
    assert [s.id for s in view.pending] == [2, 3]
    assert view.last_wake.id == bad


async def test_consecutive_failures_and_the_mark_of_the_newest_failed_wake(stores):
    """What the backoff and the strike read. Failed and
    timed-out wakes count, a shutdown (`cancelled`) neither counts nor ends the
    run, an EXPIRED wake counts, and one `closed` wake resets it: the same rule
    in both stores, `nora.domain.stimuli.failure_run`."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 6)
    assert (await _view(stimuli, stale_before_ms=NOW)).failures == 0
    good = await _wake(stimuli, through=1, at=NOW)
    await _close(stimuli, good)
    view = await _view(stimuli, stale_before_ms=NOW)
    assert (view.failures, view.agenda_failures, view.failed_mark) == (0, 0, None)
    a = await _wake(stimuli, through=2, at=NOW + 100)
    await _close(stimuli, a, "failed", cause="agenda")
    b = await _wake(stimuli, through=3, at=NOW + 200)
    await _close(stimuli, b, "cancelled")
    c = await _wake(stimuli, through=4, at=NOW + 300)
    await _close(stimuli, c, "timeout", cause="agenda")
    view = await _view(stimuli, stale_before_ms=NOW)
    assert view.failures == 2                                  # a and c, b is transparent
    assert view.agenda_failures == 2
    assert (view.failed_mark.through_stimulus_id, view.failed_mark.through_ms) == (4, NOW + 300)
    # a wake that never resolved and is past the timeout is a failure too, and
    # an INFRASTRUCTURE one (a kill, a crash): it backs off, it is never struck
    await _wake(stimuli, through=5, at=NOW + 400)
    stale = await _view(stimuli, stale_before_ms=NOW + 5000)
    assert stale.failures == 3
    assert stale.agenda_failures == 2
    assert stale.failed_mark.through_stimulus_id == 4
    # ... and while it is still OPEN it neither counts nor ends the run
    running = await _view(stimuli, stale_before_ms=NOW)
    assert running.failures == 2
    # one closed wake resets the run
    await _close(stimuli, await _open_wake_id(stimuli), "closed")
    view = await _view(stimuli, stale_before_ms=NOW)
    assert (view.failures, view.agenda_failures, view.failed_mark) == (0, 0, None)
    # a reader that tracks no sessions sees none
    assert (await _view(stimuli)).failures == 0


async def test_a_failed_outcome_stores_its_cause_and_only_agenda_counts_toward_a_strike(stores):
    """DEC-244: the cause is written with the outcome and
    read back by the same rule in both stores. `infra` is the default and the
    reading of anything unrecorded; an unknown cause is refused."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 4)
    infra = await _wake(stimuli, through=2, at=NOW)
    await _close(stimuli, infra, "failed")                       # cause defaults to infra
    view = await _view(stimuli, stale_before_ms=NOW)
    assert (view.failures, view.agenda_failures, view.failed_mark) == (1, 0, None)
    agenda = await _wake(stimuli, through=3, at=NOW + 100)
    await _close(stimuli, agenda, "failed", cause="agenda")
    view = await _view(stimuli, stale_before_ms=NOW)
    assert (view.failures, view.agenda_failures) == (2, 1)
    assert view.failed_mark.through_stimulus_id == 3
    later_infra = await _wake(stimuli, through=4, at=NOW + 200)
    await _close(stimuli, later_infra, "timeout", cause="infra")
    view = await _view(stimuli, stale_before_ms=NOW)
    assert (view.failures, view.agenda_failures) == (3, 1)       # infra is transparent
    assert view.failed_mark.through_stimulus_id == 3             # ... and never the mark
    with pytest.raises(ValueError, match="failure cause"):
        await _close(stimuli, await _wake(stimuli, through=4, at=NOW + 300), "failed",
                     cause="weather")


async def _open_wake_id(stimuli: Any) -> int:
    view = await _view(stimuli, stale_before_ms=NOW)
    return int(view.last_wake.id)


async def test_an_untracked_wake_is_a_watermark_with_or_without_a_clock(stores):
    """No hook (`[cognition] enabled = false`): wakes are untracked, and an
    untracked wake never expires even when the reader tracks sessions."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 2)
    await _wake(stimuli, through=2, tracked=False)
    for kw in ({}, {"stale_before_ms": NOW + 10**9}):
        view = await _view(stimuli, **kw)
        assert view.pending == () and view.last_session is None
        assert view.last_wake.tracked is False
    # Under a newer tracked wake that failed, the mark falls back to the
    # untracked one, which a stale clock does not expire either.
    await _msgs(stimuli, 1)
    bad = await _wake(stimuli, through=3, at=NOW + 100)
    await _close(stimuli, bad, "failed")
    view = await _view(stimuli, stale_before_ms=NOW + 10**9)
    assert view.mark.through_stimulus_id == 2
    assert [s.id for s in view.pending] == [3]


async def test_a_tracked_wake_with_no_outcome_is_open_then_expired_past_the_timeout(stores):
    """A wake whose session row was never written (an exit before it, a
    crash) is OPEN while young — in flight, its range claimed, so no later wake
    can take it — and EXPIRED, read as failed, past the timeout."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 2)
    wake = await _wake(stimuli, through=2, at=NOW)
    running = await _view(stimuli, stale_before_ms=NOW - 1)
    assert running.last_session.state == "running"
    assert (running.last_session.started_at_ms, running.last_session.outcome) == (NOW, None)
    assert running.pending == ()                 # claimed while it is open
    assert running.mark.through_stimulus_id == 2
    dead = await _view(stimuli, stale_before_ms=NOW + 1)
    assert dead.last_session.state == "failed" and dead.last_session.outcome is None
    assert [s.id for s in dead.pending] == [1, 2]
    assert dead.mark.through_stimulus_id == 0
    assert dead.last_wake.id == wake
    untracking = await _view(stimuli)            # a reader that tracks nothing
    assert untracking.last_session is None and untracking.pending == ()


async def test_an_expired_wake_under_a_closed_one_is_skipped_by_the_mark(stores):
    """The mark query (not the newest-wake shortcut) applies the expiry too."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 3)
    first = await _wake(stimuli, through=1, at=NOW)
    await _close(stimuli, first)
    await _wake(stimuli, through=2, at=NOW + 10)              # never resolved
    third = await _wake(stimuli, through=3, at=NOW + 20)
    await _close(stimuli, third, "failed")
    fresh = await _view(stimuli, stale_before_ms=NOW)
    assert fresh.mark.through_stimulus_id == 2                 # the open one holds
    stale = await _view(stimuli, stale_before_ms=NOW + 15)
    assert stale.mark.through_stimulus_id == 1
    assert [s.id for s in stale.pending] == [2, 3]


async def test_one_outcome_per_wake_and_the_first_stands(stores):
    stimuli, _ledger = stores
    await _msgs(stimuli, 1)
    wake = await _wake(stimuli, through=1)
    first = await _close(stimuli, wake, "cancelled")
    second = await _close(stimuli, wake, "closed", carries=[_carry(1)])
    assert (first.outcome, first.written) == ("cancelled", True)
    assert (second.outcome, second.written) == ("cancelled", False)
    view = await _view(stimuli)
    assert view.last_session.state == "failed"
    assert dict(view.carry) == {}                # the refused close wrote no carry


async def test_a_close_for_a_wake_a_later_wake_superseded_is_timeout_with_no_carry(stores):
    """A session whose wake EXPIRED and whose stimuli a later wake
    took must not close over them (that is the same stimuli processed twice,
    with doubled strikes). The store records it `timeout` and writes none of
    its carries."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 2)
    old = await _wake(stimuli, through=2, at=NOW)
    newer = await _wake(stimuli, through=2, at=NOW + 1000)
    late = await _close(stimuli, old, "closed", carries=[_carry(1), _carry(2)])
    assert (late.outcome, late.written) == ("timeout", True)
    await _close(stimuli, newer, "closed")
    view = await _view(stimuli)
    assert dict(view.carry) == {} and view.pending == ()
    # CONTROL: a close of the NEWEST wake is a close.
    assert (await _close(stimuli, await _wake(stimuli, through=2, at=NOW + 2000))).outcome == (
        "closed"
    )


# ---- carry: written with the close, counted only for a closed wake --------

async def test_carries_ride_only_a_closed_outcome(stores):
    stimuli, _ledger = stores
    await _msgs(stimuli, 1)
    wake = await _wake(stimuli, through=1)
    with pytest.raises(ValueError):
        await _close(stimuli, wake, "failed", carries=[_carry(1)])
    with pytest.raises(ValueError):
        await _close(stimuli, wake, "closed", carries=[
            NewCarry(unit="elsewhere", stimulus_id=1, strike=1, state="carried"),
        ])
    view = await _view(stimuli, stale_before_ms=NOW - 1)
    assert view.last_session.state == "running"   # neither refusal wrote an outcome


async def test_a_close_that_fails_part_way_writes_nothing(stores):
    """The outcome and every carry are one write. A carry the table
    refuses (a state outside `ck_cognition_carry_state`) after good ones leaves
    NO outcome and NO carry — not a closed wake with half its carries."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 3)
    wake = await _wake(stimuli, through=3)
    with pytest.raises(Exception):  # noqa: B017 -- the SQL CHECK or the double's refusal
        await _close(stimuli, wake, "closed", carries=[
            _carry(1), _carry(2), _carry(3, state="bogus"),
        ])
    view = await _view(stimuli, stale_before_ms=NOW - 1)
    assert view.last_session.state == "running" and view.last_session.outcome is None
    assert dict(view.carry) == {}
    # The wake can still be resolved, once.
    assert (await _close(stimuli, wake, "failed")).written is True


async def test_a_carried_stimulus_is_pending_again_with_its_strike(stores):
    stimuli, _ledger = stores
    await _msgs(stimuli, 2)
    wake = await _wake(stimuli, through=2)
    await _close(stimuli, wake, carries=[_carry(1, 1, waits=True), _carry(2, 2)])
    view = await _view(stimuli)
    assert [(s.id, s.waits) for s in view.pending] == [(1, True), (2, False)]
    assert dict(view.carry) == {1: 1, 2: 2}


async def test_the_newest_carry_row_decides_and_released_or_struck_is_not_carried(stores):
    stimuli, _ledger = stores
    await _msgs(stimuli, 3)
    first = await _wake(stimuli, through=3, at=NOW)
    await _close(stimuli, first, carries=[
        _carry(1, 1, "carried"), _carry(2, 1, "carried"), _carry(3, 1, "released"),
    ])
    second = await _wake(stimuli, through=3, at=NOW + 100)
    await _close(stimuli, second, carries=[
        _carry(1, 2, "released"), _carry(2, 2, "struck"), _carry(3, 2, "carried"),
    ])
    view = await _view(stimuli)
    assert [s.id for s in view.pending] == [3]
    assert dict(view.carry) == {3: 2}


async def test_a_carried_stimulus_that_is_also_pending_appears_once(stores):
    stimuli, _ledger = stores
    await _msgs(stimuli, 1)
    wake = await _wake(stimuli, through=0)
    await _close(stimuli, wake, carries=[_carry(1)])
    view = await _view(stimuli)
    assert [s.id for s in view.pending] == [1]


# ---- retraction: one predicate, every read ---------------------------------

async def test_a_retracted_stimulus_is_never_pending_and_retracting_twice_is_a_no_op(stores):
    stimuli, _ledger = stores
    sid = await _self(stimuli, "w1")
    assert [s.id for s in (await _view(stimuli)).pending] == [sid]
    assert await stimuli.retract(unit=UNIT, stimulus_id=sid, reason="cancelled",
                                 recorded_at_ms=NOW) is True
    assert await stimuli.retract(unit=UNIT, stimulus_id=sid, reason="cancelled",
                                 recorded_at_ms=NOW) is False
    assert (await _view(stimuli)).pending == ()


async def test_a_retracted_stimulus_never_returns_through_carry(stores):
    """A self-wake a closed session carried (deferred, or left open) and
    that was then cancelled or moved must not come back through the carry side
    door: the carry read applies the same predicate the pending read does."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 1)
    sid = await _self(stimuli, "w1")
    wake = await _wake(stimuli, through=sid, at=NOW + 200)
    await _close(stimuli, wake, carries=[_carry(sid), _carry(1)])
    assert {s.id for s in (await _view(stimuli)).pending} == {1, sid}   # control
    await stimuli.retract(unit=UNIT, stimulus_id=sid, reason="cancelled", recorded_at_ms=NOW)
    view = await _view(stimuli)
    assert [s.id for s in view.pending] == [1]
    assert dict(view.carry) == {1: 1}


async def test_retract_self_takes_back_every_stimulus_standing_for_a_ref_but_the_kept_one(stores):
    stimuli, _ledger = stores
    old = await _self(stimuli, "w1", due=NOW + 100)
    straggler = await _self(stimuli, "w1", due=NOW + 200)
    new = await _self(stimuli, "w1", due=NOW + 300)
    other = await _self(stimuli, "w2", due=NOW + 100)
    assert await stimuli.retract_self(
        unit=UNIT, wake_ref="w1", keep=new, reason="moved", recorded_at_ms=NOW,
    ) == 2
    assert [s.id for s in (await _view(stimuli)).pending] == [new, other]
    assert {old, straggler}.isdisjoint(s.id for s in (await _view(stimuli)).pending)
    assert await stimuli.retract_self(
        unit=UNIT, wake_ref="w1", keep=None, reason="cancelled", recorded_at_ms=NOW,
    ) == 1
    assert [s.id for s in (await _view(stimuli)).pending] == [other]


# ---- the guarded wake write ------------------------------------------------

async def test_a_guarded_wake_is_written_only_while_the_view_it_came_from_holds(stores):
    """The evaluator decides from a view and writes later. If the unit's newest
    wake changed, or its outcome did (a wake read as EXPIRED closing late), the
    decision is stale: nothing is written."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 2)
    assert await _wake(stimuli, through=1, guard=WakeGuard(5, None)) is None
    first = await _wake(stimuli, through=1, guard=WakeGuard(None, None))
    assert first is not None
    # A second writer that also read «no wake yet» is refused.
    assert await _wake(stimuli, through=2, guard=WakeGuard(None, None)) is None
    # The view saw `first` with no outcome; it closed since.
    await _close(stimuli, first)
    assert await _wake(stimuli, through=2, guard=WakeGuard(first, None)) is None
    second = await _wake(stimuli, through=2, guard=WakeGuard(first, "closed"))
    assert second is not None and second != first
    # Unguarded (no hook): written as before.
    assert await _wake(stimuli, through=2, tracked=False) is not None


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
    pending = await ledger.pending_self_wakes(unit=UNIT, since_ms=NOW)
    assert [(p.wake_ref, p.stimulus_id, p.at_ms) for p in pending] == [("w1", 1, NOW + 100)]
    assert await ledger.count_self_wakes_created(unit=UNIT, since_ms=NOW) == 3
    assert await ledger.count_self_wakes_created(unit=UNIT, since_ms=NOW + 1) == 2
    assert await ledger.count_self_wakes_created(unit="other", since_ms=NOW) == 0


async def test_a_self_wake_whose_stimulus_is_retracted_is_not_pending(stores):
    """The ledger reads the same retraction predicate: a cancel whose ledger
    row failed after its retraction leaves a row that reads «pending», and it
    is not."""
    stimuli, ledger = stores
    sid = await _self(stimuli, "w1")
    other = await _self(stimuli, "w2")
    await ledger.record_pace(pace=_pace(stimulus_id=sid), recorded_at_ms=NOW)
    await ledger.record_pace(
        pace=_pace(wake_ref="w2", subject_rows=("event:2",), stimulus_id=other),
        recorded_at_ms=NOW,
    )
    await stimuli.retract(unit=UNIT, stimulus_id=sid, reason="cancelled", recorded_at_ms=NOW)
    pending = await ledger.pending_self_wakes(unit=UNIT, since_ms=NOW)
    assert [p.wake_ref for p in pending] == ["w2"]


async def test_pending_self_wakes_reads_a_bounded_window_by_ref(stores):
    """Only refs with a transition since `since_ms` are read. A ref
    set long ago and never touched is out; one set long ago and MOVED inside
    the window is in, at its moved time (its whole chain is read)."""
    _stimuli, ledger = stores
    day = 24 * 3600 * 1000
    await ledger.record_pace(pace=_pace(wake_ref="old", stimulus_id=1), recorded_at_ms=NOW)
    await ledger.record_pace(
        pace=_pace(wake_ref="kept", subject_rows=("event:2",), stimulus_id=2),
        recorded_at_ms=NOW,
    )
    await ledger.record_pace(
        pace=_pace(verb="wake_move", transition="moved", wake_ref="kept", stimulus_id=3,
                   at_ms=NOW + 9 * day), recorded_at_ms=NOW + 8 * day,
    )
    pending = await ledger.pending_self_wakes(unit=UNIT, since_ms=NOW + 7 * day)
    assert [(p.wake_ref, p.stimulus_id, p.subject_rows) for p in pending] == [
        ("kept", 3, ("event:2",)),
    ]
    everything = await ledger.pending_self_wakes(unit=UNIT, since_ms=NOW)
    assert [p.wake_ref for p in everything] == ["old", "kept"]


# ---- what a write is refused for ------------------------------------------

async def test_an_outcome_for_a_wake_nobody_wrote_is_refused(stores):
    """An outcome keyed by a wake that does not exist is a row no reader
    finds; both stores refuse it and write nothing."""
    stimuli, _ledger = stores
    with pytest.raises(ValueError, match="no cognition wake 999"):
        await _close(stimuli, 999)
    await _msgs(stimuli, 1)
    wake = await _wake(stimuli, through=1)
    view = await _view(stimuli, stale_before_ms=NOW + 1)
    assert view.last_session.outcome is None and view.last_wake.id == wake


async def test_an_outcome_from_another_units_session_is_refused(stores):
    """The outcome's unit is the wake's unit. A `closed` written under
    another unit's name would take that unit's lock and advance nothing."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 1)
    wake = await _wake(stimuli, through=1)
    with pytest.raises(ValueError, match="belongs to unit"):
        await stimuli.record_outcome(
            outcome=NewWakeOutcome(
                wake_id=wake, unit="someone-else", outcome="closed", at_ms=NOW + 500,
            ),
            carries=(), recorded_at_ms=NOW + 500,
        )
    recorded = await _close(stimuli, wake)
    assert (recorded.outcome, recorded.written) == ("closed", True)


async def test_a_tracked_wake_without_a_guard_is_refused_and_an_untracked_one_is_not(stores):
    """A tracked wake is the one whose fate a session settles, so it is
    written under the unit's lock or not at all; an untracked wake (no hook)
    has nothing to race and needs none."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 2)
    new = NewWake(
        unit=UNIT, mode="live", reason="settled", from_stimulus_id=0,
        through_stimulus_id=2, stimulus_count=2, eligible_count=2,
        oldest_pending_ms=NOW, cut_cooldown=False, at_ms=NOW, tracked=True,
    )
    with pytest.raises(ValueError, match="WakeGuard"):
        await stimuli.record_wake(wake=new, recorded_at_ms=NOW)
    assert (await _view(stimuli)).last_wake is None
    assert await _wake(stimuli, through=2, tracked=False) is not None


# ---- the per-unit lock, against real Postgres only --------------------------
#
# A double cannot hold a lock, and the lock is the whole reason a guarded wake
# and a close cannot both commit (a guard reading «no outcome» while a close
# reads «no later wake»). So these two run on the SQL store alone.

@pytest.fixture
async def sql_stores(request: Any) -> AsyncIterator[tuple[Any, Any, Any]]:
    engine = create_sql_engine(request.getfixturevalue("_lifecycle_db"))
    try:
        yield SqlStimulusStore(engine), SqlCognitionStore(engine), engine
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text(f"truncate {ALL_TABLES} restart identity"))
        await engine.dispose()


async def test_a_wake_write_and_a_close_wait_for_the_units_lock(sql_stores):
    """The deterministic half: with the unit's advisory lock held by
    another transaction, neither the guarded wake write nor the close may
    finish; both finish once it is released. A no-op lock lets them through
    while it is held. The lock is taken with the store's own key spelling
    (`_LOCK_CLASS`, `hashtext(unit)`), so a lock keyed on anything else does
    not block them either."""
    import asyncio

    from nora.infra.sql.stimuli import _LOCK_CLASS

    stimuli, _ledger, engine = sql_stores
    await _msgs(stimuli, 2)
    first = await _wake(stimuli, through=1)
    guard = await _guard_now(stimuli)

    async with engine.connect() as holder:
        await holder.execute(sa.text(
            "select pg_advisory_xact_lock(:c, hashtext(:u))"
        ), {"c": _LOCK_CLASS, "u": UNIT})
        write = asyncio.ensure_future(_wake(stimuli, through=2, at=NOW + 10, guard=guard))
        close = asyncio.ensure_future(_close(stimuli, first))
        await asyncio.wait({write, close}, timeout=0.5)
        assert not write.done(), "a guarded wake write ran while the unit's lock was held"
        assert not close.done(), "a close ran while the unit's lock was held"
        await holder.rollback()
    await asyncio.wait_for(asyncio.gather(write, close), timeout=10)


@pytest.mark.parametrize("round_", range(12))
async def test_a_guarded_wake_and_a_close_never_both_take_the_same_stimuli(sql_stores, round_):
    """The race: a second wake written under a guard that read «wake 1 has
    no outcome» RACES the close of wake 1. Under the lock exactly one order
    happened: the close first (the guard then sees a new outcome and refuses
    the write), or the write first (the close then finds a later wake, is
    recorded `timeout` and writes no carries). Never both: a new wake that
    claimed the range AND a `closed` with carries over it."""
    import asyncio

    stimuli, _ledger, _engine = sql_stores
    await _msgs(stimuli, 2)
    first = await _wake(stimuli, through=1)
    guard = await _guard_now(stimuli)

    wrote, closed = await asyncio.gather(
        _wake(stimuli, through=2, at=NOW + 10, guard=guard),
        _close(stimuli, first, carries=[_carry(1)]),
    )
    view = await _view(stimuli)
    if wrote is None:
        assert (closed.outcome, closed.written) == ("closed", True)
        assert view.last_wake.id == first and view.carry == {1: 1}
    else:
        assert (closed.outcome, closed.written) == ("timeout", True)
        assert view.last_wake.id == wrote and view.carry == {}


# ---- DEC-245: a wake that did not FAIL has no failure cause -----------------

@pytest.mark.parametrize("outcome", ["closed", "cancelled"])
async def test_a_closed_or_cancelled_outcome_records_no_cause_whatever_the_caller_passes(
    stores, outcome,
):
    """`closed` is not stored `infra` (the column's default): a session that
    succeeded must not read as «the world failed» in a count of infra failures."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 1)
    wake = await _wake(stimuli, through=1)
    await _close(stimuli, wake, outcome, cause="infra")
    view = await _view(stimuli, stale_before_ms=NOW + 1)
    assert view.last_session.cause == "none"


@pytest.mark.parametrize(("outcome", "cause"), [
    ("failed", "infra"), ("failed", "agenda"), ("timeout", "infra"), ("timeout", "agenda"),
])
async def test_a_failure_keeps_the_cause_it_was_written_with(stores, outcome, cause):
    stimuli, _ledger = stores
    await _msgs(stimuli, 1)
    wake = await _wake(stimuli, through=1)
    await _close(stimuli, wake, outcome, cause=cause)
    view = await _view(stimuli, stale_before_ms=NOW + 1)
    assert (view.last_session.state, view.last_session.cause) == ("failed", cause)


@pytest.mark.parametrize("outcome", ["failed", "timeout"])
async def test_a_failure_cannot_be_written_with_no_cause(stores, outcome):
    stimuli, _ledger = stores
    await _msgs(stimuli, 1)
    wake = await _wake(stimuli, through=1)
    with pytest.raises(ValueError, match="names its cause"):
        await _close(stimuli, wake, outcome, cause="none")



# ---- the restart reclaim's read ---------------------------------------------

async def test_open_wakes_lists_tracked_wakes_with_no_outcome_recorded_before_the_cut(stores):
    """`open_wakes(recorded_before_ms)`: the wakes a process that started at that
    instant cannot own. Tracked only (an untracked wake never has an outcome, so it is
    never «open»), no outcome row, oldest first, and expired ones INCLUDED (a process
    that no longer exists failed nothing, whatever the clock says)."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 4)
    closed = await _wake(stimuli, through=1, at=NOW)
    await _close(stimuli, closed)
    open_old = await _wake(stimuli, through=2, at=NOW + 10)
    untracked = await _wake(stimuli, through=3, at=NOW + 20, tracked=False)
    open_new = await _wake(stimuli, through=4, at=NOW + 30)

    listed = await stimuli.open_wakes(recorded_before_ms=NOW + 25)
    assert [w.id for w in listed] == [open_old]
    assert untracked not in [w.id for w in listed] and closed not in [w.id for w in listed]
    assert [w.id for w in await stimuli.open_wakes(recorded_before_ms=NOW + 31)] == [
        open_old, open_new,
    ]
    assert await stimuli.open_wakes(recorded_before_ms=NOW + 10) == []      # the cut is exclusive
    assert [w.unit for w in await stimuli.open_wakes(recorded_before_ms=NOW + 31)] == [UNIT] * 2


async def test_a_cancelled_outcome_closes_the_open_list_and_returns_the_range(stores):
    stimuli, _ledger = stores
    await _msgs(stimuli, 2)
    wake = await _wake(stimuli, through=2, at=NOW)
    assert [w.id for w in await stimuli.open_wakes(recorded_before_ms=NOW + 1)] == [wake]
    recorded = await _close(stimuli, wake, "cancelled", at=NOW + 5)
    assert (recorded.outcome, recorded.written) == ("cancelled", True)
    assert await stimuli.open_wakes(recorded_before_ms=NOW + 1) == []
    view = await _view(stimuli, stale_before_ms=NOW + 1)
    assert [s.id for s in view.pending] == [1, 2]
    assert view.mark.through_stimulus_id == 0
    assert view.failures == 0                       # a cancelled wake neither counts nor strikes
