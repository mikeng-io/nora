"""Expected (case, evaluator) rows reconciled against the SDK's own report.

The experiment, task and evaluator here are the native SDK's machinery —
the accounting reads the `EvaluationReport` it actually produced, never a
synthetic stand-in. A missing, duplicated, unexpected or unanswered row
is a measurement error, not a smaller denominator.
"""

from strands_evals import Case, Experiment
from strands_evals.evaluators.evaluator import Evaluator
from strands_evals.types.evaluation import EvaluationOutput

from evals.accounting import reconcile_report, report_outcome
from evals.backbone import ERROR, PASS, read, sample_read


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


def test_a_native_sdk_report_reconciles_to_its_declared_rows():
    experiment, report = _run(
        [Case(name="generated-draw", input="generated")],
        [_Verdict()])
    coverage = reconcile_report(experiment, report)
    assert coverage["status"] == "PASS" and coverage["errors"] == []
    assert coverage["expected"] == {"draws": 1, "evaluator_rows": 1}
    assert coverage["observed"]["rows"] == 1
    axes = report_outcome(report, coverage, experiment=experiment)
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
    # Whether the lost draw ran is unestablished — never assumed.
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


def test_the_retained_report_carries_its_coverage_and_axes():
    import json
    from types import SimpleNamespace

    from evals.backbone import retain_report

    experiment, report = _run(
        [Case(name="generated-draw", input="x")], [_Verdict()])
    kept = []
    evidence = SimpleNamespace(
        failure=None, retain=lambda name, data: kept.append((name, data)))
    payload = retain_report(
        evidence, "generated-corpus", report, experiment=experiment)
    assert payload["coverage"]["status"] == "PASS"
    assert payload["outcome"]["execution"]["status"] == "PASS"
    retained = json.loads(kept[0][1])
    assert retained["coverage"] == payload["coverage"]
    assert retained["outcome"] == payload["outcome"]


def test_a_denominator_error_fails_the_run_even_when_verdicts_pass(
    monkeypatch, capsys,
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
    monkeypatch.setattr(cli, "census", lambda name: "1 case")
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


def test_the_manifest_is_required_not_inferred():
    _, report = _run([Case(name="generated-draw", input="x")], [_Verdict()])
    coverage = reconcile_report(object(), report)
    assert coverage["status"] == "UNKNOWN"
    axes = report_outcome(report, coverage)
    assert axes["validity"]["status"] == "UNKNOWN"
    # Without a manifest the observed rows are all there is to name.
    assert axes["execution"]["status"] == "PASS"
