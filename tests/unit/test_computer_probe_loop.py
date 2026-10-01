"""`computer_probe_loop` (DEC-260): probe at once, keep going past a probe that raised."""

from __future__ import annotations

import asyncio

import pytest

from nora.app.runtime import computer_probe_loop
from nora.obs.metrics import Metrics


async def test_the_first_probe_runs_before_the_first_sleep() -> None:
    """`nora_computer_up` has no value until a probe has run, so the loop must
    not wait an interval first: with an interval an hour long, one probe still
    happens."""
    seen = asyncio.Event()

    class Probe:
        async def probe_once(self) -> bool:
            seen.set()
            return True

    task = asyncio.create_task(computer_probe_loop(Probe(), Metrics(), interval_seconds=3600))
    await asyncio.wait_for(seen.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_probe_that_raises_is_counted_and_the_loop_keeps_probing() -> None:
    calls = 0
    thrice = asyncio.Event()

    class Boom:
        async def probe_once(self) -> bool:
            nonlocal calls
            calls += 1
            if calls >= 3:
                thrice.set()
            raise RuntimeError("down")

    metrics = Metrics()
    task = asyncio.create_task(computer_probe_loop(Boom(), metrics, interval_seconds=0.001))
    await asyncio.wait_for(thrice.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert metrics.get("nora_errors_total", stage="computer_probe") >= 2
