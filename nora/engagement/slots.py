"""R-10's single-in-flight lock and R-30's per-conversation FIFO queue, as ONE
collaborator.

WHY ONE OBJECT
--------------
The lock's ownership is split across `admission.run` (acquires) and the
pipeline's `finally` (releases); getting that wrong strands a queued tag
forever.

The heartbeat fire path (`nora.app.heartbeat`) must contend for the SAME slot:
a reminder must not run while a tag is in flight for that conversation, and a
tag must not start while a reminder is firing. A release that does not start
the next queued turn strands whatever queued behind the fire turn until the
NEXT tag arrives, and that tag would then jump the queue. So the pair lives
behind one object both paths hold.

WHY `enqueue` TAKES A FACTORY
-----------------------------
The queued work is a pipeline method (`_run_queued`). Storing a bound coroutine
would make this class hold a reference back into the pipeline; storing a
CALLBACK would be the `*_fn` hook CLAUDE.md's test-seam policy forbids. A
zero-argument factory is neither: the pipeline closes over its own state, and
this class needs nothing but `spawn`, which it takes at construction like any
other collaborator.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from .queue import ConversationQueues

Key = tuple[str, str]
Factory = Callable[[], Coroutine[Any, Any, None]]


class ConversationSlots:
    def __init__(
        self, *, maxlen: int, spawn: Callable[[Coroutine[Any, Any, None]], Any],
    ) -> None:
        self._in_flight: set[Key] = set()
        self._queues: ConversationQueues[Key, Factory] = ConversationQueues(maxlen=maxlen)
        self._spawn = spawn

    @property
    def maxlen(self) -> int:
        return self._queues.maxlen

    def depth(self, key: Key) -> int:
        return self._queues.depth(key)

    def held(self, key: Key) -> bool:
        return key in self._in_flight

    def acquire(self, key: Key) -> None:
        """R-10 acquire. The caller owns the matching `release_and_dispatch`."""
        self._in_flight.add(key)

    def try_acquire(self, key: Key) -> bool:
        """Acquire iff free. The heartbeat's entry point: it never queues and
        never waits — a tick that cannot get the slot leaves its rows `pending`
        and retries next tick."""
        if key in self._in_flight:
            return False
        self._in_flight.add(key)
        return True

    def enqueue(self, key: Key, factory: Factory) -> None:
        self._queues.enqueue(key, factory)

    def release_and_dispatch(self, key: Key) -> None:
        """Release the slot, then immediately start the next queued turn if
        there is one — re-acquiring the slot for it. Both operations are
        synchronous with no await between them, so no arriving tag can slip
        into the run-now path in the gap."""
        self._in_flight.discard(key)
        factory = self._queues.dequeue(key)
        if factory is None:
            return
        self._in_flight.add(key)
        self._spawn(factory())

    def __len__(self) -> int:
        """Conversations with at least one waiting turn — for the
        nothing-is-retained assertions."""
        return len(self._queues)
