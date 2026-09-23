"""`nora.domain.turn_deadline` — the enclosing turn's deadline, as a ContextVar.

The property that matters is not the arithmetic but the PROPAGATION: the turn is
bounded by an `asyncio.wait_for`, which runs its coroutine as a task, and a tool
only sees the deadline if the task inherited it. So the load-bearing test runs
the read inside a `wait_for`, exactly where production reads it.
"""

from __future__ import annotations

import asyncio

from nora.domain.turn_deadline import seconds_left, turn_deadline


def test_outside_a_turn_there_is_no_deadline_not_a_zero_one():
    assert seconds_left() is None


async def test_a_tool_inside_the_turns_wait_for_sees_what_is_left():
    async def tool() -> float | None:
        await asyncio.sleep(0.05)
        return seconds_left()

    with turn_deadline(10.0):
        left = await asyncio.wait_for(tool(), timeout=10.0)

    assert left is not None
    assert 9.0 < left < 10.0, left
    assert seconds_left() is None, "the deadline must not outlive its turn"


def test_a_spent_deadline_reads_zero_never_negative():
    with turn_deadline(-5.0):
        assert seconds_left() == 0.0
