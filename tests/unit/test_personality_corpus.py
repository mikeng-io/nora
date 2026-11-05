"""Quoted-notice cases carry their graded requirements through the real loader."""

import pytest

from evals.scenarios import load_all

PAIR = ("circle_brief_fact", "circle_brief_fact_no_time")


@pytest.mark.parametrize("name", PAIR)
def test_quoted_notice_acceptance_is_machine_readable(name):
    cases = {case.name: case for case in load_all("personality")}
    assert name in cases
    case = cases[name]
    assert case.expected_output.strip(), f"{name}: comments are not a graded contract"
    assert "social_suitability" in case.metadata["criteria"]
    assert case.metadata["entry"] == "tag"
    assert case.metadata["expect"] == "reply"


def test_quoted_notice_pair_changes_only_the_supplied_time():
    cases = {case.name: case for case in load_all("personality")}
    assert set(PAIR) <= cases.keys()
    supplied, missing = (cases[name] for name in PAIR)
    assert supplied.input.count("十點") == 1
    assert supplied.input.replace("十點", "", 1) == missing.input
    for field in ("snapshot", "person", "mention", "entry", "expect", "category", "criteria"):
        assert supplied.metadata[field] == missing.metadata[field], field
    assert supplied.metadata["snapshot"]
    assert supplied.metadata["mention"] is True
