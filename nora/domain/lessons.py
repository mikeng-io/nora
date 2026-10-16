"""A lesson: practice induced from the outcomes of many of her acts, kept per
unit as a revision chain.

The types and the pure checks. Nothing here touches a store, a model or a
clock: `check_lesson` is handed everything it judges, so every refusal is
testable without a database. The verbs that apply it are
`nora.language.lesson_book.LessonBook`; the table is
`nora.infra.sql.lessons.SqlLessonStore`.

**One lesson is one chain of revisions, never overwritten and never deleted.**
Reinforcing, rewording and retiring a lesson each append a revision; the head
(the highest `seq`) is what it says now. A retirement is a revision whose
`state` is `retired`, so an operator's rollback is one more revision that
copies an earlier one back (the same shape as Character's chain, DEC-183, but
a lesson, unlike an account, does retire: practice that stopped being true
must be able to stop being rendered).

**What a write must clear** (`check_lesson`), each a `LESSON_REFUSALS` reason
the model reads with a `detail` saying what to do instead:

- the citation check, reused from `character_revise` (`check_revision`): at
  least one citation, each a Record id or `response:<n>` episode the session
  was handed, and not only her own sends (the echo rule on evidence);
- the identifier screen (R-18/R-28), the Dreamer's own predicate;
- the doctrine echo and the lesson echo (`echoes`, defined once, below);
- no re-learn: an operator's retirement is not undone by the same outcomes;
- the cap, per lesson and per unit.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from nora.domain.culture import Citable, Refusal, check_revision

__all__ = [
    "ECHO_COVERAGE",
    "ECHO_GRAM",
    "LESSONS_OUTCOMES",
    "LESSON_BODY_MAX_CHARS",
    "LESSON_BODY_MIN_CHARS",
    "LESSON_MAX_ACTIVE",
    "LESSON_MIN_EPISODES",
    "LESSON_REFUSALS",
    "LESSON_UNIT_MAX_CHARS",
    "NAMES_HER",
    "Lesson",
    "LessonConflictError",
    "LessonRevision",
    "check_lesson",
    "echoes",
    "evidence_ceiling",
    "is_correction",
]

#: **Sizing.** A starting unit is about 800 tokens, roughly 700 characters of
#: the dense Cantonese this deployment writes (`OUTPUT_TOKENS_PER_CHAR`, 1.15),
#: which is two lessons. These caps are about three times that, on purpose: a
#: cap is a refusal threshold, never a reservation, and a cap inside the
#: emission range makes a coin toss of every write. The lessons block is
#: prefill on every turn, so its cost bounds how far the caps can grow.
#: Re-size on measured lessons, never on one sample.
LESSON_BODY_MAX_CHARS = 400

#: The least a lesson says, in NORMALISED characters (letters and digits, see
#: `ECHO_GRAM`). Below one echo run the echo test compares a fragment, so a
#: few characters or bare punctuation would pass it trivially. 12 is two runs
#: and a clause; a short Cantonese practice is 12 or more.
LESSON_BODY_MIN_CHARS = 12


class LessonConflictError(Exception):
    """The lesson's head moved between a writer's read and its append (the
    operator retired it, or another writer revised it): the write was judged
    against a head that is no longer the head, so it is not made."""

#: Different episodes a Lessons session's write must cite (`Citable.min_episodes`):
#: a lesson is a pattern across many outcomes, and one episode is one data
#: point. Three is Character · Nora's floor
#: (`NORA_MIN_EVIDENCE_EPISODES`) for the same reason; unlike hers it is not
#: lowered to what a stale wake showed, because a lesson changes what she DOES.
LESSON_MIN_EPISODES = 3

#: `nora_lessons_session_total`'s `outcome`: `written` and `retired` count
#: verb calls that landed, `kept` a session closed with `lessons_no_change`,
#: `unsettled` a session that did none of those (the batch is held),
#: `abandoned` the third unsettled run of one batch (it advances; DEC-280), and
#: `decayed` a lesson retired by code at its horizon.
LESSONS_OUTCOMES = frozenset({
    "written", "retired", "kept", "unsettled", "abandoned", "decayed",
})

#: Active lessons a unit holds at once, and the characters they hold together.
LESSON_MAX_ACTIVE = 12
LESSON_UNIT_MAX_CHARS = 2_400

#: The echo test, defined once. Both texts are NORMALISED (case-folded, every
#: character that is not a letter or digit dropped, so spacing, punctuation and
#: a re-wrap change nothing, and CJK, whose characters are letters, is kept),
#: then cut into overlapping runs of `ECHO_GRAM` characters. A lesson ECHOES a
#: text when at least `ECHO_COVERAGE` of the lesson's runs also occur in that
#: text: it adds almost nothing the text did not already say. Five characters is
#: a phrase in Cantonese and a word or two in English, so a lesson that merely
#: quotes a rule (or a sibling lesson) is caught and one that shares a topic
#: word is not. A lesson shorter than one run is compared whole.
#:
#: This is a restatement test, not a contradiction test: a lesson that CONTRADICTS
#: law shares no runs with it and is the doctrine check's job.
ECHO_GRAM = 5
ECHO_COVERAGE = 0.8

#: Why one `lesson_write` or `lesson_retire` call was refused, as the `reason`
#: the model reads. The first five are `check_revision`'s:
#: `empty`, `uncited`, `unresolved_citation`, `echo` (every citation her own
#: send), `thin_evidence` (unused here: `min_episodes` is 0), and
#: `too_long` (this module's own, per lesson).
LESSON_REFUSALS = frozenset({
    "empty", "too_long", "uncited", "unresolved_citation", "echo", "thin_evidence",
    "identifier", "coordinate",
    "echoes_doctrine", "echoes_lesson", "relearn", "cap", "too_short", "conflict",
    "unknown_lesson", "not_active", "loosens_law", "check_unavailable",
})


@dataclass(frozen=True, slots=True)
class LessonRevision:
    """One revision of one lesson. `seq` counts from 1 and is the chain: the
    head is the highest."""

    id: str
    lesson_id: str
    seq: int
    body: str
    #: Record message ids (`"4812"`) and `response:<send id>` episodes, as
    #: `check_revision` accepted them.
    citations: tuple[str, ...]
    #: `active`, or `retired` (then `retired_by` names who).
    state: str
    retired_by: str | None
    #: Set on an OPERATOR retirement alone: the newest Record id the lesson was
    #: learned from or that existed when the operator retired it. A new lesson
    #: whose evidence is all at or below it is a re-learn (`check_lesson`).
    relearn_after: int | None
    #: Why this revision exists, in a sentence (a retirement's reason, a
    #: rollback's target). Free text, never rendered to a model.
    note: str | None
    written_at: int
    established_by: str
    instruction_sha256: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class Lesson:
    """A lesson and its head revision."""

    id: str
    unit: str
    created_at: int
    head: LessonRevision

    @property
    def active(self) -> bool:
        return self.head.state == "active"


def _norm(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _grams(text: str) -> frozenset[str]:
    norm = _norm(text)
    if len(norm) <= ECHO_GRAM:
        return frozenset({norm}) if norm else frozenset()
    return frozenset(norm[i:i + ECHO_GRAM] for i in range(len(norm) - ECHO_GRAM + 1))


def echoes(lesson: str, other: str) -> bool:
    """Whether `lesson` restates `other` (see `ECHO_GRAM`, `ECHO_COVERAGE`)."""
    mine = _grams(lesson)
    if not mine:
        return False
    return len(mine & _grams(other)) / len(mine) >= ECHO_COVERAGE


def _ids(citations: Iterable[str]) -> list[int]:
    return [int(m.group()) for c in citations if (m := re.search(r"\d+", c)) is not None]


def evidence_ceiling(citations: Iterable[str]) -> int:
    """The newest Record id among `citations` (0 when none): an episode is
    `response:<her send's id>`, a message is its id, and both are Record ids."""
    return max(_ids(citations), default=0)


_CITATION_DETAILS = {
    "unresolved_citation": "cite only the `response:<n>` of an episode you were "
                           "shown, or the `n` of a reply someone else wrote",
    "echo": "every citation is one of your own messages; cite the `response:<n>` "
            "episodes that show how people responded, or did not",
    "thin_evidence": "a lesson is a pattern: cite at least {n} different "
                     "`response:<n>` episodes you were shown",
    "ids_in_body": "the lesson's text carries message numbers; keep them in "
                   "`citations` only and say the practice in words",
}


def check_lesson(
    *, body: str, citations: tuple[str, ...], citable: Citable,
    law: Sequence[str], active: Sequence[Lesson], retired_by_operator: Sequence[Lesson],
    replacing: Lesson | None,
) -> Refusal | None:
    """`None` when this lesson may be written, else why not.

    `active` is the unit's active lessons now and `replacing` the one this write
    revises (`None`: a new lesson). `retired_by_operator` is every lesson whose
    head is an operator retirement (`LessonRevision.relearn_after`). The
    identifier screen is the caller's (it is the Dreamer's predicate and this
    module is pure); it runs before this.
    """
    text = body.strip()
    if not text:
        return Refusal("empty", "the lesson is empty; say what to do differently, or "
                       "do not write one")
    if len(text) > LESSON_BODY_MAX_CHARS:
        return Refusal("too_long", f"the lesson is {len(text)} characters, past the "
                       f"{LESSON_BODY_MAX_CHARS} cap; one practice, said short")
    if len(_norm(text)) < LESSON_BODY_MIN_CHARS:
        return Refusal("too_short", f"the lesson is under {LESSON_BODY_MIN_CHARS} letters "
                       "and digits; say what to do differently in a clause")
    refused = check_revision(body=text, citations=citations, citable=citable)
    if refused is not None:
        # `check_revision`'s details are written for Character · Nora, who may
        # cite only episodes someone responded to. A Lessons session may cite
        # every episode it was shown (`unanswered` too), so its refusal says that instead.
        detail = _CITATION_DETAILS.get(refused.reason)
        return refused if detail is None else Refusal(refused.reason, detail.format(
            n=citable.min_episodes))
    for rule in law:
        if echoes(text, rule):
            return Refusal("echoes_doctrine", "this restates a law you already carry; a "
                           "lesson is what the law leaves open. Write what is different "
                           "in this unit, or write nothing")
    others = [lesson for lesson in active if replacing is None or lesson.id != replacing.id]
    for lesson in others:
        if echoes(text, lesson.head.body):
            return Refusal("echoes_lesson", f"this restates lesson {lesson.id} you already "
                           "hold; reinforce that one (pass its id) instead of writing it twice")
    if replacing is None:
        newest = evidence_ceiling(citations)
        for lesson in retired_by_operator:
            floor = lesson.head.relearn_after or 0
            if newest <= floor:
                return Refusal("relearn", f"the operator retired lesson {lesson.id}, and "
                               "everything you cite is no newer than that; a lesson the "
                               "operator removed is only learned again from outcomes after it")
    held = sum(len(lesson.head.body) for lesson in others)
    if replacing is None and len(others) >= LESSON_MAX_ACTIVE:
        return Refusal("cap", f"the unit holds {len(others)} lessons, the most it may; "
                       "retire one (`lesson_retire`) or reinforce one before writing another")
    if held + len(text) > LESSON_UNIT_MAX_CHARS:
        return Refusal("cap", f"the unit's lessons would hold {held + len(text)} characters, "
                       f"past the {LESSON_UNIT_MAX_CHARS} cap; retire or shorten one first")
    return None


#: «nora» as a word, any case (Postgres `~*` and Python `re` agree on this form):
#: not "ignorance", "Leonora" or "anorak". A CJK character beside it is not a
#: letter here, so «nora你» still names her.
NAMES_HER = r"(^|[^a-z])nora([^a-z]|$)"

#: A message that tells her how to act (Lessons):
#: a prohibition, a "too much", a "next time", a "you always". Read only on a
#: message that ADDRESSES her (`SqlCharacterEvidence.addressed_since`), and
#: leaning towards recall: a false positive costs one Lessons session that
#: judges it and closes unchanged; a miss costs only the wait for the batch.
#: Not a parser, and never what the correction says: the session reads that.
_CORRECTION = re.compile(
    "|".join((
        # U+2019: iOS types a curly apostrophe by default, and NFKC keeps it.
        r"\b(?:don['\u2019]?t|do\s+not|never|stop|quit|no\s+need\s+to|no\s+more)\b",
        r"\btoo\s+(?:long|much|many|wordy|formal|serious|slow|often)\b",
        r"\b(?:shorter|briefer|less\s+(?:often|long|text))\b",
        r"\bnext\s+time\b",
        r"\byou\s+(?:always|keep|never)\b",
        r"唔好|咪再|咪咁|唔使|唔洗|少啲|講少|短啲|太長|太多|太囉嗦|好囉嗦|下次|你成日|你次次|你又|收聲|別再|别再|不要",
    )),
    re.IGNORECASE,
)


def is_correction(text: str | None) -> bool:
    """Whether `text` reads as an instruction about how she acts (see
    `_CORRECTION`). NFKC first, as `nora.domain.cues.cues_of` does."""
    return bool(text) and _CORRECTION.search(unicodedata.normalize("NFKC", text or "")) is not None
