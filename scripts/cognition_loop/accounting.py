"""Expected cases, wakes and judgments reconciled with frozen run evidence."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from evals.outcome import UNKNOWN, UNMEASURED, axis
from scripts.cognition_loop.cases import Case


def manifest(cases: list[Case]) -> list[dict[str, Any]]:
    return json.loads(json.dumps([asdict(case) for case in cases]))


def _criterion(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in ("polarity", "kind", "text"))


def _judgments(rows: list[dict[str, Any]]) -> Counter:
    return Counter(_criterion(row.get("expectation") or {}) for row in rows)


def frozen_wake_identity(frozen: dict[str, Any], *, unit: Any,
                         at_ms: Any = None, step: Any = None) -> str | None:
    """Why a frozen artifact cannot stand in for this case/unit/wake, else None.

    An artifact proves it belongs by content, never by filename alone: its
    trajectory's unit and instant, or its error's step, must agree with the
    report row's own values. When nothing agrees — or there is nothing to
    check — the artifact's provenance is unknown rather than assumed.
    """
    if not isinstance(frozen, dict):
        return "frozen wake is not an object"
    checks = []
    trajectory = frozen.get("trajectory")
    if isinstance(trajectory, dict):
        if trajectory.get("unit") is not None:
            checks.append(trajectory["unit"] == unit)
        if trajectory.get("at_ms") is not None and at_ms is not None:
            checks.append(trajectory["at_ms"] == at_ms)
    if frozen.get("at_ms") is not None and at_ms is not None:
        checks.append(frozen["at_ms"] == at_ms)
    frozen_step = (frozen.get("error") or {}).get("step") \
        if isinstance(frozen.get("error"), dict) else None
    if frozen_step is not None:
        checks.append(frozen_step == step)
    if not checks:
        return "frozen wake identity unknown"
    if not all(checks):
        return "frozen wake identity mismatch"
    return None


def reconcile(report: dict[str, Any], output: Path, *, judgments: bool = True) -> dict[str, Any]:
    """Count against the original manifest, including interrupted and missing work."""
    try:
        return _reconcile(report, output, judgments=judgments)
    except (KeyError, TypeError, AttributeError, ValueError):
        return {"status": "ERROR", "expected": None, "observed": None,
                "errors": ["invalid coverage record"]}


def _reconcile(report: dict[str, Any], output: Path, *, judgments: bool) -> dict[str, Any]:
    expected = report.get("expected_cases")
    errors: list[str] = []
    counts = dict.fromkeys(("cases", "wakes", "criteria", "judge_answers"), 0)
    observed = dict(counts)
    if not isinstance(expected, list) or not expected:
        return {"status": "ERROR", "expected": None, "observed": None,
                "errors": ["missing expected case manifest"]}
    cases = report.get("cases") or []
    observed["cases"] = len(cases)
    observed["wakes"] = sum(len(case.get("wakes") or []) for case in cases)
    results = [e for case in cases for wake in case.get("wakes") or []
               for e in wake.get("expectations") or []]
    observed["criteria"] = len(results)
    observed["judge_answers"] = sum(len(e.get("answers") or []) for e in results)
    counts["cases"] = len(expected)
    names = [case["name"] for case in expected]
    if len(set(names)) != len(names) or Counter(names) != Counter(c.get("case") for c in cases):
        errors.append("case report denominator mismatch")
    inspect_only = (report.get("run") or {}).get("mode") == "inspect-only"
    files: set[str] = set()
    for case in expected:
        matches = [row for row in cases if row.get("case") == case["name"]]
        row = matches[0] if len(matches) == 1 else {}
        wakes = row.get("wakes") or []
        counts["wakes"] += len(case["wakes"])
        if row.get("unit") != case["unit"]:
            errors.append(f"case unit mismatch: {case['name']}")
        if len(wakes) != len(case["wakes"]):
            errors.append(f"wake report denominator mismatch: {case['name']}")
        for index, wake_id in enumerate(case["wakes"]):
            criteria = [] if inspect_only else [
                e for e in case["expectations"] if e["wake"] == index]
            counts["criteria"] += len(criteria)
            counts["judge_answers"] += 2 * len(criteria)
            wake = wakes[index] if index < len(wakes) else {}
            if wake.get("recorded_wake_id") != wake_id:
                errors.append(f"wake report identity mismatch: {case['name']}:{index}")
            name = f"wake-{index}-{wake_id}.json"
            if name in files:
                errors.append("ambiguous frozen wake: " + name)
            files.add(name)
            try:
                frozen = json.loads((output / name).read_text())
                if not isinstance(frozen, dict):
                    raise ValueError
            except (OSError, ValueError):
                errors.append("missing or invalid frozen wake: " + name)
                continue
            identity = frozen_wake_identity(
                frozen, unit=case["unit"], at_ms=wake.get("at_ms"),
                step=wake.get("step"))
            if identity is not None:
                errors.append(f"{identity}: {name}")
            if judgments:
                actual = wake.get("expectations") or []
                if _judgments(actual) != Counter(_criterion(e) for e in criteria):
                    errors.append("criterion denominator mismatch: " + name)
                if any(len(e.get("answers") or []) != 2 for e in actual):
                    errors.append("judge answer denominator mismatch: " + name)
                if json.dumps(frozen.get("expectations", []), sort_keys=True, allow_nan=False) != (
                        json.dumps(actual, sort_keys=True, allow_nan=False)):
                    errors.append("frozen/report judgments differ: " + name)
    return {"status": "ERROR" if errors else "PASS", "expected": counts,
            "observed": observed, "errors": list(dict.fromkeys(errors))}


def outcome(report: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    """The three result axes, from the same rows the denominators counted.

    Execution answers whether every expected wake ran and settled — a
    silent/no-session wake counts only on its positive canonical
    completion, a missing row stays unestablished, never assumed.
    Validity answers whether the measurement reached its consumers
    intact: coverage plus the evidence receipt together, never paired
    event counts alone. Behaviour carries the protocol verdict counts
    under the path's own claim scope — `UNMEASURED` as a production
    behaviour claim.
    """
    rejudged = report.get("rejudged")
    run = report.get("run") or {}
    mode = run.get("mode") or ("protocol-rehearsal" if rejudged else None)
    scope = ("protocol-rehearsal" if rejudged
             else "inspect-only" if mode == "inspect-only"
             else run.get("claim") or "recorded-wake-replay")
    causes: list[str] = []
    if rejudged is not None:
        executed = rejudged.get("execution")
        if executed != "PASS":
            causes.append(f"rejudgement execution: {executed}")
        execution = axis("PASS" if executed == "PASS" else "ERROR",
                         scope="rejudgement", causes=causes)
    else:
        settled = errored = unestablished = 0
        rows = {c.get("case"): c for c in report.get("cases") or []
                if isinstance(c, dict)}
        for case in report.get("expected_cases") or []:
            wakes = (rows.get(case.get("name")) or {}).get("wakes") or []
            for index in range(len(case.get("wakes") or ())):
                wake = wakes[index] if index < len(wakes) else None
                if not isinstance(wake, dict):
                    unestablished += 1
                    continue
                step = wake.get("step")
                if step not in (None, "report"):
                    errored += 1
                    causes.append(
                        f"{case.get('name')}:{index} failed at {step}: "
                        f"{wake.get('error')}")
                elif (wake.get("session_id") is not None
                      or wake.get("no_session")
                      or wake.get("model_calls") is False):
                    settled += 1
                else:
                    unestablished += 1
                    causes.append(
                        f"{case.get('name')}:{index} settled without a "
                        "completion signal")
        execution = axis(
            "ERROR" if errored else UNKNOWN if unestablished else "PASS",
            scope=mode, settled=settled, errored=errored,
            unestablished=unestablished, causes=causes)
    evidence = report.get("rejudge_evidence") if rejudged else report.get("evidence")
    receipt = (evidence or {}).get("status")
    validity_causes = list(coverage.get("errors") or [])
    if receipt not in (None, "PASS"):
        validity_causes.extend((evidence or {}).get("causes") or [receipt])
    if coverage.get("status") == "PASS" and receipt == "PASS":
        valid = "PASS"
    elif coverage.get("status") == "ERROR" or receipt == "ERROR":
        valid = "ERROR"
    else:
        valid = UNKNOWN
    verdicts = {"PASS": 0, "FAIL": 0, "REVIEW": 0, "ERROR": 0}
    for case in report.get("cases") or []:
        for wake in (case.get("wakes") or []) if isinstance(case, dict) else []:
            for entry in wake.get("expectations") or []:
                verdict = entry.get("verdict")
                verdicts[verdict if verdict in verdicts else "ERROR"] += 1
    return {
        "execution": execution,
        "validity": axis(valid, scope=scope, causes=validity_causes),
        "behavior": axis(UNMEASURED, scope=scope, verdicts=verdicts),
    }


def write_report(output: Path, report: dict[str, Any]) -> None:
    report["coverage"] = reconcile(report, output)
    try:
        report["outcome"] = outcome(report, report["coverage"])
    except Exception:  # noqa: BLE001 — a failed projection is itself an error
        report["outcome"] = {
            "execution": {"status": "ERROR"}, "validity": {"status": "ERROR"},
            "behavior": {"status": "ERROR"}, "cause": "outcome projection failed"}
    if report["coverage"]["status"] != "PASS" \
            or report["outcome"].get("cause") == "outcome projection failed":
        report["status"] = "ERROR"
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
