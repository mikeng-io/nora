"""`LessonBook` — the one place a lesson is written, reinforced, retired or rolled
back.

It mirrors `nora.language.character_session.EntitySessions`: write verbs a model
calls, each checked by code AS IT IS MADE and answered with a refusal the model
can act on, so a refused write never reaches the store. Two differences, each
forced by the data:

- **A lesson write commits at once, where a Character revision is staged.** The
  cap is judged against what the unit holds NOW, and a session that retires one
  lesson and then writes another must see the retirement count. A staged write
  would judge the second against a store the first had not reached.
- **The operator reaches the same book.** `retire` and `rollback` take `by`,
  and the CLI (`nora lessons`) calls them. Rolling back a revision appends it as
  a new revision, never an edit, and an operator's retirement is undone the same
  way (`rollback` to an earlier, active revision).

**One model-facing surface: the Lessons session** (L2,
`nora.language.lessons.LessonsAdaptor`), built only under `[lessons] enabled`
(default off). No turn holds these verbs, and nothing renders a lesson until L5.

**The doctrine check (L3) runs last**, when the book is built with one: a separate
instrument call (`nora.language.doctrine_check.DoctrineCheck`) reads the lesson
against the law. A conflict refuses the write and records a tension row for the
operator; an instrument error refuses it and records nothing. The operator's CLI
book is built without one, because it never writes a lesson.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from nora.domain.culture import Citable, Refusal
from nora.domain.doctrine_check import CheckVerdict, Tension
from nora.domain.lessons import (
    Lesson,
    LessonConflictError,
    LessonRevision,
    check_lesson,
    evidence_ceiling,
)
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

#: A lesson id as it appears in a refusal's detail.
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

__all__ = ["LessonBook", "LessonStore"]


class _Check(Protocol):
    """`nora.language.doctrine_check.DoctrineCheck`."""

    @property
    def model(self) -> str: ...

    async def judge(self, lesson: str) -> CheckVerdict: ...


class _Tensions(Protocol):
    """`nora.infra.sql.tension.SqlTensionStore`."""

    async def record(self, tension: Tension) -> str: ...


class LessonStore(Protocol):
    """`nora.infra.sql.lessons.SqlLessonStore`'s surface."""

    async def lessons(self, *, unit: str) -> tuple[Lesson, ...]: ...

    async def get(self, *, lesson_id: str) -> Lesson | None: ...

    async def revisions(self, *, lesson_id: str) -> tuple[LessonRevision, ...]: ...

    async def create(
        self, *, unit: str, body: str, citations: tuple[str, ...], written_at: int,
        established_by: str, instruction_sha256: str | None = ...,
        model: str | None = ...,
    ) -> Lesson: ...

    async def append(
        self, *, lesson_id: str, expected_seq: int, body: str, citations: tuple[str, ...],
        state: str, retired_by: str | None, relearn_after: int | None, note: str | None,
        written_at: int, established_by: str, instruction_sha256: str | None = ...,
        model: str | None = ...,
    ) -> LessonRevision: ...


def _refusal(refusal: Refusal) -> dict[str, object]:
    return {"status": "refused", "reason": refusal.reason, "detail": refusal.detail}


class LessonBook:
    def __init__(
        self, *, store: LessonStore, law: Callable[[], Sequence[str]],
        screen: Callable[[Mapping[str, object]], tuple[str, str] | None],
        clock_ms: Callable[[], int],
        check: _Check | None = None, tensions: _Tensions | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        """`law` returns the text of each LAW rule now, the doctrine's own words
        (`DoctrineStore.law_rules()`, DEC-291). It is a required argument and not
        defaulted to nothing: a book built without the law would pass every
        doctrine echo, and nothing would say so. `screen` is the Dreamer's egress
        predicate (`nora.agent.dream_screen.offence`, R-18/R-28), injected because
        `nora.language` sits below `nora.agent` and may not import it; likewise
        required, for the same reason."""
        self._store = store
        self._law = law
        self._screen = screen
        self._clock = clock_ms
        # The doctrine check (L3) and where its conflicts go: both or neither. A
        # check with nowhere to write a tension would refuse silently; a tension
        # store with no check would never be written. The operator's CLI book
        # has neither: it never writes a lesson.
        if (check is None) != (tensions is None):
            raise ValueError("a LessonBook takes the doctrine check and the tension "
                             "store together, or neither")
        self._check = check
        self._tensions = tensions
        self._metrics = metrics

    # -- reads ---------------------------------------------------------------

    async def active(self, *, unit: str) -> tuple[Lesson, ...]:
        return tuple(x for x in await self._store.lessons(unit=unit) if x.active)

    # -- a model's verbs ---------------------------------------------------------

    def verbs(
        self, *, unit: str, citable: Citable, established_by: str,
        instruction_sha256: str | None = None, model: str | None = None,
        handles: dict[str, str] | None = None,
        on_ok: Callable[[str], None] | None = None,
    ) -> list[Callable[..., Awaitable[dict[str, object]]]]:
        """`lesson_write` and `lesson_retire`, bound to one unit's session.
        `citable` is what code handed the model (`Citable`); provenance is the
        runtime's, never the model's.

        `handles` maps the short name a session was shown (`L1`) to the lesson's
        id, and is extended with every lesson the session writes. A session
        never sees an id: the Dreamer's screen refuses any argument carrying a
        UUID (`DreamerRuntime._screened`), so a verb taking one could never be
        called. `None` passes ids through (the tests' and the CLI's shape).
        `on_ok` is told the verb's name after each call that was not refused."""

        def _resolve(name: str | None) -> str | None:
            if name is None or handles is None:
                return name
            # An unknown handle is passed on as itself and refused as
            # `unknown_lesson` by the book, with the handle in its detail.
            return handles.get(name.strip().upper(), name)

        def _shown(out: dict[str, object], verb: str) -> dict[str, object]:
            if out.get("status") != "ok":
                if handles is None or not isinstance(out.get("detail"), str):
                    return out
                # A refusal names the lesson it is about (`echoes_lesson`,
                # `relearn`) by id. Shown to a session that addresses lessons by
                # handle, the id is advice it cannot follow (the screen refuses
                # it as an argument), so it reads the handle, or, for a lesson
                # it was never shown (an operator's retirement), a description.
                by_id = {i: h for h, i in handles.items()}
                detail = _UUID.sub(
                    lambda m: by_id.get(m.group(0), "a lesson the operator retired"),
                    str(out["detail"]),
                )
                return {**out, "detail": detail}
            if handles is not None:
                lesson_id = str(out["lesson_id"])
                handle = next((h for h, i in handles.items() if i == lesson_id), None)
                if handle is None:
                    handle = f"L{len(handles) + 1}"
                    handles[handle] = lesson_id
                out = {**out, "lesson_id": handle}
            if on_ok is not None:
                on_ok(verb)
            return out

        async def lesson_write(
            body: str, citations: list[str], lesson_id: str | None = None,
        ) -> dict[str, object]:
            """Write one lesson: a practice for how to act in this unit that the
            outcomes of your own sends taught you. Say it short, as one thing to
            do differently, not what the unit is like and not a rule you already
            carry. Pass `lesson_id` to reinforce or reword a lesson you hold (the
            body replaces it) instead of writing a second one.

            Returns `status`: ok with the `lesson_id`, or refused with a `reason`
            and a `detail` saying what to do instead.

            Args:
                body: The lesson, in prose.
                citations: What it rests on, as strings: the `response:<n>` of
                    episodes you were shown (as many different ones as your
                    instructions ask for), and any `n` of a reply someone else
                    wrote. Never only your own messages.
                lesson_id: A lesson you hold, exactly as you were shown it, to
                    revise it. Omit for a new one.
            """
            return _shown(await self.learn(
                unit=unit, citable=citable, body=body,
                citations=tuple(str(c) for c in citations),
                lesson_id=_resolve(lesson_id),
                established_by=established_by, instruction_sha256=instruction_sha256,
                model=model,
            ), "lesson_write")

        async def lesson_retire(lesson_id: str, why: str) -> dict[str, object]:
            """Retire a lesson you hold: it is no longer true or no longer
            useful, or you are folding it into another. It stops being shown;
            its history stays.

            Returns `status`: ok, or refused with a `reason` and a `detail`.

            Args:
                lesson_id: The lesson, exactly as you were shown it.
                why: One short sentence.
            """
            return _shown(await self.retire(
                unit=unit, lesson_id=_resolve(lesson_id) or lesson_id, by="session",
                why=why, established_by=established_by,
                instruction_sha256=instruction_sha256, model=model,
            ), "lesson_retire")

        return [lesson_write, lesson_retire]

    # -- the operations ----------------------------------------------------------

    async def learn(
        self, *, unit: str, citable: Citable, body: str, citations: tuple[str, ...],
        lesson_id: str | None, established_by: str,
        instruction_sha256: str | None = None, model: str | None = None,
    ) -> dict[str, object]:
        held = await self._store.lessons(unit=unit)
        active = [x for x in held if x.active]
        replacing: Lesson | None = None
        if lesson_id is not None:
            replacing = next((x for x in held if x.id == lesson_id), None)
            if replacing is None:
                return self._refused(unit, Refusal(
                    "unknown_lesson", f"{lesson_id!r} is not a lesson of this unit"))
            if not replacing.active:
                return self._refused(unit, Refusal(
                    "not_active", "that lesson is retired; write a new one if the "
                    "evidence is new"))
        # The screen reads everything the model authored, citations included.
        found = self._screen({"body": body, "citations": list(citations)})
        if found is not None:
            reason, _path = found
            return self._refused(unit, Refusal(
                reason, "the lesson carries an identifier or a coordinate; say the "
                "practice without it"))
        refusal = check_lesson(
            body=body, citations=citations, citable=citable, law=list(self._law()),
            active=active,
            retired_by_operator=[x for x in held
                                 if x.head.retired_by == "operator"],
            replacing=replacing,
        )
        if refusal is not None:
            return self._refused(unit, refusal)
        text = body.strip()
        if self._check is not None:
            judged = await self._judged(
                unit=unit, text=text, citations=citations,
                established_by=established_by, instruction_sha256=instruction_sha256,
                model=model,
            )
            if judged is not None:
                return judged
        now = self._clock()
        if replacing is None:
            made = await self._store.create(
                unit=unit, body=text, citations=citations, written_at=now,
                established_by=established_by, instruction_sha256=instruction_sha256,
                model=model,
            )
            return {"status": "ok", "lesson_id": made.id, "revision": 1}
        revision = await self._append(
            unit, lesson_id=replacing.id, expected_seq=replacing.head.seq, body=text,
            citations=citations, state="active",
            retired_by=None, relearn_after=None, note=None, written_at=now,
            established_by=established_by, instruction_sha256=instruction_sha256,
            model=model,
        )
        if isinstance(revision, dict):
            return revision
        return {"status": "ok", "lesson_id": replacing.id, "revision": revision.seq}

    async def _judged(
        self, *, unit: str, text: str, citations: tuple[str, ...], established_by: str,
        instruction_sha256: str | None, model: str | None,
    ) -> dict[str, object] | None:
        """The doctrine check (L3), last and only once every cheap check has
        passed: it is a model call. `None` lets the write go on. A conflict writes
        one tension row for the operator and refuses; an error refuses and writes
        nothing (an instrument error is not a pass)."""
        check, tensions = self._check, self._tensions
        if check is None or tensions is None:
            return None
        verdict = await check.judge(text)
        if verdict.outcome == "consistent":
            return None
        if verdict.outcome != "conflict" or verdict.rule is None:
            return self._refused(unit, Refusal(
                "check_unavailable", "the check that reads a lesson against your rules "
                "did not answer, so nothing was written; try it once more, or stop"))
        rule = verdict.rule
        try:
            await tensions.record(Tension(
            unit=unit, source="lesson", doctrine_file=rule.file, doctrine_line=rule.line,
            doctrine_text=rule.text, would_have_said=text, citations=citations,
            why=verdict.why, written_at=self._clock(), established_by=established_by,
            instruction_sha256=instruction_sha256, model=model,
            check_model=check.model,
            ))
        except Exception:
            # The lesson is still refused: a lost tension row is the
            # operator's loss, never a reason to write what the law refused.
            log.exception("lessons: unit=%s the tension row could not be written", unit)
        else:
            if self._metrics is not None:
                self._metrics.inc("nora_self_tension_total", doctrine_file=rule.file)
        # The file, not the judge's reason: a session told how it was caught
        # is a session taught to reword around it.
        return self._refused(unit, Refusal(
            "loosens_law", f"acting on this would loosen or contradict a rule you keep "
            f"({rule.file}); it is not written. A lesson adds practice only where "
            "your rules are silent"))

    async def retire(
        self, *, unit: str, lesson_id: str, by: str, why: str, established_by: str,
        record_head: int = 0, instruction_sha256: str | None = None,
        model: str | None = None,
    ) -> dict[str, object]:
        """Retire a lesson. `by` is `session` or `operator`; an operator's carries
        `relearn_after`, the newest Record id the lesson rested on or
        `record_head` (the newest Record id now), whichever is higher."""
        if by not in ("session", "operator"):
            raise ValueError(f"a lesson is retired by 'session' or 'operator', not {by!r}")
        lesson = await self._store.get(lesson_id=lesson_id)
        if lesson is None or lesson.unit != unit:
            return self._refused(unit, Refusal(
                "unknown_lesson", f"{lesson_id!r} is not a lesson of this unit"))
        # An operator may set the no-re-learn floor on a lesson a session already
        # retired (one more revision, still retired); nothing else retires twice.
        if not lesson.active and not (by == "operator" and lesson.head.retired_by == "session"):
            return self._refused(unit, Refusal("not_active", "that lesson is already retired"))
        head = lesson.head
        revision = await self._append(
            unit, lesson_id=lesson.id, expected_seq=head.seq, body=head.body,
            citations=head.citations,
            state="retired", retired_by=by,
            relearn_after=(max(evidence_ceiling(head.citations), record_head)
                           if by == "operator" else None),
            note=why.strip()[:500] or None, written_at=self._clock(),
            established_by=established_by, instruction_sha256=instruction_sha256,
            model=model,
        )
        if isinstance(revision, dict):
            return revision
        return {"status": "ok", "lesson_id": lesson.id, "revision": revision.seq}

    async def rollback(
        self, *, unit: str, lesson_id: str, to_seq: int, established_by: str,
    ) -> dict[str, object]:
        """Append revision `to_seq` of a lesson again, as the newest revision.
        Whatever it was (active, or an operator's retirement), it is the head
        now. The operator's act, so the cap is not applied: a rollback that
        restores a lesson over the cap only makes the next write refuse."""
        lesson = await self._store.get(lesson_id=lesson_id)
        if lesson is None or lesson.unit != unit:
            return self._refused(unit, Refusal(
                "unknown_lesson", f"{lesson_id!r} is not a lesson of this unit"))
        target = next((r for r in await self._store.revisions(lesson_id=lesson.id)
                       if r.seq == to_seq), None)
        if target is None:
            return self._refused(unit, Refusal(
                "unknown_lesson", f"lesson {lesson_id} has no revision {to_seq}"))
        if target.seq == lesson.head.seq:
            return self._refused(unit, Refusal(
                "not_active", f"revision {to_seq} is already the head"))
        revision = await self._append(
            unit, lesson_id=lesson.id, expected_seq=lesson.head.seq, body=target.body,
            citations=target.citations,
            state=target.state, retired_by=target.retired_by,
            relearn_after=target.relearn_after,
            note=f"rollback to revision {target.seq}", written_at=self._clock(),
            established_by=established_by,
        )
        if isinstance(revision, dict):
            return revision
        return {"status": "ok", "lesson_id": lesson.id, "revision": revision.seq}

    async def _append(
        self, unit: str, **fields: Any,
    ) -> LessonRevision | dict[str, object]:
        """The store's compare-and-set append, or a `conflict` refusal when the
        lesson's head moved after this write was judged (an operator's
        retirement, or another writer). The write is not retried: it was judged
        against a head that is gone."""
        try:
            return await self._store.append(**fields)
        except LessonConflictError:
            return self._refused(unit, Refusal(
                "conflict", "this lesson changed while you were writing; read it "
                "again before changing it"))

    def _refused(self, unit: str, refusal: Refusal) -> dict[str, object]:
        log.warning("lessons: unit=%s write refused: %s", unit, refusal.reason)
        return _refusal(refusal)
