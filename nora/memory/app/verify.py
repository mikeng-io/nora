"""Run deterministic intention verification after condition matching."""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from nora.domain.rollup import HealthRow, adapter_up, polls_healthy

ClockEnding = Literal["dropped", "leaked", "unknown"]


@dataclass(frozen=True, slots=True)
class DueIntention:
    unit: str
    id: str
    deadline: datetime
    drops: bool


class VerificationBatch(Protocol):
    async def due(self, now: datetime) -> Sequence[DueIntention]: ...

    async def window(self, due: DueIntention) -> tuple[datetime, set[tuple[str, str]]]: ...

    async def end(self, due: DueIntention, ending: ClockEnding) -> None: ...

    async def record_verdicts(self, clock: Callable[[], datetime]) -> None: ...


class VerificationStore(Protocol):
    def batch(self) -> AbstractAsyncContextManager[VerificationBatch]: ...


class HealthReader(Protocol):
    async def health_rows(
        self, *, sources: Sequence[str], start_ms: int, through_ms: int,
        graph_unit: str | None = None, require_preceding: bool | None = None,
    ) -> list[HealthRow]: ...


class IntentionVerifier:
    def __init__(
        self, store: VerificationStore, health: HealthReader, clock: Callable[[], datetime],
        *, max_poll_gap_ms: int,
    ) -> None:
        self._store = store
        self._health = health
        self._clock = clock
        self._max_poll_gap_ms = max_poll_gap_ms

    async def run_once(self) -> None:
        async with self._store.batch() as batch:
            for due in await batch.due(self._clock()):
                if due.drops:
                    ending: ClockEnding = "dropped"
                else:
                    opened, sources = await batch.window(due)
                    ending = "leaked" if await self._healthy(due, opened, sources) else "unknown"
                await batch.end(due, ending)
            await batch.record_verdicts(self._clock)

    async def _healthy(
        self, due: DueIntention, opened: datetime, sources: set[tuple[str, str]],
    ) -> bool:
        if not sources or opened > due.deadline:
            return False
        start, end = (int(at.timestamp() * 1000) for at in (opened, due.deadline))
        rows = await self._health.health_rows(
            sources=sorted({source for source, _ in sources}),
            start_ms=start, through_ms=end, graph_unit=due.unit, require_preceding=True,
        )
        return all(
            _healthy_source(rows, source, scope, start, end, self._max_poll_gap_ms)
            for source, scope in sources
        )


def _healthy_source(
    rows: Sequence[HealthRow], source: str, scope: str, start: int, end: int, gap: int,
) -> bool:
    if not any(row.source == source and row.scope == scope and start <= row.at_ms <= end
               for row in rows):
        return False
    if source == "presence":
        return polls_healthy(
            rows, scope=scope, start_ms=start, end_ms=end,
            max_gap_ms=gap, require_preceding=True,
        )
    return adapter_up(rows, platform=source, start_ms=start, end_ms=end, require_preceding=True)
