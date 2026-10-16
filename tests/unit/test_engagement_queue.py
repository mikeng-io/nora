"""`ConversationQueues` — the per-conversation FIFO structure of R-30, beside
R-9's debounce and R-10's in-flight lock.

The behaviour that matters for the rule: FIFO order, a bound that refuses the
newest arrival rather than evicting silently, per-conversation isolation, and —
the leak-shape CLAUDE.md names — nothing retained once a conversation drains.
"""

from __future__ import annotations

import pytest

from nora.engagement.queue import ConversationQueues


def test_fifo_order_within_a_conversation():
    q: ConversationQueues[str, str] = ConversationQueues(maxlen=8)
    q.enqueue("g1", "a")
    q.enqueue("g1", "b")
    q.enqueue("g1", "c")

    assert q.dequeue("g1") == "a"
    assert q.dequeue("g1") == "b"
    assert q.dequeue("g1") == "c"


def test_conversations_are_isolated():
    q: ConversationQueues[str, str] = ConversationQueues(maxlen=8)
    q.enqueue("g1", "a1")
    q.enqueue("g2", "b1")
    q.enqueue("g1", "a2")

    assert q.dequeue("g2") == "b1"      # g2 is untouched by g1's order
    assert q.dequeue("g1") == "a1"
    assert q.dequeue("g1") == "a2"


def test_depth_reflects_only_the_named_conversation():
    q: ConversationQueues[str, str] = ConversationQueues(maxlen=8)
    q.enqueue("g1", "a")
    q.enqueue("g1", "b")
    q.enqueue("g2", "c")

    assert q.depth("g1") == 2
    assert q.depth("g2") == 1
    assert q.depth("g3") == 0           # never-seen key is empty, not a KeyError


def test_a_drained_conversation_retains_nothing():
    """The whole entry is deleted once its last waiting item is dequeued — no
    per-conversation dict entry survives a quiet conversation, so nothing
    accumulates (the `Handler._prefills` leak shape)."""
    q: ConversationQueues[str, str] = ConversationQueues(maxlen=8)
    q.enqueue("g1", "a")
    q.enqueue("g1", "b")

    assert len(q) == 1
    q.dequeue("g1")
    assert len(q) == 1                  # still one item waiting
    q.dequeue("g1")
    assert len(q) == 0                  # drained: the key itself is gone
    assert q.depth("g1") == 0


def test_dequeue_of_an_empty_or_unknown_key_is_none():
    q: ConversationQueues[str, str] = ConversationQueues(maxlen=8)
    assert q.dequeue("g1") is None
    q.enqueue("g1", "a")
    q.dequeue("g1")
    assert q.dequeue("g1") is None      # emptied, key removed, still None not error


def test_the_bound_is_read_from_maxlen_not_a_silent_deque_cap():
    """`depth` is what admission compares against `maxlen` to decide QUEUE_FULL —
    the queue itself never silently drops, so the bound is a visible, counted
    decision made in admission, not a `deque(maxlen=...)` eviction nobody sees."""
    q: ConversationQueues[str, int] = ConversationQueues(maxlen=2)
    q.enqueue("g1", 1)
    q.enqueue("g1", 2)
    # Nothing stops a caller that ignores the bound — proving the queue does not
    # enforce it itself; admission is where the flood is shed.
    q.enqueue("g1", 3)
    assert q.depth("g1") == 3
    assert q.maxlen == 2


def test_maxlen_must_be_at_least_one():
    with pytest.raises(ValueError):
        ConversationQueues(maxlen=0)
