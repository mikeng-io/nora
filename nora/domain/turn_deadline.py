"""The wall-clock deadline of the agent turn this code is running inside.

A turn runs under ONE `asyncio.wait_for` — `[llm].timeout_seconds` on the tag
path, `[heartbeat].timeout_seconds` on a fire turn — and when it fires the turn
dies SILENTLY (`invocation_timeout`, R-5). A tool that spends wall clock on a
model call of its own has to know how much of that budget is LEFT, not only
how much one call may take, because calls the model makes in SEPARATE turns of
the loop add up. The vision describer's per-call ceiling
(`nora.infra.vision.client.derive_vision_timeout`) bounds one invocation; three
`media_fetch` reads issued one after another at 120 s each are 360 s, the whole
of a 360 s loop (DEC-197).

A `ContextVar`, set around the `wait_for` that bounds the turn: `wait_for`
wraps the coroutine in a task, a task copies the context it is created in, and
every tool Strands runs inside that turn inherits it. Code running OUTSIDE any
turn — ingest comprehension, the Dreamer's own passes — sees `None` and keeps
its own ceilings unchanged.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

_DEADLINE: ContextVar[float | None] = ContextVar("nora_turn_deadline", default=None)


@contextmanager
def turn_deadline(seconds: float) -> Generator[None]:
    """Mark everything started inside this block as bounded by `seconds`.

    Enter it around the `asyncio.wait_for(..., timeout=seconds)` that bounds
    the turn, with the SAME number, so the deadline a tool reads and the one
    that kills the turn cannot disagree.
    """
    token = _DEADLINE.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def seconds_left() -> float | None:
    """How long the enclosing turn has before its `wait_for` fires.

    `None` outside a turn — "no turn bounds this", which is not the same as
    "no time left" and must not be read as zero. Never negative.
    """
    deadline = _DEADLINE.get()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())
