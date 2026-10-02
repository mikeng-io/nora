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

from nora.app.wake import wake_loop
from nora.obs.metrics import Metrics

GAUGE = "nora_cognition_wake_last_tick_seconds"


class _Evaluator:
    mode = "shadow"

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


def test_the_series_is_absent_until_the_loop_runs():
    """An UNLABELLED gauge exports 0 from the moment it is declared, and `time() - 0`
    would page a deployment with `[cognition] mode = off`. The label is what makes
    «no wake loop» the absence of the series."""
    text = Metrics().render()
    assert f"# TYPE {GAUGE} gauge" in text
    assert not [ln for ln in text.splitlines() if ln.startswith(GAUGE)]


async def test_the_gauge_is_set_at_start_and_moves_with_every_tick():
    metrics, clock, evaluator = Metrics(), _Clock(), _Evaluator()
    task = asyncio.ensure_future(wake_loop(evaluator, metrics, tick_seconds=0.001, clock=clock))
    try:
        await _until(lambda: metrics.get(GAUGE, mode="shadow") == 1_000.0, what="set at start")
        clock.now = 2_000.0
        await _until(lambda: metrics.get(GAUGE, mode="shadow") == 2_000.0, what="moved by a tick")
        assert evaluator.ticks >= 1
    finally:
        await _stop(task)


async def test_a_tick_that_raises_still_moves_the_gauge_and_counts_the_error():
    """A tick that FINISHED, badly: the loop is alive, and `NoraErrorsRising` is what
    says it is failing. A gauge that froze here would read as a stalled loop."""
    metrics, clock, evaluator = Metrics(), _Clock(), _Evaluator()
    evaluator.behaviour = "raise"
    task = asyncio.ensure_future(wake_loop(evaluator, metrics, tick_seconds=0.001, clock=clock))
    try:
        clock.now = 3_000.0
        await _until(lambda: metrics.get(GAUGE, mode="shadow") == 3_000.0, what="moved")
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
        frozen = metrics.get(GAUGE, mode="shadow")
        clock.now = 9_999.0
        await asyncio.sleep(0.05)
        assert metrics.get(GAUGE, mode="shadow") == frozen != clock.now
    finally:
        evaluator.hung.set()
        await _stop(task)
