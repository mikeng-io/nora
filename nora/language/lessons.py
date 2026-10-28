"""The Lessons adaptor: one Dreamer session per unit, woken by closed response
episodes, writing through `lesson_write` and `lesson_retire`.

**The evidence is a closed `response` episode** (`nora.infra.sql.rollups`): one
of her sends with the replies, reactions, edits and unquoted follows inside its
window, or `silent` where an answer was expected and none came. Every CLOSED
episode is read, `unanswered` included: the session is handed each send's
text with its outcome and judges whether an unanswered send asked something.
`open` (its outcome can still change) and `unknown` (an outage, R-27) are never
shown.

**The wake** is the dream loop's count-or-stale rule for an entity key
(`nora.app.runtime._dream_evidence_once`): `[lessons] wake_batch` closed episodes
since the key's watermark, or `stale_seconds` with any new one, or a held lesson
reaching its horizon (`LessonEvidence.count`). A unit with nothing new and no
lesson due never runs.

**Writes land when the verb is called**, unlike a Character revision, which is
staged (`nora.language.lesson_book`, why). So `accept` stages nothing: it judges
whether the session SETTLED (wrote, retired, or closed with
`lessons_no_change`). A session that did none of these holds the watermark, up
to `MAX_CONSECUTIVE_REJECTIONS` times for one batch (DEC-280's bound, the
Character sessions' rule).

**Decay is code, in `commit`**: a lesson whose head is older than
`[lessons] horizon_seconds` after the session ran (it may have reinforced it from
new episodes) is retired. `commit` runs on every run, a blank one too, so a
horizon wake with no new episode still retires what is due.

**A lesson is never shown by id.** The Dreamer's screen refuses any verb
argument carrying a UUID, so each held lesson is shown as `L1`, `L2`, ... and the
book maps the handle back (`LessonBook.verbs(handles=...)`).

**Rendering is separate:** `[lessons] enabled` gates the session, and
`render_lessons` (`nora.language.lesson_render`), gated by `[lessons] render`,
is what shows the held lessons to a prompt.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_elements,
    render_group,
    render_row_window,
)
from nora.domain.culture import MAX_CONSECUTIVE_REJECTIONS, Citable, Episode, RoomMessage
from nora.domain.errors import AccountNotRecordableError
from nora.domain.lessons import LESSON_MIN_EPISODES, LESSONS_OUTCOMES, Lesson, is_correction
from nora.domain.ports import Proposal, VerbProvenance
from nora.domain.prompt import age
from nora.language.character import CharacterRoom
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

__all__ = [
    "LESSONS_ADAPTOR_NAME",
    "LessonEvidence",
    "LessonsAdaptor",
    "LessonsKey",
]

#: The adaptor's `name`: its watermark identity, schedule, budget and grant
#: key, and its objective file.
LESSONS_ADAPTOR_NAME = "lessons"

#: `inputs` holds one site (the unit's lessons); `commit` two (the lessons
#: again, after the session wrote, and a retirement per lesson due).
LESSONS_STORE_CALLS_PER_TICK = 3

_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("lesson_unit", 5, "turn"),
    ("held_lessons", 20, "turn"),
    ("lesson_corrections", 40, "turn"),
    ("lesson_episodes", 50, "turn"),
)

#: The most corrections one session is handed (the newest). A unit that tells
#: her how to act forty times in a week has a conversation to read, not forty
#: corrections; the episodes carry the rest.
CORRECTIONS_MAX = 20
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}


def _at(name: str) -> dict[str, Any]:
    order, key = _ROW[name]
    return {"order": order, "key": key}


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

    async def episode_count(
        self, *, unit: str, after: int, evidence_only: bool = ...,
    ) -> int: ...

    async def episodes(
        self, *, unit: str, after: int, limit: int, evidence_only: bool = ...,
    ) -> tuple[list[Episode], int]: ...


class _Book(Protocol):
    """`nora.language.lesson_book.LessonBook`'s surface this adaptor uses."""

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


def _due(lesson: Lesson, now_ms: int, horizon_ms: int) -> bool:
    """A held lesson not re-evidenced for `horizon_ms`: its head (the last
    write or reinforcement) is that old."""
    return lesson.active and now_ms - lesson.head.written_at >= horizon_ms


class LessonEvidence:
    """The dream loop's count and read for a Lessons key: every closed episode
    of the unit past the watermark (by her send's Record id), and a held lesson
    reaching its horizon, which counts as a full batch so the key wakes and
    `commit` retires it."""

    def __init__(
        self, *, store: _Episodes, book: _Book, keys: Mapping[str, LessonsKey],
        wake_batch: int, horizon_seconds: int,
        clock_ms: Callable[[], int],
        correction_quiet_seconds: int | None = None, stale_seconds: int = 604_800,
        metrics: Metrics | None = None, self_ids: frozenset[str] = frozenset(),
    ) -> None:
        """`correction_quiet_seconds` turns on the correction fast path:
        `None` is off. A correction wakes the key once it is that old, so the
        conversation around it has settled."""
        self._store = store
        self._book = book
        self._keys = keys
        self._wake_batch = wake_batch
        self._horizon_ms = horizon_seconds * 1000
        self._clock = clock_ms
        self._quiet_ms = (None if correction_quiet_seconds is None
                          else correction_quiet_seconds * 1000)
        self._stale_ms = stale_seconds * 1000
        self._metrics = metrics
        #: Her own platform ids, so an @-mention of her addresses her.
        self._self_ids = self_ids
        self._held: dict[str, int] = {}

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

    def held(self, key: str) -> int | None:
        """The watermark the last `newest` read for `key` started after: the
        name of the batch the session in flight was handed."""
        return self._held.get(key)

    async def count(self, *, key: str, after: int) -> int:
        unit = self._keys[key].unit
        n = await self._store.episode_count(unit=unit, after=after, evidence_only=False)
        if n >= self._wake_batch:
            return n
        if await self.corrections(key):
            # The fast path: one correction is enough to look now.
            if self._metrics is not None:
                self._metrics.inc("nora_lessons_correction_wake_total")
            return self._wake_batch
        now = self._clock()
        if any(_due(x, now, self._horizon_ms) for x in await self._book.active(unit=unit)):
            return self._wake_batch
        return n

    async def newest(
        self, *, key: str, after: int, limit: int,
    ) -> tuple[Sequence[Episode | RoomMessage], int]:
        """The newest `limit` closed episodes and the cursor past them, or
        nothing and the cursor UNMOVED when fewer than `LESSON_MIN_EPISODES`
        arrived: no write could cite so few, so a model call on them (a horizon
        or stale wake on one or two) is spent for nothing, and consuming them
        would keep them from the batch they belong to. The run is then blank:
        `commit` still runs the decay, and the watermark's age resets, so a
        short batch is looked at again a `stale_seconds` later, not every tick."""
        self._held[key] = after
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


@dataclass
class _Run:
    """One session in flight: what it may cite, the handles it was shown, and
    what it did."""

    citable: Citable
    handles: dict[str, str]
    batch_start: int | None
    acted: list[str] = field(default_factory=list)
    kept: bool = False


class LessonsAdaptor:
    """One unit's lessons, per wake of its closed episodes."""

    name = LESSONS_ADAPTOR_NAME
    key_kind = "entity"
    uses_watermark = True
    objective = LESSONS_ADAPTOR_NAME
    #: Unread: answers through `verbs`, never an envelope.
    envelope_key = "lessons"
    #: None: the episodes and the lessons held are handed to it whole.
    tools: frozenset[str] = frozenset()
    store_calls_per_tick = LESSONS_STORE_CALLS_PER_TICK

    def __init__(
        self, *, book: _Book, evidence: LessonEvidence, keys: Mapping[str, LessonsKey],
        name_of: Callable[[str | None], str], horizon_seconds: int,
        metrics: Metrics | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._book = book
        #: Read by the dream loop, never awaited here.
        self.evidence = evidence
        self._keys = keys
        #: A reply's author by `person_id` (the roster's name).
        self._name_of = name_of
        self._horizon_ms = horizon_seconds * 1000
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._runs: dict[str, _Run] = {}
        #: What the last settled session of a key landed through its verbs,
        #: handed from `accept` to `commit`'s return so the runtime counts the
        #: run as `ok` (its writes never pass through a `Proposal`).
        self._landed: dict[str, int] = {}
        # In memory, as `EntitySessions._rejections` (its ponytail applies).
        self._rejections: dict[str, tuple[int | None, int]] = {}

    def keys(self) -> Sequence[str]:
        return tuple(sorted(self._keys))

    def _count(self, outcome: str, n: int = 1) -> None:
        if outcome not in LESSONS_OUTCOMES:
            raise ValueError(f"nora_lessons_session_total{{{outcome}}} is outside its "
                             f"vocabulary")
        if self._metrics is not None:
            self._metrics.inc("nora_lessons_session_total", float(n), outcome=outcome)

    # -- inputs ------------------------------------------------------------

    def _episode(self, e: Episode, labels: Mapping[str, str]) -> str:
        """One `<episode>`: her send and the replies that quoted it, each an `m`
        with its Record id, inside one element carrying the handle, room,
        outcome and counts. `follows` is a count of unquoted answers (inferred), so it is shown
        apart from the quoted replies."""
        rows = render_row_window(
            [(e.send_id, "you", e.text), *(
                (r.id, self._name_of(r.person_id), r.body or "") for r in e.replies
            )],
            row_id=lambda m: m[0],
            turn=lambda m: e.turn_handle if m[0] == e.send_id else None,
            attrs=lambda m: (("from", m[1]),),
            body=lambda m: m[2],
        )
        return render_group("episode", [
            ("n", f"response:{e.send_id}"),
            ("room", labels.get(e.room) or "unnamed room"),
            ("outcome", e.outcome),
            ("reactions", str(e.reactions)),
            ("edits", str(e.edits)),
            ("follows", str(e.follows)),
        ], rows)

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        """The unit's rooms, the lessons it holds (by handle, with their age),
        and the closed episodes since the watermark. `""` with no episode: no
        model call, and `commit` still runs the decay."""
        lk = self._keys.get(key)
        episodes = [e for e in batch if isinstance(e, Episode)
                    and e.outcome not in ("open", "unknown")]
        corrections = [m for m in batch if isinstance(m, RoomMessage)]
        if lk is None or not (episodes or corrections):
            return ""
        now = self._clock()
        held = await self._book.active(unit=lk.unit)
        handles = {f"L{i}": x.id for i, x in enumerate(held, start=1)}
        sends = frozenset(e.send_id for e in episodes)
        told = frozenset(m.row.id for m in corrections)
        self._runs[key] = _Run(
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
            handles=handles,
            batch_start=self.evidence.held(key),
        )
        labels = {r.key: r.label for r in lk.rooms}
        handle_of = {i: h for h, i in handles.items()}
        parts: list[ContextBlock | None] = [
            ContextBlock(
                name="lesson_unit", **_at("lesson_unit"), body="",
                when_empty="self_close",
                attrs=(("rooms", ", ".join(r.label or "unnamed room" for r in lk.rooms)),),
            ),
            None if not held else ContextBlock(
                name="held_lessons", **_at("held_lessons"), body=(
                render_elements(
                    held, tag="lesson",
                    attrs=lambda x, _i: (
                        ("id", handle_of[x.id]),
                        ("written", age(x.head.written_at, now)),
                        ("revision", str(x.head.seq)),
                    ),
                    body=lambda x: x.head.body,
                )
            )),
            None if not corrections else ContextBlock(
                name="lesson_corrections", **_at("lesson_corrections"),
                body=render_row_window(
                    corrections, row_id=lambda m: m.row.id,
                    attrs=lambda m: (("from", self._name_of(m.row.person_id)),
                                     ("room", labels.get(m.room) or "unnamed room")),
                    body=lambda m: m.row.body or "",
                ),
            ),
            None if not episodes else ContextBlock(
                name="lesson_episodes", **_at("lesson_episodes"),
                body="\n".join(self._episode(e, labels) for e in episodes),
            ),
        ]
        return assemble([p for p in parts if p is not None], sep="\n\n")

    # -- the verbs ---------------------------------------------------------

    def verbs(
        self, *, key: str, provenance: VerbProvenance,
    ) -> list[Callable[..., Awaitable[dict[str, object]]]]:
        """`lesson_write` and `lesson_retire` from the book, bound to this run,
        and `lessons_no_change`, which settles a session that changes nothing."""
        run = self._runs.get(key)
        unit = self._keys[key].unit

        async def lessons_no_change(why: str) -> dict[str, object]:
            """Close this session with the lessons unchanged. Call this when
            the episodes teach nothing new, or nothing holds across enough of
            them to write.

            Returns `status`: ok.

            Args:
                why: One short sentence: why nothing changes.
            """
            current = self._runs.get(key)
            if current is None:
                return {"status": "unavailable", "reason": "no_session",
                        "detail": "this session is over; nothing was written"}
            current.kept = True
            log.info("lessons: key=%s unchanged: %s", key, why[:200])
            return {"status": "ok"}

        if run is None:
            return [lessons_no_change]
        return [
            *self._book.verbs(
                unit=unit, citable=run.citable,
                established_by=provenance.established_by,
                instruction_sha256=provenance.instruction_sha256,
                model=provenance.model, handles=run.handles, on_ok=run.acted.append,
            ),
            lessons_no_change,
        ]

    # -- accept / commit ---------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]],
    ) -> Sequence[object]:
        """Nothing to stage (every write landed when it was made). Judges
        whether the session SETTLED: it wrote or retired a lesson, or closed
        with `lessons_no_change`. Unsettled RAISES `AccountNotRecordableError`
        (the runtime holds the watermark), unless it is the
        `MAX_CONSECUTIVE_REJECTIONS`-th in a row of the same batch: then it is
        counted `abandoned` and the batch is consumed (DEC-280)."""
        run = self._runs.pop(key, None)
        if run is None or not (run.acted or run.kept):
            log.warning("lessons: key=%s ended without writing, retiring or closing; "
                        "HOLDING the watermark", key)
            self._count("unsettled")
            self._reject(key, None if run is None else run.batch_start)
            return ()
        self._rejections.pop(key, None)
        self._landed[key] = len(run.acted)
        written = run.acted.count("lesson_write")
        retired = run.acted.count("lesson_retire")
        if written:
            self._count("written", written)
        if retired:
            self._count("retired", retired)
        if not run.acted:
            self._count("kept")
        return ()

    def _reject(self, key: str, start: int | None) -> None:
        previous, n = self._rejections.get(key, (start, 0))
        n = n + 1 if previous == start else 1
        if n < MAX_CONSECUTIVE_REJECTIONS:
            self._rejections[key] = (start, n)
            raise AccountNotRecordableError(
                f"the lessons session for key={key} did not settle")
        self._rejections.pop(key, None)
        log.warning("lessons: key=%s unsettled %d times in a row on the batch from %s; "
                    "advancing past it", key, n, start)
        self._count("abandoned")

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> int:
        """The decay: retire every lesson of the unit not re-evidenced within
        the horizon. Runs after the session (which may have reinforced one), and
        on a blank run too. Returns how many rows the run wrote: the session's
        landed writes and retirements, and the decay's."""
        landed = self._landed.pop(key, 0)
        lk = self._keys.get(key)
        if lk is None:
            return landed
        retired = 0
        for lesson in await self._book.active(unit=lk.unit):
            if not _due(lesson, now_ms, self._horizon_ms):
                continue
            out = await self._book.retire(
                unit=lk.unit, lesson_id=lesson.id, by="session",
                why=f"horizon: not re-evidenced in {self._horizon_ms // 86_400_000} days",
                established_by=f"dream:{self.name}:{key}:{now_ms}:horizon",
            )
            if out.get("status") == "ok":
                retired += 1
        if retired:
            self._count("decayed", retired)
        return landed + retired
