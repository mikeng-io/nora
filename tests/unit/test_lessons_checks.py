"""The pure checks a lesson write must clear (`nora.domain.lessons.check_lesson`):
citation, echo, no re-learn, cap.

No store, no model: `check_lesson` is handed everything it judges, so each
refusal is one call. The same checks through the book and Postgres are
`tests/infra/test_lessons.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nora.doctrine.loader import DoctrineStore
from nora.domain.culture import Citable
from nora.domain.lessons import (
    LESSON_BODY_MAX_CHARS,
    LESSON_MAX_ACTIVE,
    LESSON_REFUSALS,
    LESSON_UNIT_MAX_CHARS,
    Lesson,
    LessonRevision,
    check_lesson,
    echoes,
)
from nora.obs.metrics import Metrics

DOCTRINE = Path(__file__).resolve().parents[2] / "nora" / "doctrine"
CITABLE = Citable(
    messages=frozenset({11, 12, 13}), nora=frozenset({11}),
    episodes=frozenset({21, 22}), echo_rule=True,
)
GOOD = "when someone asks for a recommendation here, give one pick and the reason, not a list"


def _lesson(n: int, body: str, *, state: str = "active", retired_by: str | None = None,
            relearn_after: int | None = None) -> Lesson:
    rev = LessonRevision(
        id=f"r{n}", lesson_id=f"l{n}", seq=1, body=body, citations=("12",), state=state,
        retired_by=retired_by, relearn_after=relearn_after, note=None, written_at=1,
        established_by="t",
    )
    return Lesson(id=f"l{n}", unit="u", created_at=1, head=rev)


def _check(body: str = GOOD, citations: tuple[str, ...] = ("12",), *, law=(), active=(),
           operator_retired=(), replacing=None):
    return check_lesson(
        body=body, citations=citations, citable=CITABLE, law=list(law), active=list(active),
        retired_by_operator=list(operator_retired), replacing=replacing,
    )


def test_a_cited_lesson_that_echoes_nothing_passes():
    assert _check() is None


def test_every_refusal_reason_is_in_the_vocabulary():
    reasons = {
        _check(body="  ").reason,
        _check(body="say less!!").reason,
        _check(body="x" * (LESSON_BODY_MAX_CHARS + 1)).reason,
        _check(citations=()).reason,
        _check(citations=("999",)).reason,
        _check(citations=("11",)).reason,
        _check(law=[GOOD]).reason,
        _check(active=[_lesson(1, GOOD)]).reason,
        _check(operator_retired=[_lesson(2, "x", state="retired", retired_by="operator",
                                         relearn_after=50)]).reason,
        _check(active=[_lesson(i, f"distinct practice number {i} " * 3)
                       for i in range(LESSON_MAX_ACTIVE)]).reason,
    }
    assert reasons == {"empty", "too_short", "too_long", "uncited", "unresolved_citation", "echo",
                       "echoes_doctrine", "echoes_lesson", "relearn", "cap"}
    assert reasons <= LESSON_REFUSALS


def test_a_citation_the_session_was_not_handed_is_refused():
    assert _check(citations=("12", "999")).reason == "unresolved_citation"
    assert _check(citations=("response:23",)).reason == "unresolved_citation"


def test_her_own_sends_alone_are_not_evidence():
    assert _check(citations=("11",)).reason == "echo"
    assert _check(citations=("11", "12")) is None
    assert _check(citations=("response:21",)) is None


def test_echo_is_coverage_of_five_character_runs_after_normalising():
    rule = "Never share a member's location with anyone outside the room it came from."
    assert echoes(rule, rule)
    assert echoes("NEVER share a member's LOCATION, with anyone outside the room it came "
                  "from!", rule)
    assert echoes("never share a member's location with anyone outside the room", rule)
    assert not echoes("one pick and the reason, not a list", rule)
    # Sharing a topic word is not a restatement.
    assert not echoes("ask before you share where somebody is", rule)
    assert not echoes("", rule)


def test_a_lesson_that_restates_a_real_law_rule_is_refused():
    store = DoctrineStore(DOCTRINE, canary="c", display_name="Nora", trigger="tag",
                          language_hint="yue-Hant-HK", metrics=Metrics())
    law = [r.text for r in store.law_rules()]
    assert law, "the doctrine has a law subset (DEC-291)"
    quoted = next(t for t in law if len(t) >= 80)[:300]
    refusal = _check(body=quoted, law=law)
    assert refusal is not None and refusal.reason == "echoes_doctrine"
    assert _check(body=GOOD, law=law) is None


def test_a_lesson_that_restates_a_held_lesson_is_refused_unless_it_revises_that_one():
    held = _lesson(1, GOOD)
    assert _check(body=GOOD, active=[held]).reason == "echoes_lesson"
    assert _check(body=GOOD + " here", active=[held]).reason == "echoes_lesson"
    # Revising it is not an echo of itself; an echo of ANOTHER lesson still is.
    assert _check(body=GOOD + " and say why", active=[held], replacing=held) is None
    other = _lesson(2, "keep replies to one line when the room is moving fast and busy")
    echo = _check(body=other.head.body, active=[held, other], replacing=held)
    assert echo is not None and echo.reason == "echoes_lesson"


def test_a_retired_lesson_is_not_an_echo_target():
    gone = _lesson(1, GOOD, state="retired", retired_by="session")
    assert _check(body=GOOD, active=[]) is None
    assert gone.active is False


def test_no_relearn_from_evidence_no_newer_than_an_operator_retirement():
    retired = _lesson(1, GOOD, state="retired", retired_by="operator", relearn_after=13)
    assert _check(citations=("12",), operator_retired=[retired]).reason == "relearn"
    assert _check(citations=("11", "13"), operator_retired=[retired]).reason == "relearn"
    # An episode is a Record id too (her send's): 21 is newer than 13, so it is new evidence.
    assert _check(citations=("12", "response:21"), operator_retired=[retired]) is None
    later = _lesson(3, GOOD, state="retired", retired_by="operator", relearn_after=25)
    assert _check(citations=("12", "response:21"), operator_retired=[later]).reason == "relearn"
    # A citation above the ceiling is new evidence.
    fresh = Citable(messages=frozenset({12, 14}), nora=frozenset(), echo_rule=True)
    assert check_lesson(body=GOOD, citations=("12", "14"), citable=fresh, law=[], active=[],
                        retired_by_operator=[retired], replacing=None) is None


def test_cap_by_count_and_by_characters():
    full = [_lesson(i, f"practice {i}: " + "word " * 20 + f"unique{i}")
            for i in range(LESSON_MAX_ACTIVE)]
    assert _check(active=full).reason == "cap"
    # Revising one of a full unit does not add a lesson, so the count cap is not it.
    assert _check(body="a different shorter practice for one thing only", active=full,
                  replacing=full[0]) is None
    wide = [_lesson(i, f"w{i} " * 130) for i in range(6)]
    assert sum(len(x.head.body) for x in wide) + len(GOOD) > LESSON_UNIT_MAX_CHARS
    assert _check(active=wide).reason == "cap"


@pytest.mark.parametrize("n", [LESSON_BODY_MAX_CHARS])
def test_a_lesson_at_the_per_lesson_cap_is_accepted(n):
    assert _check(body=("one " * 200)[:n]) is None


def test_only_the_cli_and_the_lessons_session_build_a_book():
    """The lesson verbs sit on ONE model surface, the Lessons session, which the
    composition root builds only under `[lessons] enabled`; the operator's CLI
    is the other builder. A third importer would be a second model surface for
    the verbs (a turn, Cognition): a turn reads lessons, it never writes one."""
    root = Path(__file__).resolve().parents[2] / "nora"
    users = {
        str(p.relative_to(root.parent)) for p in root.rglob("*.py")
        if "from nora.language.lesson_book import" in p.read_text(encoding="utf-8")
    }
    assert users == {"nora/app/lessons_cli.py", "nora/app/composition.py"}
    composition = (root / "app" / "composition.py").read_text(encoding="utf-8")
    assert composition.count("LessonBook(") == 1
    assert composition.index("if cfg.lessons.enabled:") < composition.index("LessonBook(")


def test_a_lesson_too_short_to_say_anything_is_refused_before_it_can_pass_echo():
    for body in ("ok", "!!!!!!!!!!!!!!!!", "say less.", "a b c d e f g"):
        refusal = _check(body=body)
        assert refusal is not None and refusal.reason == "too_short", body
    assert _check(body="stop offering five options; say less") is None
    assert _check(body="唔好每次都畀五個選擇，講少啲") is None
