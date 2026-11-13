"""How a judge's answer is read and rolled up."""

import pytest

from nora.subject.eval_evidence import EvidenceError
from scripts.cognition_loop.judges import parse_answer
from scripts.cognition_loop.verdicts import (
    ERROR,
    PASS,
    JudgeAnswer,
    expectation_verdict,
    validate_citations,
)


def test_an_absence_answer_needs_no_citation_only_when_the_kind_is_empty():
    answers = (
        JudgeAnswer("met", reason="no reminder exists", entry_ids=()),
        JudgeAnswer("met", reason="none set", entry_ids=()),
    )
    assert expectation_verdict(answers, nothing_to_cite=True).verdict == PASS
    assert expectation_verdict(answers).verdict == ERROR


def test_a_citation_without_its_kind_prefix_is_known():
    answer = JudgeAnswer("met", reason="r", entry_ids=("u#character:p:1",))
    assert validate_citations(answer, {"profile:u#character:p:1"}) is None
    assert validate_citations(answer, {"profile:u#character:p:2"}) == "u#character:p:1"


def test_a_fenced_answer_parses_and_an_empty_one_names_its_size():
    got = parse_answer('```json\n{"verdict": "met", "reason": "r", "entry_ids": ["n:1"]}\n```')
    assert got.verdict == "met" and got.entry_ids == ("n:1",)
    with pytest.raises(EvidenceError, match=r"\(0 chars\)"):
        parse_answer("")
    with pytest.raises(EvidenceError, match="met\\|not_met"):
        parse_answer('{"verdict": "maybe"}')


def test_a_citation_by_the_uuid_that_ends_one_known_id_is_grounded():
    from scripts.cognition_loop.verdicts import JudgeAnswer

    u = "11111111-2222-4333-8444-555555555555"
    known = [f"profile:unit-a#person-b:{u}", "note:7"]
    def cites(i):
        return JudgeAnswer(verdict="met", entry_ids=(i,))
    assert validate_citations(cites(u), known) is None
    assert validate_citations(cites(f"unit-a#wrong:{u}"), known) is None
    other = "00000000-0000-4000-8000-000000000000"
    assert validate_citations(cites(other), known) == other
    twice = [*known, f"memo:{u}"]
    assert validate_citations(cites(f"x:{u}"), twice) == f"x:{u}"
