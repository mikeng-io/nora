"""The standing corpus's grading, without a database or a model.

`design/standing-compact/20-verification.md` §1. The integration file drives
the harness; this pins what a reading MEANS: a `fold` case is graded only on a
judge's verdict over a written position, a typo in a case is refused at load
(before a fold is paid for), and the corpus holds the mix of kinds the design
asks for.
"""
from __future__ import annotations

import collections
import json

import pytest
from strands_evals.types.evaluation import EnvironmentState, EvaluationData

from evals.backbone import (
    ERROR,
    FAIL,
    NORA_ERROR,
    NORA_JUDGED,
    NORA_JUDGED_WHY,
    NORA_POSITION,
    PASS,
    MeetsGrader,
)
from evals.rebuilt.domain import Outcome
from evals.rebuilt.grading import Judgement
from evals.rebuilt.judge import (
    POSITION_CRITERION,
    _build_position_prompt,
    criterion_verdict,
)
from evals.rebuilt.rubric import FOLD_KINDS
from evals.scenarios import CORPUS_ENTRY, JUDGE_REQUIRED, JUDGED_CORPORA, _check, load_all


@pytest.mark.parametrize(
    ("position", "judged", "expected"),
    [
        ("held", "PASS", True),
        ("held", "FAIL", False),
        # A position no judge read has not been measured against anything.
        ("held", "", None),
        ("held", "REVIEW", None),
        # Nothing written is not a verdict about a position.
        (None, "PASS", None),
        (None, "", None),
    ],
)
def test_a_fold_is_graded_only_on_a_judged_position(position, judged, expected) -> None:
    assert Outcome(admitted=None, position=position, judged=judged).meets("fold") is expected


def test_a_fold_that_errored_is_ungradable_whatever_else_it_carries() -> None:
    outcome = Outcome(admitted=None, position="held", judged="PASS", error="timeout")
    assert outcome.meets("fold") is None


def _data(**state: object) -> EvaluationData:
    return EvaluationData(
        input="", actual_output="",
        actual_environment_state=[EnvironmentState(name=k, state=v) for k, v in state.items()],
        metadata={"expect": "fold"},
    )


def test_the_grader_reads_the_position_and_carries_the_judges_reason() -> None:
    [passed] = MeetsGrader().evaluate(_data(**{
        NORA_ERROR: None, NORA_POSITION: "held", NORA_JUDGED: "PASS",
        NORA_JUDGED_WHY: "the owed answer is still there",
    }))
    assert passed.label == PASS
    assert "the owed answer is still there" in passed.reason

    [failed] = MeetsGrader().evaluate(_data(**{
        NORA_ERROR: None, NORA_POSITION: "held", NORA_JUDGED: "FAIL",
        NORA_JUDGED_WHY: "the older threads are gone",
    }))
    assert failed.label == FAIL
    assert "the older threads are gone" in failed.reason

    [unjudged] = MeetsGrader().evaluate(_data(**{
        NORA_ERROR: None, NORA_POSITION: "held", NORA_JUDGED: "",
    }))
    assert unjudged.label == ERROR


def test_the_position_prompt_carries_the_evidence_the_kind_and_the_case() -> None:
    prompt = _build_position_prompt(
        kind="stale", prop="THE-CASE-PROPERTY", evidence="THE-RECORD", position="THE-POSITION",
    )
    for part in ("THE-CASE-PROPERTY", "THE-RECORD", "THE-POSITION", FOLD_KINDS["stale"]):
        assert part in prompt
    assert POSITION_CRITERION in prompt
    with pytest.raises(ValueError, match="unknown standing kind"):
        _build_position_prompt(kind="vibes", prop="p", evidence="e", position="x")


def test_a_position_verdict_parses_fail_closed() -> None:
    answer = json.dumps([{
        "criterion": POSITION_CRITERION, "score": 0.0, "confidence": 0.9,
        "rationale": "dropped the thread",
    }])
    assert criterion_verdict(answer, POSITION_CRITERION) == "FAIL"
    assert criterion_verdict("not json", POSITION_CRITERION) == "REVIEW"
    assert criterion_verdict("[]", POSITION_CRITERION) == "REVIEW"
    judgement = Judgement(criterion=POSITION_CRITERION, verdict="FAIL", samples=(answer, answer))
    assert judgement.why == "dropped the thread"


def test_the_corpus_is_registered_as_judge_only() -> None:
    assert CORPUS_ENTRY["standing"] == "standing"
    assert "standing" in JUDGED_CORPORA
    assert "standing" in JUDGE_REQUIRED


def test_the_corpus_holds_the_mix_the_design_asks_for() -> None:
    """≥3 carry, ≥3 stale, ≥3 residue, ≥2 recap — ACTIVE cases, every one a
    named turn at that turn's own instant."""
    cases = load_all("standing")
    active = [c for c in cases if not c.metadata.get("parked")]
    kinds = collections.Counter(c.metadata["kind"] for c in active)
    assert kinds["carry"] >= 3 and kinds["stale"] >= 3 and kinds["residue"] >= 3, kinds
    assert kinds["recap"] >= 2, kinds
    for case in active:
        assert case.metadata["snapshot"], case.name
        assert case.metadata["tag_id"], case.name
        assert case.expected_output.strip(), case.name
    anchors = [(c.metadata["tag_id"], c.metadata["kind"]) for c in cases]
    assert len(set(anchors)) == len(anchors), "a turn is graded twice on one kind"


@pytest.mark.parametrize(
    ("patch", "complaint"),
    [
        ({"metadata": {"tag_id": "t", "kind": "vibes"}}, "metadata.kind"),
        ({"metadata": {"kind": "carry"}}, "metadata.tag_id"),
        ({"expected_output": ""}, "expected_output"),
        ({"expect": "reply"}, "expect must be `fold`"),
    ],
)
def test_a_malformed_fold_case_is_refused_at_load(patch, complaint) -> None:
    """At LOAD, because at run time the fold's model call is already spent."""
    row = {
        "name": "probe", "entry": "standing", "expect": "fold",
        "expected_output": "a thread is still held",
        "metadata": {"tag_id": "t", "kind": "carry"},
        **patch,
    }
    with pytest.raises(ValueError, match=complaint):
        _check("standing", row)


def test_the_suite_refuses_a_judge_only_corpus_without_a_judge(monkeypatch, capsys) -> None:
    """Refused BEFORE any subject is composed or model is paid for."""
    import sys

    from evals.__main__ import main

    monkeypatch.setattr(sys, "argv", ["python -m evals", "standing"])
    assert main() == 2
    assert "--judge" in capsys.readouterr().err


def test_print_input_renders_one_case_without_asking_for_a_verdict(monkeypatch, capsys) -> None:
    """`--print-input`: the case goes down the ONE path flagged `print_input`,
    and what prints is the model input the drive recorded — nothing else."""
    from evals import __main__ as cli
    from evals.backbone import NORA_SHOWN

    seen = []

    def fake_task(spec):
        def run(case):
            seen.append((spec.entry, case.name, case.metadata.get("print_input")))
            return {"environment_state": [EnvironmentState(name=NORA_SHOWN, state="THE-INPUT")]}
        return run

    monkeypatch.setattr(cli, "nora_task", fake_task)
    name = next(c.name for c in load_all("standing") if not c.metadata.get("parked"))
    assert cli._print_input("standing", name, source_url="x") == 0
    assert capsys.readouterr().out.strip() == "THE-INPUT"
    assert seen == [("standing", name, True)]

    # A parked case has no moment to render, and a corpus whose entry cannot
    # render its input is refused by name.
    parked = next(c.name for c in load_all("standing") if c.metadata.get("parked"))
    assert cli._print_input("standing", parked, source_url="x") == 2
    assert cli._print_input("no_trigger", "anything", source_url="x") == 2
