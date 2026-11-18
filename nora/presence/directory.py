"""Read the collector's volatile observations without querying provider accounts."""

import time
from collections.abc import Callable

from nora.domain.presence_circles import CircleObservation
from nora.presence.collector import PresenceCollector


class CollectorCircleObservations:
    def __init__(
        self, collector: PresenceCollector, *,
        clock: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._collector, self._clock = collector, clock

    async def read(self) -> tuple[CircleObservation, ...]:
        return self._collector.observations(now_ms=self._clock())
