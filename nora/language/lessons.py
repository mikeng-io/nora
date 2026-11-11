"""The lesson evidence a live Cognition session reads, and the cursor it settles.

The writer moved (design `cognition-loop/50 §6`): lessons are written by the
unit's ONE Cognition session, not by a session of their own, so what survives
of the old Dreamer adaptor is the SELECTOR — which closed episodes and
corrections a session is handed (`LessonEvidence.newest`) — plus
`CognitionLessons`, the seam the session runner drives: `shown` reads the
batch past the unit's `lessons` watermark, `take` advances it when that
session closed, and `decay` retires a held lesson its horizon passed without
re-evidence.

**The evidence is a closed `response` episode** (`nora.infra.sql.rollups`): one
of her sends with the replies, reactions, edits and unquoted follows inside its
window, or `silent` where an answer was expected and none came. Every CLOSED
episode is read, `unanswered` included: the session is handed each send's
text with its outcome and judges whether an unanswered send asked something.
`open` (its outcome can still change) and `unknown` (an outage, R-27) are never
shown. A correction addressing her (the fast path, `is_correction`) rides the
same hand.

**The episodes ride along; they do not wake her.** A unit wakes on its ordinary
stimuli and the next session reads whatever closed since the cursor — evidence,
not a stimulus of its own, so nothing invents a second wake path and a quiet
unit loses nothing (episodes accrue). A lesson due for decay still retires,
because `decay` runs on every closed session, not only one that was handed
episodes.

**Writes land when the verb is called** (`nora.language.lesson_book`, why), and
the verbs are offered live only: a historical session never sees them.

**A lesson is never shown by id.** The session addresses a held lesson as
`L1`, `L2`, ... and the book maps the handle back (`LessonBook.verbs(handles=...)`).

**Rendering is separate:** `render_lessons` (`nora.language.lesson_render`),
gated by `[lessons] render`, is what shows the held lessons to a prompt; the
session's own evidence blocks are `nora.language.cognition_render`'s.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from nora.domain.culture import Citable, Episode, RoomMessage
from nora.domain.lessons import (
    LESSON_MIN_EPISODES,
    LESSONS_OUTCOMES,
    Lesson,
    LessonEvidenceView,
    is_correction,
)
from nora.language.character import CharacterRoom
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

__all__ = [
    "LESSONS_ADAPTOR_NAME",
    "CognitionLessons",
    "LessonEvidence",
    "LessonsKey",
]

#: The feature's watermark identity: the `consolidation_watermark` row the
#: old adaptor wrote and `CognitionLessons` now reads and advances, so a
#: deployment's cursor carries over (`last_run_ms` means the same thing).
LESSONS_ADAPTOR_NAME = "lessons"

#: The most corrections one session is handed (the newest). A unit that tells
#: her how to act forty times in a week has a conversation to read, not forty
#: corrections; the episodes carry the rest.
CORRECTIONS_MAX = 20


@dataclass(frozen=True)
class LessonsKey:
    """What a Lessons key names: the unit (its key IS the unit, the lesson
    store's own partition) and its rooms, for their labels."""

    unit: str
    rooms: tuple[CharacterRoom, ...]


class _Episodes(Protocol):
    """`nora.infra.sql.character.SqlCharacterEvidence`'s episode and
    correction reads."""

    async def addressed_since(
        self, *, rooms: Sequence[tuple[str, str]], since_ms: int, until_ms: int,
        limit: int, self_ids: frozenset[str] = ...,
    ) -> list[RoomMessage]: ...

    async def last_run_ms(self, *, adaptor: str, key: str) -> int | None: ...

    async def episodes(
        self, *, unit: str, after: int, limit: int, evidence_only: bool = ...,
    ) -> tuple[list[Episode], int]: ...


class _Book(Protocol):
    """`nora.language.lesson_book.LessonBook`'s surface this seam uses."""

    async def active(self, *, unit: str) -> tuple[Lesson, ...]: ...

    def verbs(
        self, *, unit: str, citable: Citable, established_by: str,
        instruction_sha256: str | None = ..., model: str | None = ...,
        handles: dict[str, str] | None = ...,
        on_ok: Callable[[str], None] | None = ...,
    ) -> list[Callable[..., Awaitable[dict[str, object]]]]: ...

    async def retire(
        self, *, unit: str, lesson_id: str, by: str, why: str, established_by: str,
        record_head: int = ..., instruction_sha256: str | None = ...,
        model: str | None = ...,
    ) -> dict[str, object]: ...


class _Cursor(Protocol):
    """The `consolidation_watermark` store (`SqlLanguageStore`): where the
    unit's `lessons` cursor lives."""

    async def watermark_for(self, *, consolidator_name: str, scope_id: str) -> int: ...

    async def advance_watermark(
        self, *, consolidator_name: str, scope_id: str, last_message_id: int,
    ) -> None: ...


def _due(lesson: Lesson, now_ms: int, horizon_ms: int) -> bool:
    """A held lesson not re-evidenced for `horizon_ms`: its head (the last
    write or reinforcement) is that old."""
    return lesson.active and now_ms - lesson.head.written_at >= horizon_ms


class LessonEvidence:
    """Which episodes and corrections a session is handed for a Lessons key:
    every closed episode of the unit past the watermark (by her send's Record
    id), and the corrections the fast path picked."""

    def __init__(
        self, *, store: _Episodes, book: _Book, keys: Mapping[str, LessonsKey],
        clock_ms: Callable[[], int],
        correction_quiet_seconds: int | None = None, stale_seconds: int = 604_800,
        self_ids: frozenset[str] = frozenset(),
    ) -> None:
        """`correction_quiet_seconds` turns on the correction fast path:
        `None` is off. A correction is handed in once it is that old, so the
        conversation around it has settled."""
        self._store = store
        self._book = book
        self._keys = keys
        self._clock = clock_ms
        self._quiet_ms = (None if correction_quiet_seconds is None
                          else correction_quiet_seconds * 1000)
        self._stale_ms = stale_seconds * 1000
        #: Her own platform ids, so an @-mention of her addresses her.
        self._self_ids = self_ids

    async def corrections(self, key: str) -> list[RoomMessage]:
        """Messages in the unit's rooms that address her and read as an
        instruction about how she acts (`is_correction`), sent after her last
        session here minus the quiet (what that session could not yet see) and
        at least the quiet ago. At most a `stale_seconds` back, so a unit's
        first session is not handed its whole history. Empty when the fast
        path is off.

        **Known gap:** a correction sent during the minutes a session ran, in
        its quiet window, is older than the next lower bound and is not fast-
        pathed; it still reaches a session through the episodes it is part of."""
        if self._quiet_ms is None:
            return []
        lk = self._keys[key]
        now = self._clock()
        last = await self._store.last_run_ms(adaptor=LESSONS_ADAPTOR_NAME, key=key)
        since = max(now - self._stale_ms, (last or 0) - self._quiet_ms)
        found = await self._store.addressed_since(
            rooms=[(r.platform, r.key) for r in lk.rooms], since_ms=since,
            until_ms=now - self._quiet_ms, limit=CORRECTIONS_MAX * 5,
            self_ids=self._self_ids,
        )
        return [m for m in found if is_correction(m.row.body)][-CORRECTIONS_MAX:]

    async def newest(
        self, *, key: str, after: int, limit: int,
    ) -> tuple[Sequence[Episode | RoomMessage], int]:
        """The newest `limit` closed episodes and the cursor past them, or
        nothing and the cursor UNMOVED when fewer than `LESSON_MIN_EPISODES`
        arrived: no write could cite so few, so the session is shown none —
        what one or two outcomes carry is a guess, not a practice — and
        consuming them would keep them from the batch they belong to. The
        session is then handed none: `decay` still runs, and the episodes
        wait whole for the next one."""
        found, cursor = await self._store.episodes(
            unit=self._keys[key].unit, after=after, limit=limit, evidence_only=False,
        )
        corrections = await self.corrections(key)
        if corrections:
            # A correction is enough on its own; the episodes beside it
            # are context, however few.
            handed: list[Episode | RoomMessage] = [*found, *corrections]
            return handed, cursor
        if len(found) < LESSON_MIN_EPISODES:
            return [], after
        return found, cursor

    async def session_view(
        self, *, key: str, after: int, limit: int,
        name_of: Callable[[str | None], str],
    ) -> LessonEvidenceView:
        """`newest` resolved into the typed view one session is handed: the
        held lessons by `L` handle, the names and room labels the render
        speaks in, and `citable` — built from the SAME items, so a write can
        cite only what this session was shown."""
        items, cursor = await self.newest(key=key, after=after, limit=limit)
        # A store is told to hand over neither `open` nor `unknown`; drop them
        # anyway if one arrives (the contract the type declares).
        episodes = tuple(e for e in items if isinstance(e, Episode)
                         and e.outcome not in ("open", "unknown"))
        corrections = tuple(m for m in items if isinstance(m, RoomMessage))
        lk = self._keys[key]
        held = await self._book.active(unit=lk.unit)
        handles = {f"L{i}": x.id for i, x in enumerate(held, start=1)}
        names: dict[str, str] = {}
        for m in corrections:
            if m.row.person_id:
                names[m.row.person_id] = name_of(m.row.person_id)
        for e in episodes:
            for r in e.replies:
                if r.person_id:
                    names.setdefault(r.person_id, name_of(r.person_id))
        sends = frozenset(e.send_id for e in episodes)
        told = frozenset(m.row.id for m in corrections)
        return LessonEvidenceView(
            episodes=episodes, corrections=corrections, held=held,
            handles=handles, names=names,
            labels={r.key: r.label for r in lk.rooms},
            citable=Citable(
                messages=(sends | told
                          | frozenset(r.id for e in episodes for r in e.replies)),
                nora=sends,
                # Every shown episode, `unanswered` included: whether one asked
                # something is the session's judgement. Her send cited
                # as a bare message id is still her own words (the echo rule).
                episodes=sends,
                echo_rule=True,
                # A lesson is a pattern across many outcomes, never one.
                # Not lowered to what was shown: a stale wake on two episodes
                # may retire or close, but not induce.
                min_episodes=LESSON_MIN_EPISODES,
                # A lesson citing a correction needs no three episodes.
                corrections=told,
            ),
            cursor=cursor,
        )


class CognitionLessons:
    """The lesson work of a live Cognition session, as the three calls the
    session runner drives. `shown`, before the model call, hands over the
    closed episodes, corrections and held lessons past the unit's `lessons`
    cursor, with the `citable` a write is held to. `take` advances that cursor
    — only after the session CLOSED: a failed, timed-out or refused session
    keeps its batch for the next one. `decay` retires each held lesson the
    horizon ran out on, on every closed session (the old adaptor's `commit`
    ran it on a blank run too; a session handed nothing still retires what is
    due). The verbs themselves are `verbs`, the book's own checked pair."""

    def __init__(
        self, *, evidence: LessonEvidence, book: _Book,
        keys: Mapping[str, LessonsKey], cursor: _Cursor,
        batch_limit: int, horizon_seconds: int,
        name_of: Callable[[str | None], str],
        clock_ms: Callable[[], int], metrics: Metrics | None = None,
    ) -> None:
        self._evidence = evidence
        self._book = book
        self._keys = keys
        self._cursor = cursor
        self._batch_limit = batch_limit
        self._horizon_ms = horizon_seconds * 1000
        #: A reply's or correction's author by `person_id` (the roster's name).
        self._name_of = name_of
        self._clock = clock_ms
        self._metrics = metrics

    def _key_for(self, unit: str) -> str:
        if unit not in self._keys:
            raise LookupError(f"no lessons key for unit {unit!r}")
        return unit

    async def shown(self, *, unit: str) -> LessonEvidenceView:
        """The unit's evidence past its `lessons` watermark, as one view."""
        key = self._key_for(unit)
        after = await self._cursor.watermark_for(
            consolidator_name=LESSONS_ADAPTOR_NAME, scope_id=key)
        view = await self._evidence.session_view(
            key=key, after=after, limit=self._batch_limit, name_of=self._name_of)
        if view.corrections and self._metrics is not None:
            self._metrics.inc("nora_lessons_correction_wake_total")
        return view

    def verbs(
        self, *, unit: str, citable: Citable, handles: dict[str, str] | None,
        established_by: str, instruction_sha256: str | None, model: str | None,
    ) -> list[Callable[..., Awaitable[dict[str, object]]]]:
        """`lesson_write` and `lesson_retire`, bound to this session's
        `citable` (what it was SHOWN, or empty when the read failed, so a
        citation is refused rather than unchecked)."""
        metrics = self._metrics

        def _count(verb: str) -> None:
            if metrics is not None:
                metrics.inc("nora_lessons_session_total",
                            outcome=("written" if verb == "lesson_write"
                                     else "retired"))

        return self._book.verbs(
            unit=unit, citable=citable, established_by=established_by,
            instruction_sha256=instruction_sha256, model=model,
            handles=handles,
            on_ok=_count if metrics is not None else None,
        )

    async def take(self, *, unit: str, view: LessonEvidenceView) -> None:
        """Move the unit's `lessons` cursor past what the closed session was
        shown. A short batch's cursor is unmoved (`newest`), so this is a
        no-op write then, not a skip."""
        await self._cursor.advance_watermark(
            consolidator_name=LESSONS_ADAPTOR_NAME,
            scope_id=self._key_for(unit), last_message_id=view.cursor)

    async def decay(self, *, unit: str, now_ms: int, established_by: str) -> int:
        """Retire every lesson of the unit not re-evidenced within the
        horizon. Runs after the session (which may have reinforced one), and
        on a session handed nothing too. Returns how many retired."""
        retired = 0
        for lesson in await self._book.active(unit=unit):
            if not _due(lesson, now_ms, self._horizon_ms):
                continue
            out = await self._book.retire(
                unit=unit, lesson_id=lesson.id, by="session",
                why=f"horizon: not re-evidenced in {self._horizon_ms // 86_400_000} days",
                established_by=established_by,
            )
            if out.get("status") == "ok":
                retired += 1
        if retired and self._metrics is not None:
            if "decayed" not in LESSONS_OUTCOMES:
                raise ValueError("decayed is outside the outcome vocabulary")
            self._metrics.inc(
                "nora_lessons_session_total", float(retired), outcome="decayed")
        return retired
