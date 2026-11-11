"""The session's lesson seam — the evidence read before the model call, the
verbs offered while it runs and the settle when it closes. All three fail
closed: a failed read is `unavailable` on the blocks it feeds, a failed
settle is logged and the batch re-read next session.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

#: The declared blocks one `shown` read feeds (`cognition_render._BLOCKS`):
#: when that read fails they all render `status="unavailable"`, never empty.
LESSON_BLOCKS = ("held_lessons", "lesson_corrections", "lesson_episodes")

log = logging.getLogger("nora.app.cognition_session")


async def read(
    lessons: Any, *, unit: str, grounding: Any, historical: bool,
    inc: Any,
) -> tuple[Any, Any]:
    """`shown` into the grounding, and the view handed back with it: the closed
    `response` episodes and corrections past the unit's `lessons` cursor, with
    the held lessons by `L` handle and the `citable` its writes are held to.
    Live only — a historical session never reads or writes present-day
    practice — and a failed read is told as `unavailable`, never as an empty
    batch. The caller keeps the view on the run state: verbs cite only what it
    carried and `take` consumes only a session that closed."""
    if lessons is None or historical:
        return grounding, None
    try:
        view = await lessons.shown(unit=unit)
    except Exception:
        log.exception("cognition lesson evidence read failed (unit=%s)", unit)
        inc("nora_errors_total", stage="cognition_lessons")
        return replace(grounding, unavailable=(
            grounding.unavailable | frozenset(LESSON_BLOCKS))), None
    return replace(grounding, lesson_evidence=view), view


def verbs(
    lessons: Any, *, unit: str, state: Any, established_by: str,
    doctrine_sha256: str | None, model: str | None,
) -> tuple[Any, ...]:
    """The lesson verbs a live session is offered. ponytail: history must
    not create present-day practice, so the historical lane is offered none;
    a session whose evidence read failed holds an EMPTY citable, so a write
    can cite nothing and refuses, never slips past unchecked."""
    if lessons is None or state.historical:
        return ()
    from nora.domain.culture import Citable

    return tuple(lessons.verbs(
        unit=unit,
        citable=(
            state.lesson_view.citable if state.lesson_view is not None else Citable()
        ),
        handles=(
            dict(state.lesson_view.handles)
            if state.lesson_view is not None else None
        ),
        established_by=established_by,
        instruction_sha256=doctrine_sha256,
        model=model,
    ))


async def settle(
    lessons: Any, *, unit: str, state: Any, session_id: int,
    historical: bool, now_ms: int, inc: Any,
) -> None:
    """The lessons cursor moves on the CLOSE, never before: a session that
    failed, timed out or was refused keeps its batch for the next one. The
    decay runs on every closed session — a lesson due on a session handed
    nothing still retires. A failed settle is logged, not raised: the
    session closed and its writes stand."""
    if lessons is None or historical:
        return
    try:
        if state.lesson_view is not None:
            await lessons.take(unit=unit, view=state.lesson_view)
        await lessons.decay(
            unit=unit, now_ms=now_ms,
            established_by=f"cognition:{unit}:{session_id}:horizon",
        )
    except Exception:
        log.exception("cognition lesson settle failed (unit=%s)", unit)
        inc("nora_errors_total", stage="cognition_lessons")
