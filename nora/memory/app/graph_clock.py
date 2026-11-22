"""Place current graph schedules on the logical UTC tick grid."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from nora.domain.ticks import tick_of_at


class DueSchedules(Protocol):
    async def publish_due(self, tick: int) -> None: ...


class GraphClock:
    def __init__(self, store: DueSchedules, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock

    async def run_once(self) -> None:
        await self._store.publish_due(tick_of_at(self._clock()))
