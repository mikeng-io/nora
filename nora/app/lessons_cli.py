"""`nora lessons` — the operator's view of, and rollback for, Lessons.

`list` and `show` only read, through the read-only engine every report here uses.
`retire` and `rollback` WRITE (one `lesson_revision` row each, through
`LessonBook`, the same book a session's verbs use) and are a dry run unless
`--apply`, like `nora memory rebuild --dry-run`. Nothing is ever deleted:

- `retire` appends a retirement by the operator. A lesson the operator retired
  is not learned again from outcomes no newer than the retirement
  (`LessonRevision.relearn_after`); the newest Record id is read here.
- `rollback --to N` appends revision N again as the newest revision, so it is
  how a retirement is undone as well as how a reworded lesson is put back.
"""

from __future__ import annotations

from collections.abc import Callable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.doctrine_check import Tension
from nora.domain.lessons import Lesson, LessonRevision
from nora.domain.prompt import one_line
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.schema import MESSAGES
from nora.language.lesson_book import LessonBook

__all__ = [
    "OPERATOR", "book_for", "record_head", "render_chain", "render_list", "render_tensions",
]

#: What an operator's revision names as its writer.
OPERATOR = "operator:cli"


def book_for(engine: AsyncEngine, clock_ms: Callable[[], int]) -> LessonBook:
    """A book for the operator's two writes. `law` and `screen` are the checks a
    SESSION's `lesson_write` runs; `retire` and `rollback` run neither, so they
    are empty here and must never be reused to build a book that writes."""
    return LessonBook(
        store=SqlLessonStore(engine), law=lambda: (), screen=lambda _item: None,
        clock_ms=clock_ms,
    )


async def record_head(engine: AsyncEngine) -> int:
    """The newest Record id now (0 when empty): what an operator's retirement
    says is older than it."""
    async with engine.connect() as conn:
        return int((await conn.execute(sa.select(sa.func.max(MESSAGES.c.id)))).scalar() or 0)


def _line(lesson: Lesson) -> str:
    h = lesson.head
    who = f" by {h.retired_by}" if h.retired_by else ""
    return (f"{lesson.id}  unit={lesson.unit}  rev={h.seq}  {h.state}{who}  "
            f"cites={len(h.citations)}  {one_line(h.body)}")


def render_list(unit: str, lessons: tuple[Lesson, ...], *, everything: bool) -> list[str]:
    shown = [x for x in lessons if everything or x.active]
    if not shown:
        return [f"no {'lessons' if everything else 'active lessons'} in unit {unit}"]
    return [_line(x) for x in shown]


def render_chain(lesson: Lesson, chain: tuple[LessonRevision, ...]) -> list[str]:
    out = [f"lesson {lesson.id}  unit={lesson.unit}  head=rev {lesson.head.seq} "
           f"({lesson.head.state})"]
    for r in chain:
        who = f" by {r.retired_by}" if r.retired_by else ""
        out.append(f"  rev {r.seq}  {r.state}{who}  written_at={r.written_at}  "
                   f"by={r.established_by}")
        out.append(f"    cites: {', '.join(r.citations)}")
        if r.note:
            out.append(f"    note: {r.note}")
        out.append(f"    {one_line(r.body)}")
    return out


def render_tensions(tensions: tuple[Tension, ...]) -> list[str]:
    """Each refused lesson, the law rule it pulled against (file and line), the
    check's reason and the evidence it cited, newest first."""
    if not tensions:
        return ["no tensions"]
    out: list[str] = []
    for t in tensions:
        out.append(f"{t.id}  unit={t.unit}  written_at={t.written_at}  "
                   f"law={t.doctrine_file}:{t.doctrine_line}  check={t.check_model}")
        out.append(f"    would have said: {one_line(t.would_have_said)}")
        out.append(f"    law: {one_line(t.doctrine_text)}")
        out.append(f"    why: {one_line(t.why)}")
        out.append(f"    cites: {', '.join(t.citations)}")
    return out
