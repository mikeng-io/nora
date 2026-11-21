"""Run Observe's clock and condition matcher without an agent occasion."""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.app.tick_grid import aligned_ticks
from nora.domain.ticks import TICK_SECONDS

log = logging.getLogger(__name__)


class _Executor(Protocol):
    async def run_once(self) -> None: ...


class GraphObserve:
    def __init__(self, *, clock: _Executor, matcher: _Executor, metrics: Any) -> None:
        self._clock = clock
        self._matcher = matcher
        self._metrics = metrics

    async def tick(self) -> None:
        for stage, executor in (("graph_clock", self._clock), ("condition_matcher", self._matcher)):
            try:
                await executor.run_once()
            except Exception:
                log.exception("observe tick failed (%s)", stage)
                self._metrics.inc("nora_errors_total", stage=stage)

    async def run(self) -> None:
        async for _ in aligned_ticks(TICK_SECONDS, immediate=True):
            await self.tick()


def graph_observe(engine: AsyncEngine, clock: Callable[[], datetime], metrics: Any) -> GraphObserve:
    import nora.memory as memory

    return GraphObserve(
        clock=memory.wire.graph_clock(engine, clock),
        matcher=memory.wire.condition_matcher(engine),
        metrics=metrics,
    )
