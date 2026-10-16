"""`SqlLessonStore` — lessons per unit, each a revision chain.

Append-only: `create` inserts a `lesson` and its first revision, `append` adds
the next one. The head is the highest `seq`; nothing is updated or deleted, so
nothing here keeps `row_history` (`nora.infra.sql.history.APPEND_ONLY`).

**`append` takes the next `seq` inside one transaction**, and
`uq_lesson_revision_seq` is what decides a race: two writers on one lesson
cannot both land the same `seq`, the loser raises. The Lessons session runs one
at a time per unit (`DreamerRuntime` serialises per key) and the operator's CLI
is the only other writer, so a collision is rare and loud, never a forked chain.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.lessons import Lesson, LessonConflictError, LessonRevision
from nora.infra.sql import memory_index
from nora.infra.sql.schema import LESSON, LESSON_REVISION

__all__ = ["SqlLessonStore"]


def _revision(row: sa.Row[Any]) -> LessonRevision:
    return LessonRevision(
        id=row.id, lesson_id=row.lesson_id, seq=row.seq, body=row.body,
        citations=tuple(row.citations), state=row.state, retired_by=row.retired_by,
        relearn_after=row.relearn_after, note=row.note, written_at=row.written_at,
        established_by=row.established_by, instruction_sha256=row.instruction_sha256,
        model=row.model,
    )


def _is_uuid(text: str) -> bool:
    """A lesson id comes from a model or the operator's shell; a string that is
    not a UUID names no lesson, and must not reach Postgres as a type error."""
    try:
        UUID(text)
    except ValueError:
        return False
    return True


class SqlLessonStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def lessons(self, *, unit: str) -> tuple[Lesson, ...]:
        """Every lesson of `unit` with its head revision, oldest lesson first
        (active and retired; the caller filters)."""
        top = LESSON_REVISION.alias("top")
        newest = (
            sa.select(sa.func.max(top.c.seq)).where(top.c.lesson_id == LESSON.c.id)
            .scalar_subquery()
        )
        stmt = (
            sa.select(LESSON.c.id.label("lid"), LESSON.c.unit, LESSON.c.created_at,
                      *LESSON_REVISION.c)
            .select_from(LESSON.join(
                LESSON_REVISION,
                sa.and_(LESSON_REVISION.c.lesson_id == LESSON.c.id,
                        LESSON_REVISION.c.seq == newest),
            ))
            .where(LESSON.c.unit == unit)
            .order_by(LESSON.c.created_at, LESSON.c.id)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(Lesson(id=r.lid, unit=r.unit, created_at=r.created_at, head=_revision(r))
                     for r in rows)

    async def get(self, *, lesson_id: str) -> Lesson | None:
        if not _is_uuid(lesson_id):
            return None
        stmt = (
            sa.select(LESSON.c.unit, LESSON.c.created_at, *LESSON_REVISION.c)
            .select_from(LESSON.join(
                LESSON_REVISION, LESSON_REVISION.c.lesson_id == LESSON.c.id))
            .where(LESSON.c.id == lesson_id)
            .order_by(LESSON_REVISION.c.seq.desc()).limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else Lesson(
            id=lesson_id, unit=row.unit, created_at=row.created_at, head=_revision(row))

    async def revisions(self, *, lesson_id: str) -> tuple[LessonRevision, ...]:
        """Every revision of one lesson, newest first."""
        if not _is_uuid(lesson_id):
            return ()
        stmt = (sa.select(LESSON_REVISION).where(LESSON_REVISION.c.lesson_id == lesson_id)
                .order_by(LESSON_REVISION.c.seq.desc()))
        async with self._engine.connect() as conn:
            return tuple(_revision(r) for r in (await conn.execute(stmt)).all())

    async def create(
        self, *, unit: str, body: str, citations: tuple[str, ...], written_at: int,
        established_by: str, instruction_sha256: str | None = None,
        model: str | None = None,
    ) -> Lesson:
        """A new lesson in `unit` and its first revision, in one transaction."""
        async with self._engine.begin() as conn:
            lesson_id = (await conn.execute(
                sa.insert(LESSON).values(unit=unit, created_at=written_at)
                .returning(LESSON.c.id)
            )).scalar_one()
            row = (await conn.execute(
                sa.insert(LESSON_REVISION).values(
                    lesson_id=lesson_id, seq=1, body=body, citations=list(citations),
                    state="active", written_at=written_at, established_by=established_by,
                    instruction_sha256=instruction_sha256, model=model,
                ).returning(*LESSON_REVISION.c)
            )).one()
            await memory_index.project(conn, "lesson", str(lesson_id))
        return Lesson(id=lesson_id, unit=unit, created_at=written_at, head=_revision(row))

    async def append(
        self, *, lesson_id: str, expected_seq: int, body: str, citations: tuple[str, ...],
        state: str,
        retired_by: str | None, relearn_after: int | None, note: str | None,
        written_at: int, established_by: str, instruction_sha256: str | None = None,
        model: str | None = None,
    ) -> LessonRevision:
        """The next revision of an existing lesson, compare-and-set on the head:
        `expected_seq` is the head the caller judged its write against. Raises
        `LookupError` when the lesson is not there and `LessonConflictError` when
        its head is no longer `expected_seq` (or a racing writer took the next
        `seq` first)."""
        try:
            return await self._append(
                lesson_id=lesson_id, expected_seq=expected_seq, body=body,
                citations=citations, state=state, retired_by=retired_by,
                relearn_after=relearn_after, note=note, written_at=written_at,
                established_by=established_by, instruction_sha256=instruction_sha256,
                model=model,
            )
        except sa.exc.IntegrityError as exc:
            if "uq_lesson_revision_seq" not in str(exc):
                raise
            raise LessonConflictError(f"lesson {lesson_id} was revised concurrently") from exc

    async def _append(
        self, *, lesson_id: str, expected_seq: int, body: str, citations: tuple[str, ...],
        state: str, retired_by: str | None, relearn_after: int | None, note: str | None,
        written_at: int, established_by: str, instruction_sha256: str | None,
        model: str | None,
    ) -> LessonRevision:
        async with self._engine.begin() as conn:
            top = (await conn.execute(
                sa.select(sa.func.max(LESSON_REVISION.c.seq))
                .where(LESSON_REVISION.c.lesson_id == lesson_id)
            )).scalar_one()
            if top is None:
                raise LookupError(f"no lesson {lesson_id}")
            if top != expected_seq:
                raise LessonConflictError(f"lesson {lesson_id} is at revision {top}, "
                                          f"not {expected_seq}")
            row = (await conn.execute(
                sa.insert(LESSON_REVISION).values(
                    lesson_id=lesson_id, seq=top + 1, body=body, citations=list(citations),
                    state=state, retired_by=retired_by, relearn_after=relearn_after,
                    note=note, written_at=written_at, established_by=established_by,
                    instruction_sha256=instruction_sha256, model=model,
                ).returning(*LESSON_REVISION.c)
            )).one()
            # A retirement is a revision too (`state='retired'`).
            await memory_index.project(conn, "lesson", lesson_id)
        return _revision(row)
