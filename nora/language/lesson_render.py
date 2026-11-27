"""The legacy Cognition session's `<lessons>` block and the episode rendering it reads.

The unit's active lessons, oldest first. Tag and fire turns read the graph's lessons
through pass assembly instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from nora.domain.blocks import (
    ContextBlock,
    render_elements,
    render_group,
    render_row_window,
)
from nora.domain.cognition_ground import UNNAMED_ROOM
from nora.domain.composed import compose_body
from nora.domain.culture import Episode
from nora.domain.lessons import Lesson, LessonEvidenceView
from nora.domain.prompt import age

__all__ = ["ActiveLessons", "render_lessons"]


class _Store(Protocol):
    async def lessons(self, *, unit: str, as_of_ms: int | None = None) -> tuple[Lesson, ...]: ...


class ActiveLessons:
    """The bodies of a unit's active lessons, oldest first: what the block
    shows, and nothing a turn could act on beyond them (no ids, no citations)."""

    def __init__(self, store: _Store) -> None:
        self._store = store

    async def bodies(self, *, unit: str, as_of_ms: int | None = None) -> tuple[str, ...]:
        rows = (await self._store.lessons(unit=unit) if as_of_ms is None
                else await self._store.lessons(unit=unit, as_of_ms=as_of_ms))
        return tuple(x.head.body for x in rows if x.active)


def render_lessons(lessons: Sequence[str]) -> ContextBlock | None:
    """One `<lesson>` per active lesson, or `None` with none (an empty block
    would say she had learned nothing here, which is not what no rows means).
    The store caps them (`LESSON_UNIT_MAX_CHARS`), so nothing is cut here."""
    shown = [x.strip() for x in lessons if x.strip()]
    if not shown:
        return None
    return ContextBlock(
        name="lessons",
        body=compose_body(render_elements(shown, tag="lesson", attrs=lambda _x, i: (("n", str(i)),),
                             body=lambda x: x)),
    )




def lesson_episode(
    e: Episode, *, names: Mapping[str, str], labels: Mapping[str, str],
) -> str:
    """One `<episode>`: her send and the replies that quoted it, each an `m`
    with its Record id, inside one element carrying the handle, room, outcome
    and counts. `follows` is a count of unquoted answers (inferred), so it is
    shown apart from the quoted replies."""
    rows = render_row_window(
        [(e.send_id, "you", e.text), *(
            (r.id, names.get(r.person_id or "", "someone"), r.body or "")
            for r in e.replies
        )],
        row_id=lambda m: m[0],
        turn=lambda m: e.turn_handle if m[0] == e.send_id else None,
        attrs=lambda m: (("from", m[1]),),
        body=lambda m: m[2],
    )
    return render_group("episode", [
        ("n", f"response:{e.send_id}"),
        ("room", labels.get(e.room) or UNNAMED_ROOM),
        ("outcome", e.outcome),
        ("reactions", str(e.reactions)),
        ("edits", str(e.edits)),
        ("follows", str(e.follows)),
    ], rows)


def lesson_evidence_blocks(
    view: LessonEvidenceView, *, now: datetime,
) -> list[ContextBlock]:
    """The lesson evidence a live session was handed, as its three declared
    blocks — empty lanes self-close, so a session handed episodes and no
    corrections is not told about a corrections list."""
    out: list[ContextBlock] = []
    if view.held:
        handle_of = {i: h for h, i in view.handles.items()}
        out.append(ContextBlock(
            name="held_lessons",
            body=compose_body(render_elements(
                view.held, tag="lesson",
                attrs=lambda x, _i: (
                    ("id", handle_of.get(x.id) or x.id),
                    ("written", age(x.head.written_at, now)),
                    ("revision", str(x.head.seq)),
                ),
                body=lambda x: x.head.body,
            )),
        ))
    if view.corrections:
        out.append(ContextBlock(
            name="lesson_corrections",
            body=compose_body(render_row_window(
                view.corrections, row_id=lambda m: m.row.id,
                attrs=lambda m: (
                    ("from", view.names.get(m.row.person_id or "", "someone")),
                    ("room", view.labels.get(m.room) or UNNAMED_ROOM),
                ),
                body=lambda m: m.row.body or "",
            )),
        ))
    if view.episodes:
        out.append(ContextBlock(
            name="lesson_episodes",
            body=compose_body("\n".join(
                lesson_episode(e, names=view.names, labels=view.labels)
                for e in view.episodes)),
        ))
    return out
