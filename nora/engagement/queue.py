"""Per-CONVERSATION FIFO queues — the third per-conversation structure, next
to R-9's debounce window and R-10's in-flight lock (R-30, DEC-49).

WHY THIS EXISTS
---------------
A second tag that arrives while a conversation's turn is in flight is QUEUED,
not dropped: dropping it would leave persons 2 and 3 in silence when three
people tag at once, indistinguishable from being ignored (R-5's failure shape
pointed at people instead of the operator). The queue drains one turn at a time
as each in-flight turn finishes.

WHY ONE TURN AT A TIME (correctness, not politeness)
----------------------------------------------------
Each turn's context is assembled at DEQUEUE time, so tag 2 sees tag 1's reply
already in the transcript. Two concurrent turns for the same conversation would
both reason from a room that is missing the other's reply and could re-answer
or contradict each other. R-10's "one in-flight turn per conversation" is
therefore preserved exactly — the queue feeds that single slot in order, it
does not widen it.

WHY THE KEY IS REMOVED WHEN DRAINED
-----------------------------------
State here is per-CONVERSATION, never per-tag: a `deque` per active
conversation, and the whole entry is deleted the moment its last waiting tag is
dequeued (`dequeue`). Nothing accumulates once a conversation goes quiet — the
`Handler._prefills`/`_guards` leak shape CLAUDE.md names cannot reappear here,
and `tests/architecture/test_no_retained_per_tag_state.py` holds because
a queued item is transient: it lives only while its tag genuinely waits.

WHY IT IS BOUNDED
-----------------
`maxlen` guards against an unbounded flood (a runaway sender, a loop), NOT
against waiting — a person answering three group messages takes over a minute,
so a tag waiting 45s is normal, not stale. There is deliberately NO staleness
rule: elapsed time is a weak proxy for "the group moved on", and dropping a tag
someone is still waiting on is the exact failure this structure removes. When a
conversation's queue is already at `maxlen`, the NEWEST arriving tag is refused
(`nora_tags_dropped_total{reason="queue_full"}`) — the flood is shed at the
edge while everything already accepted keeps its place in line.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Hashable
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class ConversationQueues(Generic[K, V]):
    def __init__(self, *, maxlen: int) -> None:
        if maxlen < 1:
            raise ValueError(f"queue maxlen must be >= 1, got {maxlen}")
        self._maxlen = maxlen
        self._queues: dict[K, deque[V]] = {}

    @property
    def maxlen(self) -> int:
        return self._maxlen

    def depth(self, key: K) -> int:
        """How many tags are already waiting behind `key`'s in-flight turn."""
        q = self._queues.get(key)
        return len(q) if q is not None else 0

    def enqueue(self, key: K, item: V) -> None:
        """Append `item` to the back of `key`'s FIFO queue. The caller decides
        admission (including the `maxlen` flood guard) BEFORE calling this — the
        queue does not silently drop, so a full queue is a decision made in
        `nora.engagement.stages.admission`, visible and counted, not a
        `deque(maxlen=...)` eviction nobody can see."""
        self._queues.setdefault(key, deque()).append(item)

    def dequeue(self, key: K) -> V | None:
        """Pop the FRONT of `key`'s queue (FIFO), or None if empty. The entry is
        deleted once its last waiting tag is removed, so a drained conversation
        retains nothing."""
        q = self._queues.get(key)
        if not q:
            return None
        item = q.popleft()
        if not q:
            del self._queues[key]
        return item

    def __len__(self) -> int:
        """The number of conversations with at least one waiting tag — for
        assertions that nothing is retained after every queue drains."""
        return len(self._queues)
