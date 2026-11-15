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

from strands_evals import Case, Experiment
from strands_evals.evaluators.evaluator import Evaluator
from strands_evals.types.evaluation import EvaluationOutput

from evals.accounting import case_settlement, reconcile_report, report_outcome
from evals.backbone import ERROR, PASS
from evals.reading import read, sample_read


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
    assert axes["behavior"]["verdicts"] == {"PASS": 1, "FAIL": 0, "ERROR": 0}
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
