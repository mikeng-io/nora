"""Expected (case, evaluator) rows reconciled with the SDK's report.

`Experiment.run_evaluations` returns one row per (case, evaluator) pair.
The expected set is the experiment's own cases and evaluators — never
the rows it happened to return — so a missing, duplicated, unanswered
or misaligned row is a measurement error, not a smaller denominator.
The same report rows feed the three result axes: execution (did every
expected draw run and settle), validity (did the measured rows reach
their evaluators intact), behaviour (the protocol verdict counts,
`UNMEASURED` as a production-behaviour claim).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from evals.backbone import ERROR, FAIL, NORA_ERROR, PASS
from evals.outcome import UNKNOWN, UNMEASURED, axis

ALIGNED_FIELDS = ("scores", "test_passes", "reasons")


def _row_state(row: dict[str, Any], name: str) -> Any:
    for entry in row.get("actual_environment_state") or ():
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry.get("state")
        if getattr(entry, "name", None) == name:
            return getattr(entry, "state", None)
    return None


def expected_pairs(experiment: Any) -> list[tuple[Any, Any]]:
    return [(case.name, evaluator.get_name())
            for case in experiment.cases for evaluator in experiment.evaluators]


def reconcile_report(experiment: Any, report: Any) -> dict[str, Any]:
    """The experiment's expected rows against what the report kept."""
    errors: list[str] = []
    try:
        expected = Counter(expected_pairs(experiment))
    except (AttributeError, TypeError):
        return {"status": UNKNOWN, "expected": None, "observed": None,
                "errors": ["expected manifest unavailable"]}
    rows = list(report.cases or [])
    detailed = list(report.detailed_results or [])
    observed = Counter(
        (row.get("name"), row.get("evaluator")) for row in rows)
    for name, evaluator in sorted(expected - observed, key=str):
        errors.append(f"missing report row: {name}/{evaluator}")
    for name, evaluator in sorted(observed - expected, key=str):
        errors.append(f"unexpected report row: {name}/{evaluator}")
    for (name, evaluator), count in sorted(observed.items(), key=str):
        if count > 1 and expected[(name, evaluator)]:
            errors.append(f"duplicate report row: {name}/{evaluator}")
    lengths = [len(rows), len(detailed), *(
        len(getattr(report, field, None) or []) for field in ALIGNED_FIELDS)]
    if len(set(lengths)) != 1:
        errors.append(
            "report fields misaligned: " +
            ", ".join(f"{field}={n}" for field, n in
                      zip(("cases", "detailed_results", *ALIGNED_FIELDS), lengths,
                          strict=True)))
    unanswered = 0
    for index, (name, evaluator) in enumerate(
            (row.get("name"), row.get("evaluator")) for row in rows):
        outputs = detailed[index] if index < len(detailed) else []
        if not outputs:
            unanswered += 1
            errors.append(f"evaluator produced no verdict: {name}/{evaluator}")
    return {
        "status": "ERROR" if errors else "PASS",
        "expected": {"draws": len(experiment.cases),
                     "evaluator_rows": sum(expected.values())},
        "observed": {"rows": len(rows),
                     "verdicts": sum(len(o) for o in detailed),
                     "unanswered": unanswered},
        "errors": list(dict.fromkeys(errors)),
    }


def report_outcome(report: Any, coverage: dict[str, Any], *,
                   experiment: Any = None, evidence_failure: str | None = None,
                   scope: str = "protocol-rehearsal") -> dict[str, Any]:
    """The three result axes, from the same rows coverage counted."""
    rows = list(getattr(report, "cases", None) or [])
    by_name: dict[Any, dict[str, Any]] = {}
    for row in rows:
        by_name.setdefault(row.get("name"), row)
    try:
        names = [case.name for case in experiment.cases] if experiment is not None else None
    except (AttributeError, TypeError):
        names = None
    if names is None:
        names = list(by_name)
    ran = failed = missing = 0
    causes: list[str] = []
    for name in names:
        row = by_name.get(name)
        if row is None:
            missing += 1
            causes.append(f"no report row for expected draw: {name}")
            continue
        if "actual_environment_state" not in row and "actual_output" not in row:
            failed += 1
            causes.append(f"the task raised through the SDK wrapper: {name}")
            continue
        problem = _row_state(row, NORA_ERROR)
        if problem:
            failed += 1
            causes.append(f"{name}: {problem}")
        else:
            ran += 1
    execution = axis(
        "ERROR" if failed else "UNKNOWN" if missing else "PASS",
        draws=len(names), ran=ran, errored=failed, unestablished=missing,
        causes=causes)
    validity_causes = list(coverage.get("errors") or [])
    if evidence_failure:
        validity_causes.append(f"evidence: {evidence_failure}")
    if coverage.get("status") == "PASS" and not evidence_failure:
        valid = "PASS"
    elif coverage.get("status") == "ERROR" or evidence_failure:
        valid = "ERROR"
    else:
        valid = UNKNOWN
    detailed = list(getattr(report, "detailed_results", None) or [])
    verdicts = {PASS: 0, FAIL: 0, ERROR: 0}
    for outputs in detailed:
        for out in outputs:
            label = getattr(out, "label", None)
            verdicts[label if label in verdicts else ERROR] += 1
    return {
        "execution": execution,
        "validity": axis(valid, scope=scope, causes=validity_causes),
        "behavior": axis(UNMEASURED, scope=scope, verdicts=verdicts,
                         unanswered=sum(1 for outputs in detailed if not outputs)),
    }


def accounted(report: Any, experiment: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Coverage and the three axes for one SDK report, computed once."""
    coverage = reconcile_report(experiment, report)
    return coverage, report_outcome(report, coverage, experiment=experiment)


def describe_outcome(coverage: dict[str, Any], outcome: dict[str, Any]) -> None:
    """The axes and coverage errors as the run's own output."""
    from evals.terminal import safe_print
    safe_print(
        f"  outcome: execution={outcome['execution']['status']} "
        f"validity={outcome['validity']['status']} "
        f"behavior={outcome['behavior']['status']}")
    for error in coverage.get("errors") or []:
        safe_print(f"      coverage ERROR: {error}")
