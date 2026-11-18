"""Expected (case, evaluator) rows reconciled against the SDK's own report.

The experiment, task and evaluator here are the native SDK's machinery —
the accounting reads the `EvaluationReport` it actually produced, never a
synthetic stand-in. A missing, duplicated, unexpected or unanswered row
is a measurement error, not a smaller denominator; a structurally green
report with no canonical capture/settlement behind it is UNKNOWN, never
PASS.
"""

import json
from types import SimpleNamespace

import pytest
from strands_evals import Case, Experiment
from strands_evals.evaluators.evaluator import Evaluator
from strands_evals.types.evaluation import EnvironmentState, EvaluationData, EvaluationOutput

from evals.accounting import case_settlement, reconcile_report, report_outcome
from evals.backbone import ERROR, PASS
from evals.reading import read, sample_read


def _journey_report(status, *, error=None):
    from evals.backbone import NORA_ERROR, NORA_JOURNEY, JourneyGrader

    states = [EnvironmentState(name=NORA_JOURNEY, state=status),
              EnvironmentState(name=NORA_ERROR, state=error)]
    grader = JourneyGrader()
    outputs = grader.evaluate(EvaluationData(
        input="generated", actual_environment_state=states))
    row = {"name": "generated-draw", "evaluator": grader.get_name(),
           "actual_output": "generated", "actual_environment_state": states}
    report = SimpleNamespace(
        cases=[row], detailed_results=[outputs],
        scores=[outputs[0].score], test_passes=[outputs[0].test_pass],
        reasons=[outputs[0].reason])
    experiment = SimpleNamespace(
        cases=[Case(name="generated-draw", input="generated")], evaluators=[grader])
    return experiment, report


@pytest.mark.parametrize("status,expected", [
    ("PASS", "PASS"), ("FAIL", "FAIL"), ("REVIEW", "REVIEW"),
    ("ERROR", "ERROR"), ("foreign-status", "ERROR"), (None, "ERROR"),
])
def test_journey_grader_preserves_the_retained_judgment(status, expected):
    _, report = _journey_report(status)
    answer = report.detailed_results[0][0]
    assert answer.label == expected
    assert answer.test_pass is (expected == "PASS")


def test_journey_task_error_cannot_be_cleared_by_a_retained_verdict():
    _, report = _journey_report("REVIEW", error="generated failure")
    assert report.detailed_results[0][0].label == "ERROR"


@pytest.mark.parametrize("status", ["PASS", "FAIL", "REVIEW", "ERROR"])
def test_the_journey_producer_preserves_semantic_judgments(status):
    from evals.rebuilt.autonomous_journey import _journey_result

    outcome = _journey_result({"status": status, "errors": []}, said=("generated",))
    valid = status != "ERROR"
    assert (outcome.error is None) is valid
    assert outcome.judged == (status if valid else "")
    assert outcome.said == ("generated",)
    _, native = _journey_report(outcome.judged, error=outcome.error)
    assert native.detailed_results[0][0].label == status


def _request_event(model, ref):
    return {"kind": "event", "name": "nora.model.request", "event_id": ref,
            "attributes": {"nora.model.request.body": json.dumps({"model": model})}}


def test_request_counts_come_from_canonical_requests_including_both_judges():
    from nora.subject.request_evidence import request_census

    events = [_request_event(model, f"e{i}")
              for i, model in enumerate(("subject", "alpha", "beta", "subject"))]
    events.append({"kind": "fixture_model_request", "model": "invented"})
    actual = request_census(events, judges=["alpha", "beta"])
    assert actual["status"] == "observed"
    assert actual["model_requests"] == 4 and actual["judge_requests"] == 2
    assert actual["by_model"] == {"subject": 2, "alpha": 1, "beta": 1}
    assert actual["refs"] == ["e0", "e1", "e2", "e3"]
    assert request_census([], judges=["alpha", "beta"])["model_requests"] == 0


@pytest.mark.parametrize("damage", ["missing", "malformed", "untyped", "identity"])
def test_invalid_request_evidence_is_unavailable_never_zero(damage):
    from nora.subject.request_evidence import request_census

    event = _request_event("subject", "e1")
    if damage == "missing":
        event["attributes"] = {}
    elif damage == "malformed":
        event["attributes"]["nora.model.request.body"] = "broken"
    elif damage == "untyped":
        event["attributes"]["nora.model.request.body"] = json.dumps({"model": None})
    else:
        event["event_id"] = None
    actual = request_census([event], judges=["alpha", "beta"])
    assert actual["status"] == "unavailable"
    assert actual["cause"] == "model_request_census_invalid"
    assert actual["model_requests"] is None and actual["judge_requests"] is None


@pytest.mark.parametrize("status,reviewed,accepted", [
    ("PASS", 0, True), ("REVIEW", 1, False),
])
def test_journey_review_is_counted_without_changing_the_execution_axes(
        status, reviewed, accepted):
    experiment, report = _journey_report(status)
    coverage = reconcile_report(experiment, report)
    axes = report_outcome(
        report, coverage, experiment=experiment,
        evidence={"receipt": {"status": "PASS"},
                  "settlement": {"generated-draw": "settled"}})
    assert axes["execution"]["status"] == "PASS"
    assert axes["validity"]["status"] == "PASS"
    assert axes["behavior"]["status"] == "UNMEASURED"
    assert axes["behavior"]["verdicts"].get("REVIEW") == reviewed
    flat = read(report)
    assert (flat.failed, flat.errored, getattr(flat, "reviewed", None)) == (0, 0, reviewed)
    assert flat.accepted is accepted
    sampled = sample_read(report)["generated-draw"]
    assert sampled.verdict == status
    assert (sampled.failed, sampled.errored, getattr(sampled, "reviewed", None)) == (0, 0, reviewed)


def test_sampled_review_is_unresolved_and_a_mixed_judgment_stays_unstable():
    _, report = _journey_report("REVIEW")
    report.cases *= 2
    report.detailed_results *= 2
    stable = sample_read(report)["generated-draw"]
    assert stable.verdict == "REVIEW" and getattr(stable, "reviewed", None) == 2
    _, passing = _journey_report("PASS")
    report.detailed_results[1] = passing.detailed_results[0]
    mixed = sample_read(report)["generated-draw"]
    assert mixed.verdict == "UNSTABLE"
    assert (mixed.passed, mixed.errored, mixed.reviewed) == (1, 0, 1)
    assert read(report).accepted is False


class _Verdict(Evaluator):
    """A deterministic evaluator: one labelled verdict row per draw."""

    def __init__(self, label=PASS, name=None):
        super().__init__(name=name)
        self._label = label

    def evaluate(self, data):
        return [EvaluationOutput(
            score=1.0 if self._label == PASS else 0.0,
            test_pass=self._label == PASS,
            reason="generated verdict", label=self._label)]


def _experiment(cases, evaluators):
    return Experiment(cases=cases, evaluators=evaluators)


def _answered(case):
    return {"output": "generated answer",
            "environment_state": [{"name": "nora.error", "state": None}]}


def _run(cases, evaluators, task=_answered):
    experiment = _experiment(cases, evaluators)
    return experiment, experiment.run_evaluations(task=task)


def _evidence(tmp_path, events):
    """A private evidence record holding the generated events in `RunEvidence` order."""
    root = tmp_path / "evidence-record"
    root.mkdir(mode=0o700)
    lines = []
    for order, event in enumerate(events, 1):
        lines.append(json.dumps({"order": order, "event_id": f"e{order}", **event}))
    record = root / "events.jsonl"
    record.write_text("\n".join(lines) + "\n")
    record.chmod(0o600)
    return SimpleNamespace(
        failure=None, path=root,
        retain=lambda name, data: (root / name).write_bytes(data),
        check=lambda: None, record=lambda kind, **kw: None)


def _settled(name, *, failed=False, cid="generated-cid"):
    events = [{"kind": "case_started", "name": name, "case_id": cid}]
    if failed:
        events.append({"kind": "case_failed", "case_id": cid,
                       "error_type": "RuntimeError"})
    else:
        events.append({"kind": "case_result", "case_id": cid, "result": {}})
    return events


def test_a_native_sdk_report_reconciles_to_its_declared_rows(tmp_path):
    experiment, report = _run(
        [Case(name="generated-draw", input="generated")],
        [_Verdict()])
    coverage = reconcile_report(experiment, report)
    assert coverage["status"] == "PASS" and coverage["errors"] == []
    assert coverage["expected"] == {"draws": 1, "evaluator_rows": 1}
    assert coverage["observed"]["rows"] == 1
    # A structurally green report alone is not a pass: with no canonical
    # capture or settlement the required axes stay UNKNOWN.
    axes = report_outcome(report, coverage, experiment=experiment)
    assert axes["execution"]["status"] == "UNKNOWN"
    assert axes["validity"]["status"] == "UNKNOWN"
    # With the run's own validated capture and case_result receipt, the
    # same rows are a measured PASS.
    axes = report_outcome(
        report, coverage, experiment=experiment,
        evidence={"receipt": {"status": "PASS"},
                  "settlement": {"generated-draw": "settled"}})
    assert axes["execution"]["status"] == "PASS"
    assert axes["validity"]["status"] == "PASS"
    assert axes["behavior"]["status"] == "UNMEASURED"
    assert axes["behavior"]["verdicts"] == {"PASS": 1, "FAIL": 0, "REVIEW": 0, "ERROR": 0}
    assert read(report).passed == 1 and read(report).errored == 0


def test_a_missing_row_cannot_shrink_the_declared_denominator():
    experiment, report = _run(
        [Case(name="generated-kept", input="x"), Case(name="generated-lost", input="x")],
        [_Verdict()])
    index = next(i for i, row in enumerate(report.cases)
                 if row["name"] == "generated-lost")
    for field in ("cases", "detailed_results", "scores", "test_passes", "reasons"):
        getattr(report, field).pop(index)
    coverage = reconcile_report(experiment, report)
    assert coverage["status"] == "ERROR"
    assert "missing report row: generated-lost/_Verdict" in coverage["errors"]
    axes = report_outcome(report, coverage, experiment=experiment)
    assert axes["validity"]["status"] == "ERROR"
    # Whether the lost draw ran is unestablished — never assumed. With the
    # kept draw's settlement recorded, only the missing row stays unknown.
    axes = report_outcome(
        report, coverage, experiment=experiment,
        evidence={"receipt": {"status": "PASS"},
                  "settlement": {"generated-kept": "settled"}})
    assert axes["execution"]["status"] == "UNKNOWN"
    assert axes["execution"]["unestablished"] == 1


def test_a_duplicated_row_is_an_error_not_extra_evidence():
    experiment, report = _run(
        [Case(name="generated-draw", input="x")], [_Verdict()])
    report.cases.append(dict(report.cases[0]))
    report.detailed_results.append(list(report.detailed_results[0]))
    report.scores.append(report.scores[0])
    report.test_passes.append(report.test_passes[0])
    report.reasons.append(report.reasons[0])
    coverage = reconcile_report(experiment, report)
    assert coverage["status"] == "ERROR"
    assert "duplicate report row: generated-draw/_Verdict" in coverage["errors"]


def test_an_unexpected_row_is_an_error_not_bonus_coverage():
    experiment, report = _run(
        [Case(name="generated-draw", input="x")], [_Verdict()])
    report.cases.append({**report.cases[0], "name": "generated-stray"})
    report.detailed_results.append(list(report.detailed_results[0]))
    report.scores.append(report.scores[0])
    report.test_passes.append(report.test_passes[0])
    report.reasons.append(report.reasons[0])
    coverage = reconcile_report(experiment, report)
    assert coverage["status"] == "ERROR"
    assert "unexpected report row: generated-stray/_Verdict" in coverage["errors"]


def test_two_evaluators_reconcile_each_named_row():
    experiment, report = _run(
        [Case(name="generated-draw", input="x")],
        [_Verdict(name="judge-a"), _Verdict(name="judge-b")])
    coverage = reconcile_report(experiment, report)
    assert coverage["status"] == "PASS"
    assert coverage["expected"] == {"draws": 1, "evaluator_rows": 2}
    # One evaluator's row gone missing is still a denominator error.
    index = next(i for i, row in enumerate(report.cases)
                 if row["evaluator"] == "judge-b")
    for field in ("cases", "detailed_results", "scores", "test_passes", "reasons"):
        getattr(report, field).pop(index)
    coverage = reconcile_report(experiment, report)
    assert "missing report row: generated-draw/judge-b" in coverage["errors"]


def test_a_failed_task_row_is_execution_error_never_silence():
    """The SDK keeps a bare case row and no verdicts when the task raises:
    the draw reads execution ERROR, the evaluator row unanswered — the
    failure stays in the denominator."""

    def _raise(case):
        raise RuntimeError("generated task failure")

    experiment, report = _run(
        [Case(name="generated-crash", input="x")], [_Verdict()], task=_raise)
    coverage = reconcile_report(experiment, report)
    assert coverage["status"] == "ERROR"
    assert "evaluator produced no verdict: generated-crash/_Verdict" \
        in coverage["errors"]
    axes = report_outcome(report, coverage, experiment=experiment)
    assert axes["execution"]["status"] == "ERROR"
    assert axes["execution"]["errored"] == 1
    assert axes["behavior"]["unanswered"] == 1
    reading = read(report)
    assert (reading.passed, reading.failed, reading.errored) == (0, 0, 1)


def test_a_sampled_draw_that_never_answered_is_errored_not_missing():
    """Every sampled draw ERRORs together → the case is ERROR, not
    UNSTABLE and not absent from the reading."""

    def _raise(case):
        raise RuntimeError("generated task failure")

    cases = [Case(name=f"generated-base#{i}", input="x") for i in (1, 2)]
    _, report = _run(cases, [_Verdict()], task=_raise)
    reading = sample_read(report)["generated-base"]
    assert reading.verdict == ERROR and reading.errored == 2


def test_the_case_settlement_reads_the_canonical_events():
    events = [
        {"kind": "case_started", "name": "generated-done", "case_id": "a"},
        {"kind": "case_result", "case_id": "a", "result": {}},
        {"kind": "case_started", "name": "generated-raised", "case_id": "b"},
        {"kind": "case_result", "case_id": "b", "result": {}},
        {"kind": "case_failed", "case_id": "b", "error_type": "RuntimeError"},
        {"kind": "case_started", "name": "generated-open", "case_id": "c"},
    ]
    settlement = case_settlement(events)
    assert settlement["generated-done"] == "settled"
    assert settlement["generated-raised"] == "failed"
    assert settlement["generated-open"] == "unestablished"


def test_a_clean_row_without_a_canonical_settlement_stays_unestablished():
    """A structurally complete row whose case never recorded `case_result`
    is UNKNOWN — a label, not a receipt, cannot settle it."""
    experiment, report = _run(
        [Case(name="generated-draw", input="x")], [_Verdict()])
    coverage = reconcile_report(experiment, report)
    assert coverage["status"] == "PASS"
    for settlement in ({"generated-draw": "unestablished"}, {}):
        axes = report_outcome(
            report, coverage, experiment=experiment,
            evidence={"receipt": {"status": "PASS"}, "settlement": settlement})
        assert axes["execution"]["status"] == "UNKNOWN"
        assert axes["execution"]["unestablished"] == 1


def test_a_clean_row_whose_evidence_case_failed_is_execution_error():
    experiment, report = _run(
        [Case(name="generated-draw", input="x")], [_Verdict()])
    coverage = reconcile_report(experiment, report)
    axes = report_outcome(
        report, coverage, experiment=experiment,
        evidence={"receipt": {"status": "PASS"},
                  "settlement": {"generated-draw": "failed"}})
    assert axes["execution"]["status"] == "ERROR"


def test_an_unvalidated_or_failed_capture_is_never_validity_pass():
    experiment, report = _run(
        [Case(name="generated-draw", input="x")], [_Verdict()])
    coverage = reconcile_report(experiment, report)
    settled = {"receipt": {"status": "PASS"},
               "settlement": {"generated-draw": "settled"}}
    for receipt in ({"status": "ERROR", "causes": ["dropped_event"]},):
        axes = report_outcome(report, coverage, experiment=experiment,
                              evidence={"receipt": receipt,
                                        "settlement": settled["settlement"]})
        assert axes["validity"]["status"] == "ERROR"
        assert "dropped_event" in axes["validity"]["causes"]
    axes = report_outcome(report, coverage, experiment=experiment,
                          evidence={"settlement": settled["settlement"]})
    assert axes["validity"]["status"] == "UNKNOWN"
    assert "evidence capture unvalidated or unavailable" in axes["validity"]["causes"]


def test_the_retained_report_carries_its_coverage_and_axes(tmp_path):
    from evals.backbone import retain_report

    experiment, report = _run(
        [Case(name="generated-draw", input="x")], [_Verdict()])
    evidence = _evidence(tmp_path, _settled("generated-draw"))
    payload = retain_report(
        evidence, "generated-corpus", report, experiment=experiment)
    assert payload["coverage"]["status"] == "PASS"
    assert payload["outcome"]["execution"]["status"] == "PASS"
    assert payload["outcome"]["validity"]["status"] == "PASS"
    retained = json.loads((tmp_path / "evidence-record" /
                           "corpus-generated-corpus.json").read_text())
    assert retained["coverage"] == payload["coverage"]
    assert retained["outcome"] == payload["outcome"]
    # The retained evidence block is the run's real validated prefix —
    # never an "UNVALIDATED" beside a PASS.
    assert retained["evidence"]["status"] == "PASS"


def test_a_denominator_error_fails_the_run_even_when_verdicts_pass(
    monkeypatch, capsys, tmp_path,
):
    """`_run_one` accepts the report only after expected-row accounting:
    a coverage ERROR flips the corpus to failed even with every verdict
    labelled PASS, and the axes print beside it."""
    import evals.__main__ as cli
    from evals import accounting

    experiment = _experiment([Case(name="generated-draw", input="x")], [_Verdict()])

    def experiment_for(name, **kwargs):
        return experiment, _answered

    monkeypatch.setattr(cli, "experiment_for", experiment_for)
    monkeypatch.setattr(cli, "census", lambda name, *, cohort=None: "1 case")
    evidence = _evidence(tmp_path, _settled("generated-draw"))
    monkeypatch.setattr(
        "nora.subject.eval_evidence.current", lambda: evidence)
    assert cli._run_one(
        "generated-corpus", source_url="sqlite://", judge=None, sample=1) is True
    monkeypatch.setattr(
        accounting, "reconcile_report",
        lambda e, r: {"status": "ERROR", "expected": None, "observed": None,
                      "errors": ["generated denominator mismatch"]})
    assert cli._run_one(
        "generated-corpus", source_url="sqlite://", judge=None, sample=1) is False
    out = capsys.readouterr().out
    assert "coverage ERROR: generated denominator mismatch" in out
    assert "validity=ERROR" in out


def test_successful_cli_prints_all_retained_corpus_accounting(monkeypatch, capsys, tmp_path):
    import evals.__main__ as cli

    experiment = _experiment([Case(name="generated-draw", input="x")], [_Verdict()])
    monkeypatch.setattr(cli, "experiment_for", lambda name, **kwargs: (experiment, _answered))
    monkeypatch.setattr(cli, "census", lambda name, *, cohort=None: "1 case")
    evidence = _evidence(tmp_path, _settled("generated-draw"))
    monkeypatch.setattr("nora.subject.eval_evidence.current", lambda: evidence)

    assert cli._run_one("generated-corpus", source_url="sqlite://", judge=None, sample=1)
    stdout = capsys.readouterr().out
    prefix = "  corpus accounting: "
    lines = [line.removeprefix(prefix) for line in stdout.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1
    retained = json.loads((evidence.path / "corpus-generated-corpus.json").read_text())
    assert json.loads(lines[0]) == {
        "coverage": retained["coverage"], "outcome": retained["outcome"],
    }
    assert "outcome: coverage=PASS execution=PASS validity=PASS behavior=UNMEASURED" in stdout


def test_an_unestablished_axis_fails_the_run_even_when_verdicts_pass(
    monkeypatch, tmp_path,
):
    """A corpus whose required outcome stays UNKNOWN — complete rows,
    every verdict PASS, but no canonical settlement — is never accepted."""
    import evals.__main__ as cli

    experiment = _experiment([Case(name="generated-draw", input="x")], [_Verdict()])
    monkeypatch.setattr(cli, "experiment_for",
                        lambda name, **kwargs: (experiment, _answered))
    monkeypatch.setattr(cli, "census", lambda name, *, cohort=None: "1 case")
    # A capture that validates but never recorded this case's completion.
    evidence = _evidence(tmp_path, [])
    monkeypatch.setattr(
        "nora.subject.eval_evidence.current", lambda: evidence)
    assert cli._run_one(
        "generated-corpus", source_url="sqlite://", judge=None, sample=1) is False


def test_the_manifest_is_required_not_inferred():
    _, report = _run([Case(name="generated-draw", input="x")], [_Verdict()])
    coverage = reconcile_report(object(), report)
    assert coverage["status"] == "UNKNOWN"
    axes = report_outcome(report, coverage)
    assert axes["validity"]["status"] == "UNKNOWN"
    # Without a manifest and without settlement evidence nothing ran.
    assert axes["execution"]["status"] == "UNKNOWN"


def test_protocol_axes_keep_behavior_unmeasured_even_with_verdicts():
    """The default claim: verdict counts are retained as protocol
    evidence, and behavior can never promote to a verdict under a
    rehearsal."""
    experiment, report = _journey_report("PASS")
    coverage = reconcile_report(experiment, report)
    axes = report_outcome(
        report, coverage, experiment=experiment,
        evidence={"receipt": {"status": "PASS"},
                  "settlement": {"generated-draw": "settled"}})
    assert axes["behavior"]["status"] == "UNMEASURED"
    assert axes["behavior"]["verdicts"] == {
        "PASS": 1, "FAIL": 0, "REVIEW": 0, "ERROR": 0}
    assert axes["behavior"].get("scope") == "protocol-rehearsal"
    assert axes["validity"]["scope"] == "protocol-rehearsal"


@pytest.mark.parametrize("status,expected", [
    ("PASS", "PASS"), ("FAIL", "FAIL"), ("REVIEW", "REVIEW"),
    ("ERROR", "ERROR"),
])
def test_a_behavioral_measurement_reports_the_evaluators_axis(status, expected):
    """Execution and validity stay their own axes; the evaluator's own
    PASS/FAIL/REVIEW/ERROR is the behavior status — a quality FAIL or
    REVIEW never demotes the execution that carried it."""
    from evals.measurement import BEHAVIORAL

    experiment, report = _journey_report(status)
    coverage = reconcile_report(experiment, report)
    axes = report_outcome(
        report, coverage, experiment=experiment,
        measurement=BEHAVIORAL,
        evidence={"receipt": {"status": "PASS"},
                  "settlement": {"generated-draw": "settled"}})
    assert axes["execution"]["status"] == "PASS"
    assert axes["validity"]["status"] == "PASS"
    assert axes["behavior"]["status"] == expected
    assert axes["behavior"].get("scope") == "behavioral-evaluation"


@pytest.mark.parametrize("status", ["FAIL", "REVIEW"])
@pytest.mark.parametrize("receipt,expected", [(None, "UNKNOWN"), ({"status": "ERROR"}, "ERROR")])
def test_invalid_evidence_cannot_be_an_ordinary_behavioral_judgment(status, receipt, expected):
    from evals.measurement import BEHAVIORAL

    experiment, report = _journey_report(status)
    axes = report_outcome(
        report, reconcile_report(experiment, report), experiment=experiment,
        measurement=BEHAVIORAL,
        evidence={"receipt": receipt, "settlement": {"generated-draw": "settled"}})
    assert axes["execution"]["status"] == "PASS"
    assert axes["behavior"]["status"] == expected


def test_a_gateway_does_not_choose_the_measurement_claim():
    from evals.measurement import BEHAVIORAL, PROTOCOL, for_world

    world = SimpleNamespace(model_gateway=object())
    assert for_world(world) == PROTOCOL
    assert for_world(world, BEHAVIORAL) == BEHAVIORAL
    with pytest.raises(ValueError, match="cannot promote"):
        for_world(SimpleNamespace(model_gateway=None), BEHAVIORAL)


def test_an_unknown_serialized_measurement_is_refused():
    from evals.measurement import Measurement

    assert Measurement(mode="protocol-rehearsal").mode == "protocol-rehearsal"
    with pytest.raises(ValueError, match="unknown measurement"):
        Measurement(mode="unrecognized")


def test_a_behavioral_pass_cannot_stand_on_an_unestablished_measurement():
    """Verdicts alone are not a claim: a missing settlement or an
    unvalidated capture keeps behavior UNKNOWN — never PASS on a
    measurement that did not run cleanly."""
    from evals.measurement import BEHAVIORAL

    experiment, report = _journey_report("PASS")
    coverage = reconcile_report(experiment, report)
    axes = report_outcome(
        report, coverage, experiment=experiment, measurement=BEHAVIORAL)
    assert axes["execution"]["status"] == "UNKNOWN"
    assert axes["behavior"]["status"] == "UNKNOWN"


def test_an_unanswered_row_can_never_become_a_behavioral_pass():
    """A draw whose evaluator produced no verdict is a measurement ERROR
    — an unanswered measurement is not a quality score."""
    from evals.measurement import BEHAVIORAL

    def _raise(case):
        raise RuntimeError("generated task failure")

    experiment, report = _run(
        [Case(name="generated-crash", input="x")], [_Verdict()], task=_raise)
    coverage = reconcile_report(experiment, report)
    axes = report_outcome(report, coverage, experiment=experiment,
                          measurement=BEHAVIORAL)
    assert axes["execution"]["status"] == "ERROR"
    assert axes["behavior"]["status"] == "ERROR"
    assert axes["behavior"]["unanswered"] == 1


def test_the_retained_report_carries_the_bound_measurement(tmp_path):
    """The retained corpus JSON reports the SAME claim the run bound —
    its mode and the honest behavior status, not a pair of literals."""
    from evals.backbone import retain_report
    from evals.measurement import BEHAVIORAL

    experiment, report = _run(
        [Case(name="generated-draw", input="x")], [_Verdict(label="FAIL")])
    evidence = _evidence(tmp_path, _settled("generated-draw"))
    payload = retain_report(
        evidence, "generated-corpus", report, experiment=experiment,
        measurement=BEHAVIORAL)
    assert payload["mode"] == "behavioral-evaluation"
    assert payload["behavior"] == "FAIL"
    assert payload["outcome"]["execution"]["status"] == "PASS"
    retained = json.loads((tmp_path / "evidence-record" /
                           "corpus-generated-corpus.json").read_text())
    assert retained["mode"] == payload["mode"]
    assert retained["behavior"] == payload["behavior"]
    assert retained["outcome"] == payload["outcome"]

    # The default claim still reads protocol rehearsal and UNMEASURED.
    payload = retain_report(
        evidence, "generated-corpus", report, experiment=experiment)
    assert payload["mode"] == "protocol-rehearsal"
    assert payload["behavior"] == "UNMEASURED"


def test_the_draw_state_stamps_the_bound_measurement_not_a_literal():
    """`Spec.measurement` reaches every SDK row: protocol stays
    UNMEASURED, a behavioral draw stamps its own retained verdict, and an
    errored draw can never read as a measured verdict."""
    from evals.backbone import (
        NORA_BEHAVIOR,
        NORA_ERROR,
        NORA_JOURNEY,
        NORA_MODE,
        Spec,
        _measured_result,
    )
    from evals.measurement import BEHAVIORAL

    def states(result):
        return {e.name: e.state for e in result["environment_state"]}

    assert Spec(source_url="postgresql://u@h/eval").measurement.mode \
        == "protocol-rehearsal"
    spec = Spec(source_url="postgresql://u@h/eval", measurement=BEHAVIORAL)

    row = {"environment_state": [
        EnvironmentState(name=NORA_JOURNEY, state="REVIEW"),
        EnvironmentState(name=NORA_ERROR, state=None)]}
    stamped = states(_measured_result(row, spec.measurement))
    assert stamped[NORA_MODE] == "behavioral-evaluation"
    assert stamped[NORA_BEHAVIOR] == "REVIEW"

    errored = {"environment_state": [
        EnvironmentState(name=NORA_ERROR, state="generated task failure")]}
    stamped = states(_measured_result(errored, spec.measurement))
    assert stamped[NORA_MODE] == "behavioral-evaluation"
    assert stamped[NORA_BEHAVIOR] == "ERROR"

    stamped = states(_measured_result(
        row, Spec(source_url="postgresql://u@h/eval").measurement))
    assert stamped[NORA_MODE] == "protocol-rehearsal"
    assert stamped[NORA_BEHAVIOR] == "UNMEASURED"
