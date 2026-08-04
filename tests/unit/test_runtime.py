"""`nora.app.runtime` — the four lifetime concerns `Handler` and `App` used to own
directly (Phase 3, Task 7): the strong-reference background task set, the prune
sweep, the `Debounce` TTL sweep, and per-adapter restart-with-backoff supervision.

Every test here proves *behaviour*, not shape: a coroutine existing is not enough,
each loop must actually run and actually do the thing it claims to.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import logging

from nora.app.runtime import (
    BackgroundTasks,
    SupervisedAdapter,
    debounce_sweep_loop,
    prune_loop,
    start_adapters,
    supervise_adapter,
)
from nora.obs.metrics import Metrics
from nora.tag import Debounce

# ---------------------------------------------------------------------------
# BackgroundTasks
# ---------------------------------------------------------------------------


async def test_spawned_task_survives_gc_because_of_the_strong_reference():
    """The task-tracking test the brief's mandatory probe targets: drop every
    external reference to the spawned task, force a collection, and prove the
    coroutine still runs to completion because `BackgroundTasks` itself holds
    the strong reference asyncio does not."""
    bg = BackgroundTasks()
    ran = False

    async def work() -> None:
        nonlocal ran
        await asyncio.sleep(0.05)
        ran = True

    bg.spawn(work())
    gc.collect()
    await asyncio.sleep(0.2)
    assert ran is True


async def test_a_cancelled_task_is_discarded_without_logging_an_error(caplog):
    bg = BackgroundTasks()
    caplog.set_level(logging.ERROR, logger="nora.app.runtime")
    started = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.sleep(10)

    task = bg.spawn(work())
    await started.wait()
    task.cancel()
    await asyncio.wait([task])

    assert len(bg) == 0
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_a_failing_task_logs_and_is_discarded(caplog):
    bg = BackgroundTasks()
    caplog.set_level(logging.ERROR, logger="nora.app.runtime")

    async def work() -> None:
        raise ValueError("boom")

    task = bg.spawn(work())
    await asyncio.wait([task])

    assert len(bg) == 0
    assert any("background task failed" in r.message for r in caplog.records)


async def test_cancel_all_cancels_every_tracked_task():
    bg = BackgroundTasks()

    async def work() -> None:
        await asyncio.sleep(10)

    bg.spawn(work())
    bg.spawn(work())
    assert len(bg) == 2

    await bg.cancel_all()
    assert len(bg) == 0


# ---------------------------------------------------------------------------
# prune_loop
# ---------------------------------------------------------------------------


async def test_prune_loop_survives_a_failing_sweep_and_keeps_looping():
    """Proves the sweep actually runs (not just that the coroutine exists), and
    that a failure on one iteration does not stop the next one from happening."""

    class FailFirstThenOkStore:
        def __init__(self) -> None:
            self.calls = 0

        async def prune_all(self) -> int:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return 3

    store = FailFirstThenOkStore()
    metrics = Metrics()

    task = asyncio.create_task(prune_loop(store, metrics, interval_seconds=0.01))
    await asyncio.sleep(0.08)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert store.calls >= 2, "the loop must run again after a failing sweep"
    assert metrics.get("nora_errors_total", stage="prune") == 1


# ---------------------------------------------------------------------------
# debounce_sweep_loop
# ---------------------------------------------------------------------------


async def test_debounce_sweep_loop_actually_removes_stale_entries():
    """Asserts the dict shrinks, not that the coroutine exists."""
    clock = {"t": 0.0}
    d = Debounce(1.0, clock=lambda: clock["t"])
    d.allow("a")
    d.allow("b")
    assert len(d._last) == 2

    clock["t"] = 100.0  # both entries are now far past any reasonable ttl
    metrics = Metrics()

    task = asyncio.create_task(
        debounce_sweep_loop(d, metrics, interval_seconds=0.01, ttl_seconds=5.0)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert len(d._last) == 0


async def test_debounce_sweep_loop_survives_a_failing_sweep():
    calls = {"n": 0}
    real_sweep = Debounce.sweep

    class ExplodingDebounce(Debounce):
        def sweep(self, older_than: float) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real_sweep(self, older_than)

    d = ExplodingDebounce(1.0)
    metrics = Metrics()

    task = asyncio.create_task(
        debounce_sweep_loop(d, metrics, interval_seconds=0.01, ttl_seconds=5.0)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls["n"] >= 2
    assert metrics.get("nora_errors_total", stage="debounce_sweep") == 1


# ---------------------------------------------------------------------------
# adapter supervision
# ---------------------------------------------------------------------------


async def test_a_failing_adapter_is_restarted_and_counted():
    metrics = Metrics()
    calls = 0

    async def flaky_listen() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("wedged")

    adapter = SupervisedAdapter(platform="signal", listen=flaky_listen)
    task = asyncio.create_task(
        supervise_adapter(adapter, metrics, base_delay_seconds=0.01, max_delay_seconds=0.02)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls >= 2, "the adapter must be restarted after it fails"
    assert metrics.get("nora_adapter_restarts_total", platform="signal") >= 1


async def test_a_second_adapter_keeps_running_while_the_first_fails():
    metrics = Metrics()
    bg = BackgroundTasks()
    bad_calls = 0
    good_calls = 0
    good_running = asyncio.Event()

    async def bad_listen() -> None:
        nonlocal bad_calls
        bad_calls += 1
        raise RuntimeError("wedged")

    async def good_listen() -> None:
        nonlocal good_calls
        good_calls += 1
        good_running.set()
        await asyncio.sleep(10)

    adapters = [
        SupervisedAdapter(platform="bad", listen=bad_listen),
        SupervisedAdapter(platform="good", listen=good_listen),
    ]
    tasks = start_adapters(bg, adapters, metrics)
    assert len(bg) == 2

    await good_running.wait()
    await asyncio.sleep(0.05)

    assert bad_calls >= 1
    assert metrics.get("nora_adapter_restarts_total", platform="bad") >= 1
    assert good_calls == 1, "the healthy adapter must not have been restarted"
    assert metrics.get("nora_adapter_restarts_total", platform="good") == 0

    for t in tasks:
        t.cancel()
    await asyncio.wait(tasks)
    assert len(bg) == 0
