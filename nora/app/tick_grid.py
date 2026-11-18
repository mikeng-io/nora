"""Runtime scheduling on the UTC grid."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from nora.domain.ticks import seconds_to_boundary

if TYPE_CHECKING:
    from nora.app.wake import WakeEvaluator

log = logging.getLogger("nora.app.wake")


async def aligned_ticks(interval: float, *, immediate: bool = False) -> AsyncIterator[None]:
    if immediate:
        yield
    while True:
        await asyncio.sleep(seconds_to_boundary(interval))
        yield


async def wake_loop(
    evaluator: WakeEvaluator, metrics: Any, *, tick_seconds: float,
) -> None:
    """The evaluator's clock: `tick` every `tick_seconds`, forever. A failing
    tick is logged and counted and never takes the loop down."""
    log.info("cognition wake evaluator started: tick every %gs", tick_seconds)
    async for _ in aligned_ticks(tick_seconds):
        try:
            await evaluator.tick()
        except Exception:
            log.exception("cognition wake tick failed")
            metrics.inc("nora_errors_total", stage="cognition_wake")
