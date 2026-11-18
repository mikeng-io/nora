"""Expected (case, evaluator) rows reconciled with the SDK's report.

`Experiment.run_evaluations` returns one row per (case, evaluator) pair.
The expected set is the experiment's own cases and evaluators — never
the rows it happened to return — so a missing, duplicated, unanswered
or misaligned row is a measurement error, not a smaller denominator.
The same report rows feed the three result axes: execution (did every
expected draw run and settle — a `case_result`/`case_failed` event is the
canonical receipt, not a clean-looking row), validity (did the measured
rows reach their evaluators intact — coverage plus the validated evidence
receipt, an unvalidated prefix is UNKNOWN), behaviour (the protocol
verdict counts, `UNMEASURED` as a production-behaviour claim).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Literal

from evals.backbone import ERROR, FAIL, NORA_ERROR, PASS
from evals.measurement import PROTOCOL, Measurement
from evals.outcome import REVIEW, UNKNOWN, axis

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


def case_settlement(events: list[dict[str, Any]] | None) -> dict[str, str]:
    """Each started case's canonical completion: a `case_result` is the
    settled receipt, `case_failed` the failed one, a case that recorded
    neither stays unestablished — never inferred from its report row."""
    names: dict[Any, Any] = {}
    settled: set[Any] = set()
    failed: set[Any] = set()
    for event in events or []:
        case_id = event.get("case_id")
        if case_id is None:
            continue
        kind = event.get("kind")
        if kind == "case_started":
            names.setdefault(case_id, event.get("name"))
        elif kind == "case_result":
            settled.add(case_id)
        elif kind == "case_failed":
            failed.add(case_id)
    return {
        name: ("failed" if case_id in failed else
               "settled" if case_id in settled else "unestablished")
        for case_id, name in names.items() if name is not None
    }


def evidence_outcome(evidence: Any) -> dict[str, Any]:
    """The canonical receipts a report can claim from this capture: the
    validated live prefix (the run's own terminal deferred, as `capture`
    itself defers it) and each started case's settlement."""
    from nora.subject.eval_evidence import EvidenceError, read_events, validate_events

    if getattr(evidence, "failure", None):
        return {"receipt": {"status": ERROR, "causes": [evidence.failure]},
                "settlement": {}}
    try:
        events = read_events(evidence.path)
        receipt = validate_events(events, require_terminal=False)
    except (EvidenceError, OSError, ValueError, AttributeError, TypeError) as exc:
        return {"receipt": {"status": ERROR, "causes": [str(exc)]},
                "settlement": {}}
    return {"receipt": receipt, "settlement": case_settlement(events)}


def report_outcome(report: Any, coverage: dict[str, Any], *,
                   experiment: Any = None, evidence: dict[str, Any] | None = None,
                   measurement: Measurement = PROTOCOL) -> dict[str, Any]:
    """The three result axes, from the same rows coverage counted plus the
    canonical evidence receipts (`receipt` = the `validate_events` verdict,
    `settlement` = `case_settlement`'s per-case completion), scoped to the
    bound measurement's claim."""
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
    receipt = (evidence or {}).get("receipt")
    settlement = (evidence or {}).get("settlement") or {}
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
            continue
        state = settlement.get(name)
        if state == "failed":
            failed += 1
            causes.append(f"{name}: the evidence case failed")
        elif state == "settled":
            ran += 1
        else:
            missing += 1
            causes.append(
                f"{name}: no canonical settlement recorded"
                if evidence is not None else
                f"{name}: settlement evidence unavailable")
    execution = axis(
        "ERROR" if failed else "UNKNOWN" if missing else "PASS",
        draws=len(names), ran=ran, errored=failed, unestablished=missing,
        causes=causes)
    receipt_status = (receipt or {}).get("status")
    validity_causes = list(coverage.get("errors") or [])
    if receipt_status == "ERROR":
        validity_causes.extend(receipt.get("causes") or ["evidence receipt ERROR"])
    elif receipt_status is None:
        validity_causes.append("evidence capture unvalidated or unavailable")
    if coverage.get("status") == "PASS" and receipt_status == "PASS":
        valid = "PASS"
    elif coverage.get("status") == "ERROR" or receipt_status == "ERROR":
        valid = "ERROR"
    else:
        valid = UNKNOWN
    detailed = list(getattr(report, "detailed_results", None) or [])
    verdicts = {PASS: 0, FAIL: 0, REVIEW: 0, ERROR: 0}
    for outputs in detailed:
        for out in outputs:
            label = getattr(out, "label", None)
            verdicts[label if label in verdicts else ERROR] += 1
    unanswered = sum(1 for outputs in detailed if not outputs)
    return {
        "execution": execution,
        "validity": axis(valid, scope=measurement.mode,
                         causes=validity_causes),
        "behavior": axis(
            measurement.behavior_status(
                verdicts=verdicts, unanswered=unanswered,
                execution=execution["status"], validity=valid),
            scope=measurement.mode, verdicts=verdicts,
            unanswered=unanswered),
    }


def accounted(report: Any, experiment: Any, *,
              evidence: dict[str, Any] | None = None,
              measurement: Measurement = PROTOCOL) -> tuple[dict[str, Any], dict[str, Any]]:
    """Coverage and the three axes for one SDK report, computed once."""
    coverage = reconcile_report(experiment, report)
    return coverage, report_outcome(
        report, coverage, experiment=experiment, evidence=evidence,
        measurement=measurement)


def accepted(coverage: dict[str, Any], outcome: dict[str, Any]) -> bool:
    """A corpus is accepted only when every required axis is established:
    coverage, execution and validity PASS — an UNKNOWN or ERROR required
    outcome is never a corpus success."""
    return (coverage["status"] == "PASS"
            and outcome["execution"]["status"] == "PASS"
            and outcome["validity"]["status"] == "PASS")


def describe_outcome(
    coverage: dict[str, Any], outcome: dict[str, Any], *,
    scope: Literal["corpus", "journey"] = "corpus",
) -> None:
    """Print the measured accounting with its report scope."""
    from evals.terminal import safe_print
    safe_print(f"  {scope} accounting: " + json.dumps({"coverage": coverage, "outcome": outcome}))
    if scope == "journey":
        return
    safe_print(
        f"  outcome: coverage={coverage['status']} execution={outcome['execution']['status']} "
        f"validity={outcome['validity']['status']} "
        f"behavior={outcome['behavior']['status']}")
    for error in coverage.get("errors") or []:
        safe_print(f"      coverage ERROR: {error}")
