from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from evals.facts.grade import _Verdict, grade_adjudication
from nora.domain.facts import FactVerdict

CASES = yaml.safe_load(Path("evals/facts/adjudication.yaml").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_the_corpus_grades_by_equality_with_no_judge(case):
    verdict = FactVerdict(
        subject=case["claim"]["subject"], predicate=case["claim"]["predicate"],
        object=case["claim"]["object"], status=case["expect_status"],
        addresses_claim=case["expect_status"] != "not_covered",
        source_url=None if case["expect_status"] == "not_covered" else "https://x.test/a",
        source_excerpt=None if case["expect_status"] == "not_covered" else case["source"][:80],
        supersedes=(
            None
            if case["expect_status"] != "contradicted"
            else "0191f2c4-0000-7000-8000-000000000001"
        ),
    )
    assert grade_adjudication(case, verdict) is True


def test_a_wrong_status_fails_the_grade():
    """The grader must be able to FAIL, or it is decoration."""
    case = next(c for c in CASES if c["name"] == "scoped_source_is_not_covered")
    wrong = FactVerdict(
        subject="deepmoor", predicate="has", object="black_key",
        status="contradicted", addresses_claim=True,
        source_url="https://x.test/a", source_excerpt="four coloured keys",
        supersedes="0191f2c4-0000-7000-8000-000000000001",
    )
    assert grade_adjudication(case, wrong) is False


def test_a_status_mismatch_alone_fails_the_grade_even_when_survival_matches():
    """The verdict above differs from its case on BOTH `status` and
    `removes_nothing` at once (contradicted removes the fact; the case expects
    it to survive) — so a grader that ignored `status` and graded on
    `removes_nothing` alone would pass that test too, by coincidence. Here
    `status` is the only thing wrong: `not_covered` also leaves the fact
    standing, same as the case expects, so this isolates the status-equality
    check and catches a grader that never actually looks at `.status`."""
    case = next(c for c in CASES if c["name"] == "on_topic_source_supports")
    assert case["expect_fact_still_standing"] is True
    wrong = FactVerdict(
        subject="deepmoor", predicate="has", object="green_key",
        status="not_covered", addresses_claim=False,
    )
    assert wrong.removes_nothing is True  # survival matches the case; status does not
    assert grade_adjudication(case, wrong) is False


def test_every_case_names_whether_the_fact_survives():
    """The corpus grades the FACT's fate, not a reply's wording — spec §13's
    line between the two layers."""
    for case in CASES:
        assert "expect_fact_still_standing" in case


def test_fact_verdict_satisfies_the_grader_protocol():
    """`_Verdict` is `@runtime_checkable` (mirrors `evals/sink.py::EvalSink`'s
    `isinstance` precedent, checked in
    `tests/contract/test_eval_sink_contract.py::test_both_implementations_
    satisfy_the_port`) so this is the one mechanical check that the real
    `FactVerdict` still has the two attributes the grader duck-types on —
    `evals/` sits outside the scoped mypy gate, so nothing else catches
    structural drift between the shapes."""
    verdict = FactVerdict(
        subject="deepmoor", predicate="has", object="green_key",
        status="supported", addresses_claim=True,
        source_url="https://x.test/a", source_excerpt="green, red, blue, yellow",
    )
    assert isinstance(verdict, _Verdict)


def test_removes_nothing_is_exactly_contradicted_status():
    """Pins the invariant that makes the grader's two checks — `status`
    equality and `removes_nothing` equality — currently redundant for every
    LEGALLY-constructible `FactVerdict`: Task 1 ruled `supersedes` permitted
    only when `status == "contradicted"` (`nora/domain/facts.py`'s
    `__post_init__`), so `removes_nothing` (`supersedes is None`) is a pure
    function of `status`. Findings review proved the redundancy empirically —
    rewriting `grade_adjudication` to compare `status` alone still passes all
    of this file's cases, because no legal verdict can carry a matching
    status and a mismatched survival for the grader to catch it with.

    This test is what turns that into something checked rather than merely
    argued: if `FactVerdict` is ever loosened to allow `supersedes` on a
    `supported` or `not_covered` verdict (or to allow a `contradicted`
    verdict with no `supersedes`), the survival axis stops being redundant
    and starts being load-bearing again — and this goes red on that day,
    rather than the corpus quietly no longer measuring what
    `expect_fact_still_standing` claims to measure."""
    supported = FactVerdict(
        subject="deepmoor", predicate="has", object="green_key",
        status="supported", addresses_claim=True,
        source_url="https://x.test/a", source_excerpt="green, red, blue, yellow",
    )
    not_covered = FactVerdict(
        subject="deepmoor", predicate="has", object="black_key",
        status="not_covered", addresses_claim=False,
    )
    contradicted = FactVerdict(
        subject="deepmoor", predicate="has", object="black_key",
        status="contradicted", addresses_claim=True,
        source_url="https://x.test/a", source_excerpt="does not exist",
        supersedes="0191f2c4-0000-7000-8000-000000000001",
    )
    for verdict in (supported, not_covered, contradicted):
        assert verdict.removes_nothing is (verdict.status != "contradicted")
