"""`ConversationSlots` — R-10's single-in-flight lock and R-30's per-conversation
FIFO queue, hoisted out of `EngagementPipeline` into one collaborator.

Acquire (`admission.run`) and release (the pipeline's `finally`) are split
across callers. The tests below pin the contract both acquirers (the pipeline
and the heartbeat fire path) rely on: releasing and dispatching are ONE
operation, and a drained conversation retains nothing.
"""

from __future__ import annotations

import asyncio

from nora.engagement.slots import ConversationSlots

KEY = ("signal", "conv-1")


def _slots(maxlen: int = 16) -> tuple[ConversationSlots, list]:
    spawned: list = []
    slots = ConversationSlots(maxlen=maxlen, spawn=lambda coro: spawned.append(coro))
    return slots, spawned


def test_acquire_then_held_then_release():
    slots, _ = _slots()
    assert slots.held(KEY) is False
    slots.acquire(KEY)
    assert slots.held(KEY) is True
    slots.release_and_dispatch(KEY)
    assert slots.held(KEY) is False


def test_try_acquire_is_false_while_held():
    slots, _ = _slots()
    assert slots.try_acquire(KEY) is True
    assert slots.try_acquire(KEY) is False
    slots.release_and_dispatch(KEY)
    assert slots.try_acquire(KEY) is True


def test_release_dispatches_the_next_factory_and_reacquires():
    slots, spawned = _slots()
    slots.acquire(KEY)
    ran: list[str] = []

    async def _turn(name: str) -> None:
        ran.append(name)

    slots.enqueue(KEY, lambda: _turn("first"))
    slots.enqueue(KEY, lambda: _turn("second"))
    assert slots.depth(KEY) == 2

    slots.release_and_dispatch(KEY)
    assert slots.held(KEY) is True          # re-acquired for the dequeued turn
    assert slots.depth(KEY) == 1
    assert len(spawned) == 1
    asyncio.run(spawned[0])
    assert ran == ["first"]


def test_release_with_empty_queue_leaves_the_slot_free():
    slots, spawned = _slots()
    slots.acquire(KEY)
    slots.release_and_dispatch(KEY)
    assert slots.held(KEY) is False
    assert spawned == []
    assert len(slots) == 0


def test_nothing_is_retained_once_drained():
    slots, spawned = _slots()
    slots.acquire(KEY)

    async def _noop() -> None:
        return None

    slots.enqueue(KEY, _noop)
    slots.release_and_dispatch(KEY)          # dequeues the only item
    slots.release_and_dispatch(KEY)          # that turn finishes, nothing behind it
    assert len(slots) == 0
    assert slots.held(KEY) is False
    for coro in spawned:
        coro.close()
