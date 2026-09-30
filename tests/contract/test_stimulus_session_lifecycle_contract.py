"""Shared contract for what the wake policy reads about a wake's FATE: its one
outcome (`cognition_wake_outcome`), what a closed wake carried forward
(`cognition_carry`, written in the same transaction), a retracted `self`
stimulus (`stimulus_retraction`, one predicate for every read), the guarded
wake write, and the self-wake state the ledger folds (reviews of #627,
DEC-242). Every implementation runs every test.

Two implementations of each store: SQL over real Postgres
(`SqlStimulusStore` + `SqlCognitionDryRunStore`), and the in-memory doubles,
which the wake evaluator's unit tests run on, so the double must decide the
watermark by exactly the rule the SQL does.

**The property it is about is the watermark.** A wake is the unit's watermark
unless it FAILED (an outcome that is not `closed`) or EXPIRED (tracked, no
outcome, past the timeout): «a failed session does not advance it» (20 §3).
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

from nora.domain.ports import NewCarry, NewCognitionPace, NewWakeOutcome
from nora.domain.stimuli import NewStimulus, NewWake, WakeGuard
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
    "cognition_wake_outcome, cognition_session_close, cognition_self_wake, "
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


async def _wake(
    stimuli: Any, *, through: int, at: int = NOW, tracked: bool = True, guard: Any = None,
) -> Any:
    return await stimuli.record_wake(wake=NewWake(
        unit=UNIT, mode="shadow", reason="settled", from_stimulus_id=0,
        through_stimulus_id=through, stimulus_count=1, eligible_count=1,
        oldest_pending_ms=NOW, cut_cooldown=False, at_ms=at, tracked=tracked,
    ), recorded_at_ms=at, guard=guard)


async def _close(
    stimuli: Any, wake_id: int, outcome: str = "closed", *, carries: Any = (),
    at: int = NOW + 500,
) -> Any:
    return await stimuli.record_outcome(
        outcome=NewWakeOutcome(wake_id=wake_id, unit=UNIT, outcome=outcome, at_ms=at),
        carries=tuple(carries), recorded_at_ms=at,
    )


def _carry(sid: int, strike: int = 1, state: str = "carried", waits: bool = False) -> NewCarry:
    return NewCarry(unit=UNIT, stimulus_id=sid, strike=strike, state=state, waits=waits)


async def _view(stimuli: Any, **kw: Any) -> Any:
    return await stimuli.view(unit=UNIT, mode="shadow", now_ms=NOW + 10_000, limit=100, **kw)


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


async def test_an_untracked_wake_is_a_watermark_with_or_without_a_clock(stores):
    """No hook (`session_mode = "off"`): the step-1 behaviour, unchanged, and
    an untracked wake never expires even when the reader tracks sessions."""
    stimuli, _ledger = stores
    await _msgs(stimuli, 2)
    await _wake(stimuli, through=2, tracked=False)
    for kw in ({}, {"stale_before_ms": NOW + 10**9}):
        view = await _view(stimuli, **kw)
        assert view.pending == () and view.last_session is None
        assert view.last_wake.tracked is False


async def test_a_tracked_wake_with_no_outcome_is_open_then_expired_past_the_timeout(stores):
    """M1: a wake whose session row was never written (an exit before it, a
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
    """Minor 1: a session whose wake EXPIRED and whose stimuli a later wake
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
    """Minor 2: the outcome and every carry are one write. A carry the table
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
    """M2: a self-wake a closed session carried (deferred, or left open) and
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
    """Minor 9: only refs with a transition since `since_ms` are read. A ref
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
