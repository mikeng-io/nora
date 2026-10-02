"""The wake loop's pulse: `nora_cognition_wake_last_tick_seconds{mode}` (the Cognition
cut-over's PR-7). `HeartbeatStalled` watches the reminders loop only, so a wake
evaluator that stopped, or hung in a store call, was invisible while the process
answered HTTP.

The gauge is the unix time the loop last FINISHED a tick, written from inside the
loop. `CognitionWakeStalled` reads `time()` minus it, so what matters is that it
MOVES while the loop runs and STOPS moving when the loop does.
"""

from __future__ import annotations

import asyncio
import contextlib
import re

from nora.app.wake import precreate_session_series, wake_loop
from nora.domain.stimuli import WAKE_OUTCOMES
from nora.obs.metrics import Metrics

GAUGE = "nora_cognition_wake_last_tick_seconds"


class _Evaluator:
    mode = "live"

    def __init__(self) -> None:
        self.ticks = 0
        self.behaviour = "ok"
        self.hung = asyncio.Event()

    async def tick(self) -> list[int]:
        self.ticks += 1
        if self.behaviour == "raise":
            raise RuntimeError("the store is down")
        if self.behaviour == "hang":
            await self.hung.wait()
        return []


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


async def _until(predicate, *, what: str) -> None:
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.002)
    raise AssertionError(f"never happened: {what}")


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


SESSION_ENDED = "nora_cognition_session_ended_total"


def _ended_children(metrics: Metrics) -> dict[str, float]:
    """`outcome -> value` for every child the counter exports right now."""
    out = {}
    for ln in metrics.render().splitlines():
        m = re.match(rf'{SESSION_ENDED}\{{outcome="([a-z]+)"\}} ([0-9.e+-]+)$', ln)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def test_the_session_counter_has_no_children_until_something_creates_them():
    """The control for the next test: a labelled counter exports nothing before its
    first `inc`, which is the whole problem (`increase()` cannot see a first increment)."""
    assert _ended_children(Metrics()) == {}


async def test_every_session_outcome_child_exists_at_zero_right_after_the_loop_starts():
    """`CognitionSessionsFailing` reads `increase()` of this counter. A series that
    appears at its first increment appears at 1, with no earlier sample to subtract, so
    the first failure of every process (nora restarts 2 to 24 times a day) was invisible.
    The children are created at 0 when the wake loop starts, for EVERY outcome the code
    can write, before the first sleep and with no session having ended."""
    metrics = Metrics()
    task = asyncio.ensure_future(
        wake_loop(_Evaluator(), metrics, tick_seconds=3600.0, clock=_Counting()),
    )
    try:
        await _until(lambda: _ended_children(metrics) != {}, what="the children exist")
        assert _ended_children(metrics) == dict.fromkeys(WAKE_OUTCOMES, 0.0)
        assert set(WAKE_OUTCOMES) >= {"closed", "failed", "timeout"}     # what the rule reads
    finally:
        await _stop(task)


def test_creating_the_children_adds_nothing_to_a_counter_already_counting():
    """Idempotent and additive: a process that already counted a session keeps its value."""
    metrics = Metrics()
    metrics.inc(SESSION_ENDED, outcome="failed")
    precreate_session_series(metrics)
    assert _ended_children(metrics)["failed"] == 1.0
    assert _ended_children(metrics)["closed"] == 0.0


def test_the_series_is_absent_until_the_loop_runs():
    """An UNLABELLED gauge exports 0 from the moment it is declared, and `time() - 0`
    would page a deployment with `[cognition] mode = off`. The label is what makes
    «no wake loop» the absence of the series."""
    text = Metrics().render()
    assert f"# TYPE {GAUGE} gauge" in text
    assert not [ln for ln in text.splitlines() if ln.startswith(GAUGE)]


class _Counting:
    """A clock that reads a different time on every call, so a gauge value says WHICH
    call wrote it. (`_Clock`, set after the task is scheduled, is read before the task
    first runs: a test built on it cannot tell the start write from a tick's.)"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return 1_000.0 + self.calls


async def test_the_gauge_is_set_before_the_first_sleep():
    """A loop that dies in its first interval must not look like one never started:
    the pulse exists from the moment the loop does. `tick_seconds` is an hour, so
    nothing but the write before the first sleep can have set it."""
    metrics = Metrics()
    task = asyncio.ensure_future(
        wake_loop(_Evaluator(), metrics, tick_seconds=3600.0, clock=_Counting()),
    )
    try:
        await _until(lambda: metrics.get(GAUGE, mode="live") == 1_001.0, what="set at start")
    finally:
        await _stop(task)


async def test_the_gauge_moves_with_every_tick():
    metrics, clock, evaluator = Metrics(), _Counting(), _Evaluator()
    task = asyncio.ensure_future(wake_loop(evaluator, metrics, tick_seconds=0.001, clock=clock))
    try:
        await _until(lambda: evaluator.ticks >= 3, what="three ticks")
        await _until(
            lambda: metrics.get(GAUGE, mode="live") >= 1_004.0, what="moved by the ticks",
        )
    finally:
        await _stop(task)


async def test_a_tick_that_raises_still_moves_the_gauge_and_counts_the_error():
    """A tick that FINISHED, badly: the loop is alive, and `NoraErrorsRising` is what
    says it is failing. A gauge that froze here would read as a stalled loop. Every
    tick raises, so the start write is the ONLY write unless the raising path writes:
    the clock counts its calls and the gauge must have moved past the first."""
    metrics, clock, evaluator = Metrics(), _Counting(), _Evaluator()
    evaluator.behaviour = "raise"
    task = asyncio.ensure_future(wake_loop(evaluator, metrics, tick_seconds=0.001, clock=clock))
    try:
        await _until(lambda: evaluator.ticks >= 3, what="three raising ticks")
        await _until(
            lambda: metrics.get(GAUGE, mode="live") > 1_001.0, what="moved by a raising tick",
        )
        assert metrics.get("nora_errors_total", stage="cognition_wake") >= 1
    finally:
        await _stop(task)


async def test_a_loop_hung_inside_a_tick_stops_moving_the_gauge():
    """THE POINT: the stall is visible. A tick that never returns (a store call with no
    deadline) writes nothing, so the gauge freezes at its last value while the wall
    clock goes on, and `time() - gauge` climbs."""
    metrics, clock, evaluator = Metrics(), _Clock(), _Evaluator()
    task = asyncio.ensure_future(wake_loop(evaluator, metrics, tick_seconds=0.001, clock=clock))
    try:
        await _until(lambda: evaluator.ticks >= 1, what="a healthy tick")
        evaluator.behaviour = "hang"
        await _until(lambda: evaluator.ticks >= 2, what="the hung tick starts")
        frozen = metrics.get(GAUGE, mode="live")
        clock.now = 9_999.0
        await asyncio.sleep(0.05)
        assert metrics.get(GAUGE, mode="live") == frozen != clock.now
    finally:
        evaluator.hung.set()
        await _stop(task)
