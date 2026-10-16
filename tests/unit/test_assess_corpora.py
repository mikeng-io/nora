"""`evals/corpora_assess.py` — the Assess adaptor's labelled corpora load and
the runner collects them (DEC-240). Loading and shape only: running these cases
against a live model is `nora-eval`'s job (see `evals/corpora_assess.py`'s
module docstring)."""

from __future__ import annotations

import pytest

from evals.corpora_assess import GATES, NEGATIVE_KINDS, census, load_assess_corpus

_CORPORA = ("assess_event", "assess_note", "assess_memo")


@pytest.mark.parametrize("name", _CORPORA)
def test_a_corpus_loads_with_at_least_40_cases(name):
    cases = load_assess_corpus(name)
    assert len(cases) >= 40


@pytest.mark.parametrize("name", _CORPORA)
def test_a_corpus_names_every_case_uniquely(name):
    cases = load_assess_corpus(name)
    assert len({c.name for c in cases}) == len(cases)


@pytest.mark.parametrize("name", _CORPORA)
def test_a_corpus_carries_negatives_of_more_than_one_kind(name):
    cases = load_assess_corpus(name)
    c = census(cases)
    assert c.negative > 0
    assert len(c.negative_kinds) >= 2
    assert c.negative_kinds <= set(NEGATIVE_KINDS)


@pytest.mark.parametrize("name", _CORPORA)
def test_every_negative_case_expects_none(name):
    for case in load_assess_corpus(name):
        if case.category == "negative":
            assert case.expect["outcome"] == "none"
            assert case.negative_kind in NEGATIVE_KINDS


@pytest.mark.parametrize("name", _CORPORA)
def test_no_case_input_row_is_missing_a_speaker_or_text(name):
    for case in load_assess_corpus(name):
        for row in case.input:
            assert row["speaker"]
            assert row["text"]


@pytest.mark.parametrize("name", _CORPORA)
def test_no_placeholder_speaker_reads_as_a_real_persons_name(name):
    """Never a real member name. Placeholder
    speakers are `P<n>`, the Rollup stand-in `rollup`, or `Nora` for her own
    send (the memo corpus: a memo may cite it, DEC-306), nothing else."""
    for case in load_assess_corpus(name):
        for row in case.input:
            speaker = row["speaker"]
            assert speaker in ("rollup", "Nora") or (
                speaker.startswith("P") and speaker[1:].isdigit()
            ), f"{case.name}: unexpected speaker {speaker!r}"


def test_the_dinner_case_is_present_and_truncated_at_confirm():
    """The dinner case: proposed -> revised (B) -> revised (C) -> confirmed,
    truncated here (no `happened`, no stories, no memo)."""
    cases = {c.name: c for c in load_assess_corpus("assess_event") if c.series == "dinner"}
    assert set(cases) == {
        "dinner_case_1_propose", "dinner_case_2_revise_b",
        "dinner_case_3_revise_c", "dinner_case_4_confirm",
    }
    assert cases["dinner_case_1_propose"].expect["outcome"] == "create"
    assert cases["dinner_case_1_propose"].expect["place"] == "Place A"
    assert cases["dinner_case_2_revise_b"].expect["place"] == "Place B"
    assert cases["dinner_case_3_revise_c"].expect["place"] == "Place C"
    outcome4 = cases["dinner_case_4_confirm"].expect
    assert outcome4["outcome"] == "confirm"
    assert outcome4["status"] == "confirmed"
    assert outcome4["place"] == "Place C"
    assert outcome4["reasoning"] == "deduced"
    assert not any(c.expect.get("outcome") == "happened" for c in cases.values())


def test_gates_are_declared_and_none_is_a_free_pass():
    assert GATES["precision_min"] > 0
    assert GATES["recall_min"] > 0
    assert GATES["unasked_act_rate_max"] == 0.0


def test_a_corpus_with_a_stale_case_key_is_refused(tmp_path, monkeypatch):
    import evals.corpora_assess as mod

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "cases:\n"
        "- name: x\n"
        "  category: positive\n"
        "  not_a_real_key: 1\n"
        "  input:\n"
        "  - {t: '10:00', speaker: P1, text: hi}\n"
        "  expect: {outcome: create}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_CORPORA", tmp_path)
    with pytest.raises(ValueError, match="unknown case keys"):
        mod.load_assess_corpus("bad")


def test_a_negative_case_must_expect_outcome_none(tmp_path, monkeypatch):
    import evals.corpora_assess as mod

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "cases:\n"
        "- name: x\n"
        "  category: negative\n"
        "  negative_kind: joke\n"
        "  input:\n"
        "  - {t: '10:00', speaker: P1, text: hi}\n"
        "  expect: {outcome: create}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_CORPORA", tmp_path)
    with pytest.raises(ValueError, match="must expect outcome: none"):
        mod.load_assess_corpus("bad")


def test_the_memo_corpus_routes_every_settled_looking_negative_somewhere():
    """DEC-306's routing rule: a speaker's own fact is
    `remember`'s, an open one a note's, a dated one an event's, never a memo;
    and the corpus carries revisions, withdrawals and her own send cited."""
    cases = load_assess_corpus("assess_memo")
    routes = {"about_the_speaker": "remember", "still_open": "note", "dated": "event"}
    for case in cases:
        if case.negative_kind in routes:
            assert case.expect["routes_to"] == routes[case.negative_kind], case.name
    outcomes = {c.expect["outcome"] for c in cases if c.category == "positive"}
    assert outcomes == {"write", "revise", "withdraw"}
    assert any(r["speaker"] == "Nora" for c in cases for r in c.input)
