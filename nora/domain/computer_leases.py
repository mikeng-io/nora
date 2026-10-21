"""Leases on Nora's computer: who may change a unit's work right now.

**Lock the workspace, independently of sessions.** Every non-job group call
uses `scope_key` for the unit. Subagent jobs keep their own `job:<id>` key.
`computer_run`, `computer_write` and `computer_edit` take one around the
call; reads and the observed-state read take none.

**The live turn never queues behind the background.** A live actor (`tag`,
`fire`) waits up to `live_wait_seconds`, then is refused (the tool answers
`unavailable` with a note). A background actor (`cognition`, `dreamer:*`,
the sweeper) never waits: if anyone holds or is waiting for the key, it is
refused at once and defers to its next pass. That is the rule the heartbeat
and the compute poller already follow for the conversation slot.

**In-process locks are enough, and that is a property of the deployment,
not of this code:** one Nora process serves every room, so every caller of
a key shares this one object. A second Nora process would need a lease in
Postgres (a row with a holder, a TTL and a heartbeat) instead.
A lease here lives exactly as long as the call it guards and is released in
a `finally`, so there is nothing to expire: a crash releases it with the
process.

**No pre-emption:** a live turn does not pre-empt a background holder
mid-call. It waits its bound like any other live caller.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

#: How long a live turn waits for a held lease before it answers that the
#: computer is busy. Short: someone is waiting on the reply.
LIVE_WAIT_SECONDS = 10.0


def session_key(session_id: UUID) -> str:
    return f"session:{session_id}"


def scope_key(scope: str) -> str:
    return f"scope:{scope}"


def lease_key(scope: str, session_id: UUID | None) -> str:
    """The unit workspace's key, independent of session identity.

    Keep session_id in the shared caller signature; it is audit provenance,
    never an alternative lock. Subagent callers use their own job key.
    """
    return scope_key(scope)


@dataclass(frozen=True)
class Lease:
    """A held lease. `waited_ms` is how long acquiring it took."""

    key: str
    live: bool
    waited_ms: int
    acquired_at: float


class ComputerLeases:
    """The process's leases, one `asyncio.Lock` per key, created on first use
    and dropped when nobody holds or waits for it (so the map does not grow
    with every session there has ever been)."""

    def __init__(
        self, *, live_wait_seconds: float = LIVE_WAIT_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._live_wait = live_wait_seconds
        self._monotonic = monotonic
        self._locks: dict[str, asyncio.Lock] = {}
        # Holders plus waiters per key: what a background caller reads to
        # know it would queue, without reaching into the lock's internals.
        self._users: dict[str, int] = {}

    def busy(self, key: str) -> bool:
        return self._users.get(key, 0) > 0

    async def acquire(
        self, key: str, *, live: bool, wait_seconds: float | None = None,
    ) -> Lease | None:
        """A lease on `key`, or None when refused: a background caller finds
        it held or waited for; a live one waited `live_wait_seconds`, or `wait_seconds`
        when the caller names its own bound (a sub-agent job's)."""
        if not live and self.busy(key):
            return None
        started = self._monotonic()
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._users[key] = self._users.get(key, 0) + 1
        try:
            if live:
                await asyncio.wait_for(
                    lock.acquire(),
                    self._live_wait if wait_seconds is None else wait_seconds,
                )
            else:
                await lock.acquire()  # nobody holds or waits: immediate
        except TimeoutError:
            self._leave(key)
            return None
        except BaseException:
            self._leave(key)
            raise
        now = self._monotonic()
        return Lease(key=key, live=live, waited_ms=int((now - started) * 1000),
                     acquired_at=now)

    async def try_acquire(self, key: str) -> Lease | None:
        """The background rule, by name: never waits."""
        return await self.acquire(key, live=False)

    def release(self, lease: Lease) -> int:
        """Release `lease`; how long it was held, in ms."""
        lock = self._locks.get(lease.key)
        if lock is not None and lock.locked():
            lock.release()
        self._leave(lease.key)
        return int((self._monotonic() - lease.acquired_at) * 1000)

    def _leave(self, key: str) -> None:
        left = self._users.get(key, 0) - 1
        if left > 0:
            self._users[key] = left
            return
        self._users.pop(key, None)
        self._locks.pop(key, None)
