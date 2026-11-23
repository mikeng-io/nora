"""Poll live watches at a bounded cadence."""

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Protocol

from nora.memory.domain.admission_proposals import SourceReference

# ponytail: a cycle starts at most once every 15 minutes per process.
WATCH_INTERVAL = timedelta(minutes=15)
# ponytail: at most three provider reads per cycle, each under its own timeout.
WATCH_LIMIT = 3

WatchKey = tuple[str, str]
WatchReader = Callable[[str], Awaitable[str | None]]


class WatchAttempt(Protocol):
    @property
    def key(self) -> WatchKey: ...

    @property
    def reference(self) -> SourceReference: ...

    async def record(self, digest: str | None, at: datetime) -> None: ...


class WatchStore(Protocol):
    def next(self, after: WatchKey | None) -> AbstractAsyncContextManager[WatchAttempt | None]: ...


class WatchPoller:
    def __init__(
        self,
        store: WatchStore,
        readers: Mapping[str, WatchReader],
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._readers = readers
        self._clock = clock
        self._due: datetime | None = None
        self._after: WatchKey | None = None

    async def run_once(self) -> None:
        at = self._clock()
        if self._due is not None and at < self._due:
            return
        self._due = at + WATCH_INTERVAL
        seen: set[WatchKey] = set()
        for _ in range(WATCH_LIMIT):
            async with self._store.next(self._after) as attempt:
                if attempt is None or attempt.key in seen:
                    return
                reference = attempt.reference
                seen.add(attempt.key)
                content = await self._readers[reference.kind](reference.value)
                digest = sha256(content.encode()).hexdigest() if content is not None else None
                await attempt.record(digest, self._clock())
            self._after = attempt.key
