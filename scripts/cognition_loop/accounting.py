"""Expected cases, wakes and judgments reconciled with frozen run evidence."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.cognition_loop.cases import Case


def manifest(cases: list[Case]) -> list[dict[str, Any]]:
    return json.loads(json.dumps([asdict(case) for case in cases]))


def _criterion(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in ("polarity", "kind", "text"))


def _judgments(rows: list[dict[str, Any]]) -> Counter:
    return Counter(_criterion(row.get("expectation") or {}) for row in rows)


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


def write_report(output: Path, report: dict[str, Any]) -> None:
    report["coverage"] = reconcile(report, output)
    if report["coverage"]["status"] != "PASS":
        report["status"] = "ERROR"
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
