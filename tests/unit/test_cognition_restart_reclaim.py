"""A restart must not hold a unit, strike a stimulus or lose one (DEC-244 «an outage never
strikes»).

Nora restarts routinely. A session a restart killed leaves its wake OPEN (tracked, no outcome row).
Nothing in the next process owns it, so without `WakeEvaluator.reclaim_orphans` the unit stays
single-flight for the rest of `session_timeout_seconds` (3,900 s), and then the wake EXPIRES, which
every reader counts as a FAILED session: the backoff doubles and the unit's failure streak grows.
Each test here has the control that fails without the reclaim.

An orphan is made the way a kill makes one: a hook that returns at once without
ever writing an outcome (`_leave_open`), so the wake row exists and nothing owns it.
On `InMemoryStimulusStore`, bound to the SQL store by the contract suite
(`tests/contract/test_stimulus_session_lifecycle_contract.py`), whose real-Postgres
half also pins `open_wakes`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from zoneinfo import ZoneInfo

from nora.app.wake import WakeEvaluator
from nora.domain.stimuli import (
    NewStimulus,
    UnitView,
    WakePolicy,
    is_restart_detail,
    unit_in_flight,
)
from nora.obs.metrics import Metrics
from nora.testing.stimuli import InMemoryStimulusStore

HK = ZoneInfo("Asia/Hong_Kong")
T = int(dt.datetime(2026, 9, 28, 15, 0, tzinfo=HK).timestamp() * 1000)
POLICY = WakePolicy(
    quiet_seconds=0, cue_quiet_seconds=0, batch_max=1000,
    max_age_seconds=3600, cooldown_seconds=300, max_concurrent_sessions=2,
    session_seconds=60, session_timeout_seconds=900,
)


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> float:
        return self.ms / 1000

    def advance(self, seconds: float) -> None:
        self.ms += int(seconds * 1000)


async def _leave_open(*, unit: str, view: UnitView, wake_id: int, now_ms: int) -> None:
    """The session hook of a process that is about to be killed: it took the wake
    and wrote nothing, and returns no awaitable, so no task will ever end it."""
    return None


def _process(store: InMemoryStimulusStore, clock: Clock, *, metrics: Metrics | None = None):
    """One process's evaluator over the shared database."""
    return WakeEvaluator(
        store=store, units=lambda: ("u1",), policy=POLICY, metrics=metrics or Metrics(),
        tz=HK, clock=clock, settle_seconds=0, on_wake=_leave_open,
    )


async def _say(store: InMemoryStimulusStore, clock: Clock, n: int = 1) -> list[int]:
    ids = []
    for _ in range(n):
        sid = await store.append(stimulus=NewStimulus(
            unit="u1", kind="message", item="message",
            ref=f"messages:{len(store.stimuli) + 1}", at_ms=clock.ms,
            platform="signal", conversation_id="room-a",
        ), recorded_at_ms=clock.ms)
        assert sid is not None
        ids.append(sid)
    return ids


async def _view(store: InMemoryStimulusStore, clock: Clock) -> UnitView:
    view = await store.view(
        unit="u1", mode="live", now_ms=clock.ms, limit=100,
        stale_before_ms=clock.ms - int(POLICY.session_timeout_seconds * 1000),
    )
    return replace(view, tracked=True)      # what the evaluator's own read sets


async def test_a_wake_a_previous_process_left_open_is_released_as_a_restart():
    store, clock = InMemoryStimulusStore(), Clock(T)
    await _say(store, clock)
    clock.advance(1)
    assert len(await _process(store, clock).tick()) == 1            # process A's wake
    assert store.outcomes == {}                                      # open: nothing ends it

    clock.advance(60)                                                # process B starts
    started = clock.ms
    reborn = _process(store, clock)
    assert await reborn.reclaim_orphans(process_started_ms=started) == 1

    outcome = store.outcomes[1]
    assert (outcome.outcome, outcome.cause) == ("cancelled", "none")
    assert is_restart_detail(outcome.detail)


async def test_without_the_reclaim_the_unit_stays_held_and_with_it_the_next_wake_goes_ahead():
    """The control and the claim, side by side. Same orphan, same minute after the
    restart: the unit is in flight until its wake expires (900 s here, 3,900 live),
    unless the new process released it."""
    store, clock = InMemoryStimulusStore(), Clock(T)
    await _say(store, clock)
    clock.advance(1)
    await _process(store, clock).tick()
    clock.advance(400)                  # past the cooldown, well inside the session timeout
    await _say(store, clock)            # something new arrived

    held = await _view(store, clock)
    assert unit_in_flight(held, POLICY, clock.ms)                    # CONTROL: held by nobody
    assert await _process(store, clock).tick() == []

    clock.advance(1)
    assert await _process(store, clock).reclaim_orphans(process_started_ms=clock.ms) == 1
    freed = await _view(store, clock)
    assert not unit_in_flight(freed, POLICY, clock.ms)
    clock.advance(301)                  # the cooldown runs from the release, as after any end
    assert len(await _process(store, clock).tick()) == 1


async def test_a_released_wake_returns_its_stimuli_and_the_watermark_does_not_move():
    store, clock = InMemoryStimulusStore(), Clock(T)
    ids = await _say(store, clock, 3)
    clock.advance(1)
    await _process(store, clock).tick()
    claimed = await _view(store, clock)
    assert claimed.pending == ()                                     # the open wake claimed them
    assert claimed.mark is not None and claimed.mark.through_stimulus_id == ids[-1]

    clock.advance(10)
    await _process(store, clock).reclaim_orphans(process_started_ms=clock.ms)
    after = await _view(store, clock)
    assert after.mark is not None and after.mark.through_stimulus_id == 0
    assert [s.id for s in after.pending] == ids                      # every one pending again


async def test_a_restart_is_never_a_failure_however_many_in_a_row():
    """DEC-244: an outage never strikes. Six restarts in a row, each leaving its wake
    open, are six `cancelled`: no failure streak, no strike, no backoff. The control
    is the same six left to EXPIRE, which the reader counts as six failures."""
    expired, clock = InMemoryStimulusStore(), Clock(T)
    await _say(expired, clock)
    for _ in range(6):
        clock.advance(POLICY.session_timeout_seconds + POLICY.cooldown_seconds + 5)
        await _process(expired, clock).tick()
    clock.advance(POLICY.session_timeout_seconds + 5)
    assert (await _view(expired, clock)).failures >= 3               # CONTROL: expiry is a failure

    store, clock = InMemoryStimulusStore(), Clock(T)
    metrics = Metrics()
    await _say(store, clock)
    for _ in range(6):
        clock.advance(30)
        started = clock.ms
        process = _process(store, clock, metrics=metrics)
        await process.reclaim_orphans(process_started_ms=started)    # the new process boots
        clock.advance(POLICY.cooldown_seconds + 1)                   # the cooldown, from release
        assert len(await process.tick()) == 1                        # then it wakes it
    clock.advance(POLICY.session_timeout_seconds + 5)
    await _process(store, clock).reclaim_orphans(process_started_ms=clock.ms)
    view = await _view(store, clock)
    assert (view.failures, view.agenda_failures) == (0, 0)
    assert [o.outcome for _, o in sorted(store.outcomes.items())] == ["cancelled"] * 6
    assert metrics.get("nora_cognition_stimuli_struck_total") == 0
    assert metrics.get("nora_cognition_wake_reclaimed_total") == 5   # 5 boots found one open


async def test_a_wake_this_process_recorded_is_not_released():
    store, clock = InMemoryStimulusStore(), Clock(T)
    await _say(store, clock)
    started = clock.ms
    process = _process(store, clock)
    clock.advance(5)
    assert len(await process.tick()) == 1                            # recorded AFTER start
    assert await process.reclaim_orphans(process_started_ms=started) == 0
    assert store.outcomes == {}


async def test_a_wake_that_already_has_an_outcome_keeps_it_and_untracked_wakes_are_ignored():
    store, clock = InMemoryStimulusStore(), Clock(T)
    await _say(store, clock)
    clock.advance(1)
    await _process(store, clock).tick()
    store.seed_wake("u2", 90, tracked=False)                         # a step-1 watermark row
    from nora.domain.ports import NewWakeOutcome
    await store.record_outcome(
        outcome=NewWakeOutcome(wake_id=1, unit="u1", outcome="closed", at_ms=clock.ms),
        recorded_at_ms=clock.ms,
    )
    clock.advance(10)
    assert await _process(store, clock).reclaim_orphans(process_started_ms=clock.ms) == 0
    assert store.outcomes[1].outcome == "closed"
    assert 90 not in store.outcomes


async def test_reclaiming_twice_releases_once():
    store, clock = InMemoryStimulusStore(), Clock(T)
    await _say(store, clock)
    clock.advance(1)
    await _process(store, clock).tick()
    clock.advance(10)
    process = _process(store, clock)
    assert await process.reclaim_orphans(process_started_ms=clock.ms) == 1
    assert await process.reclaim_orphans(process_started_ms=clock.ms) == 0


async def test_a_store_that_fails_costs_the_release_not_the_boot():
    """The wake stays open and expires on its own clock, exactly as before; the
    process starts, and the failure is counted on `nora_errors_total`."""
    store, clock, metrics = InMemoryStimulusStore(), Clock(T), Metrics()
    await _say(store, clock)
    clock.advance(1)
    await _process(store, clock).tick()
    clock.advance(10)
    process = _process(store, clock, metrics=metrics)

    store.fail_on = {"open_wakes"}
    assert await process.reclaim_orphans(process_started_ms=clock.ms) == 0
    assert metrics.get("nora_errors_total", stage="cognition_reclaim") == 1

    store.fail_on = {"record_outcome"}
    assert await process.reclaim_orphans(process_started_ms=clock.ms) == 0
    assert metrics.get("nora_errors_total", stage="cognition_reclaim") == 2
    assert store.outcomes == {}
