"""Run Observe's clock, matcher and verifier without an agent occasion."""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.app.tick_grid import aligned_ticks
from nora.config import AnalyticsConfig
from nora.domain.ports import SearchProvider
from nora.domain.ticks import TICK_SECONDS

log = logging.getLogger(__name__)


class _Executor(Protocol):
    async def run_once(self) -> None: ...


class GraphObserve:
    def __init__(
        self, *, clock: _Executor, matcher: _Executor, verifier: _Executor,
        watches: _Executor | None, metrics: Any,
    ) -> None:
        self._executors = (("graph_clock", clock), ("condition_matcher", matcher),
                           ("intention_verify", verifier), ("watch_poller", watches))
        self._metrics = metrics

    async def tick(self) -> None:
        for stage, executor in self._executors:
            if executor is None:
                continue
            try:
                await executor.run_once()
            except Exception:
                log.exception("observe tick failed (%s)", stage)
                self._metrics.inc("nora_errors_total", stage=stage)

    async def run(self) -> None:
        async for _ in aligned_ticks(TICK_SECONDS, immediate=True):
            await self.tick()


def graph_observe(
    engine: AsyncEngine, clock: Callable[[], datetime], metrics: Any,
    provider: SearchProvider | None = None, *, analytics: AnalyticsConfig | None = None,
) -> GraphObserve:
    import nora.memory as memory

    analytics = analytics if analytics is not None else AnalyticsConfig()
    return GraphObserve(
        clock=memory.wire.graph_clock(engine, clock),
        matcher=memory.wire.condition_matcher(engine),
        verifier=memory.wire.intention_verifier(
            engine, clock, max_poll_gap_seconds=analytics.health_max_poll_gap_seconds,
        ),
        watches=memory.wire.watch_poller(engine, clock, provider) if provider is not None else None,
        metrics=metrics,
    )
