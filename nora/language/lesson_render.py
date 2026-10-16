"""The `<lessons>` block: what she has learned to do differently in this unit.

Rendered on tag and fire turns directly after `<self>`, both after the doctrine,
so the doctrine is read first and wins.
Every lesson in it passed the doctrine check (DEC-298) when it was written.

The read is the unit's ACTIVE lessons, oldest first, bounded and fail-soft like
`read_culture`: a failed read renders no block and is counted under
`nora_errors_total{stage}`. Whether a room may show them is the caller's, by
`Policy.shows_self_in` (R-31): lessons are written from her sends and their
responses in every room of the unit, location rooms included, exactly as her
own account is (DEC-292).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from typing import Protocol

from nora.domain.blocks import ContextBlock, render_elements
from nora.domain.lessons import Lesson
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

__all__ = ["ActiveLessons", "read_lessons", "render_lessons"]

#: The read's wall clock: `read_culture`'s default, for the same slot it holds.
DEFAULT_LESSONS_READ_TIMEOUT_SECONDS = 2.0


class _Store(Protocol):
    async def lessons(self, *, unit: str) -> tuple[Lesson, ...]: ...


class ActiveLessons:
    """The bodies of a unit's active lessons, oldest first: what the block
    shows, and nothing a turn could act on beyond them (no ids, no citations)."""

    def __init__(self, store: _Store) -> None:
        self._store = store

    async def bodies(self, *, unit: str) -> tuple[str, ...]:
        return tuple(x.head.body for x in await self._store.lessons(unit=unit) if x.active)


async def read_lessons(
    read: Awaitable[tuple[str, ...]] | None, *, stage: str, platform: str,
    metrics: Metrics | None,
    timeout_seconds: float = DEFAULT_LESSONS_READ_TIMEOUT_SECONDS,
) -> tuple[str, ...]:
    """The caller's `ActiveLessons.bodies(...)` call, bounded; `()` with no
    reader, no lesson, or a failed read (only the last is counted)."""
    if read is None:
        return ()
    try:
        return await asyncio.wait_for(read, timeout=timeout_seconds)
    except Exception:
        log.exception("%s: lessons read failed; continuing without it", stage)
        if metrics is not None:
            metrics.inc("nora_errors_total", stage=stage, platform=platform)
        return ()


def render_lessons(lessons: Sequence[str]) -> ContextBlock | None:
    """One `<lesson>` per active lesson, or `None` with none (an empty block
    would say she had learned nothing here, which is not what no rows means).
    The store caps them (`LESSON_UNIT_MAX_CHARS`), so nothing is cut here."""
    shown = [x.strip() for x in lessons if x.strip()]
    if not shown:
        return None
    return ContextBlock(
        name="lessons",
        body=render_elements(shown, tag="lesson", attrs=lambda _x, i: (("n", str(i)),),
                             body=lambda x: x),
    )
