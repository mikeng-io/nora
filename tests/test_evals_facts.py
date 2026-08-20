from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from evals.facts.grade import grade_adjudication
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
