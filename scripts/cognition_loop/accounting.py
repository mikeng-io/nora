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


def _same(actual: Any, expected: Any) -> bool:
    """Typed identity: `True` never equals `1`, an int never equals a str."""
    return type(actual) is type(expected) and actual == expected


def frozen_wake_identity(frozen: dict[str, Any], *, unit: Any,
                         recorded_wake_id: Any, at_ms: Any = None,
                         step: Any = None, wake_id: Any = None,
                         session_id: Any = None) -> str | None:
    """Why a frozen artifact cannot stand in for this case/unit/wake, else None.

    An artifact proves it belongs by content, never by filename: its own
    recorded `recorded_wake_id`/`unit`/`at_ms` must equal the declared
    case's under typed identity, its error step must equal the row's, and
    where the row names a candidate wake/session (the replay's own —
    never the recorded wake) the trajectory must carry the same. A row
    that names no session is matched only by the trajectory's own
    explicit `session_id: None`, never by an absent key. Required fields
    either side never recorded are unknown, a disagreement is foreign —
    neither is assumed.
    """
    if not isinstance(frozen, dict):
        return "frozen wake is not an object"
    missing: list[str] = []
    foreign: list[str] = []

    def required(actual: Any, expected: Any, label: str) -> None:
        if actual is None or expected is None:
            missing.append(label)
        elif not _same(actual, expected):
            foreign.append(label)

    def candidate(actual: Any, expected: Any, label: str) -> None:
        if expected is None:
            if actual is not None:
                foreign.append(label)
        elif actual is None:
            missing.append(label)
        elif not _same(actual, expected):
            foreign.append(label)

    required(frozen.get("recorded_wake_id"), recorded_wake_id, "recorded_wake")
    required(frozen.get("unit"), unit, "unit")
    required(frozen.get("at_ms"), at_ms, "at_ms")
    error = frozen.get("error")
    raw_step = error.get("step") if isinstance(error, dict) else None
    if ((error is not None and not isinstance(error, dict))
            or (raw_step is not None and not isinstance(raw_step, str))
            or raw_step != step):
        foreign.append("step")
    trajectory = frozen.get("trajectory")
    if isinstance(trajectory, dict):
        candidate(trajectory.get("wake_id"), wake_id, "actual_wake")
        if session_id is None:
            if trajectory.get("session_id") is not None:
                foreign.append("session")
            elif "session_id" not in trajectory:
                missing.append("session")
        else:
            candidate(trajectory.get("session_id"), session_id, "session")
        trajectory_unit = trajectory.get("unit")
        if trajectory_unit is not None and (
                unit is None or not isinstance(trajectory_unit, str)
                or trajectory_unit != unit):
            foreign.append("trajectory.unit")
        trajectory_ms = trajectory.get("at_ms")
        if (trajectory_ms is not None and at_ms is not None
                and not _same(trajectory_ms, at_ms)):
            foreign.append("trajectory.at_ms")
    elif step is None and (wake_id is not None or session_id is not None):
        missing.append("trajectory")
    if foreign:
        return "frozen wake identity mismatch: " + ", ".join(foreign)
    if missing:
        return "frozen wake identity unknown: " + ", ".join(missing)
    return None


def frozen_turn_identity(frozen: dict[str, Any], *, unit: Any, turn: Any,
                         declared_input: Any, step: Any = None,
                         session_id: Any = None) -> str | None:
    """Why a frozen artifact cannot stand in for this case/unit/turn, else
    None. The artifact proves it belongs by content: its own turn index,
    unit and declared input text under typed identity, its error step
    equal to the row's, and its trajectory's `session_id` equal to the
    row's — None for an input that bound no session, the bound session's
    own identity when its stimulus settled to one.
    """
    if not isinstance(frozen, dict):
        return "frozen turn is not an object"
    missing: list[str] = []
    foreign: list[str] = []

    def required(actual: Any, expected: Any, label: str) -> None:
        if actual is None or expected is None:
            missing.append(label)
        elif not _same(actual, expected):
            foreign.append(label)

    required(frozen.get("turn"), turn, "turn")
    required(frozen.get("unit"), unit, "unit")
    declared = frozen.get("declared_input")
    required(declared.get("text") if isinstance(declared, dict) else None,
             declared_input, "declared_input")
    error = frozen.get("error")
    raw_step = error.get("step") if isinstance(error, dict) else None
    if ((error is not None and not isinstance(error, dict))
            or (raw_step is not None and not isinstance(raw_step, str))
            or raw_step != step):
        foreign.append("step")
    trajectory = frozen.get("trajectory")
    if isinstance(trajectory, dict):
        if "session_id" not in trajectory:
            missing.append("session")
        elif not _same(trajectory["session_id"], session_id):
            foreign.append("session")
        trajectory_unit = trajectory.get("unit")
        if (trajectory_unit is not None and (unit is None
                or not isinstance(trajectory_unit, str)
                or trajectory_unit != unit)):
            foreign.append("trajectory.unit")
    elif step is None:
        missing.append("trajectory")
    if foreign:
        return "frozen turn identity mismatch: " + ", ".join(foreign)
    if missing:
        return "frozen turn identity unknown: " + ", ".join(missing)
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
    counts = dict.fromkeys(
        ("cases", "wakes", "turns", "criteria", "judge_answers"), 0)
    observed = dict(counts)
    if not isinstance(expected, list) or not expected:
        return {"status": "ERROR", "expected": None, "observed": None,
                "errors": ["missing expected case manifest"]}
    cases = report.get("cases") or []
    observed["cases"] = len(cases)
    observed["wakes"] = sum(len(case.get("wakes") or []) for case in cases)
    observed["turns"] = sum(len(case.get("turns") or []) for case in cases)
    results = [e for case in cases for occasion in
               (list(case.get("wakes") or []) + list(case.get("turns") or []))
               for e in occasion.get("expectations") or []]
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
                frozen, unit=case["unit"], recorded_wake_id=wake_id,
                at_ms=wake.get("at_ms"), step=wake.get("step"),
                wake_id=wake.get("wake_id"), session_id=wake.get("session_id"))
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
        turns = row.get("turns") or []
        declared_turns = list(case.get("turns") or [])
        counts["turns"] += len(declared_turns)
        if len(turns) != len(declared_turns):
            errors.append(f"turn report denominator mismatch: {case['name']}")
        for index, declared_input in enumerate(declared_turns):
            criteria = [] if inspect_only else [
                e for e in case["expectations"] if e.get("turn") == index]
            counts["criteria"] += len(criteria)
            counts["judge_answers"] += 2 * len(criteria)
            turn = turns[index] if index < len(turns) else {}
            if turn.get("turn") != index:
                errors.append(f"turn report identity mismatch: "
                              f"{case['name']}:{index}")
            name = f"turn-{index}.json"
            if name in files:
                errors.append("ambiguous frozen turn: " + name)
            files.add(name)
            try:
                frozen = json.loads((output / name).read_text())
                if not isinstance(frozen, dict):
                    raise ValueError
            except (OSError, ValueError):
                errors.append("missing or invalid frozen turn: " + name)
                continue
            identity = frozen_turn_identity(
                frozen, unit=case["unit"], turn=index,
                declared_input=declared_input, step=turn.get("step"),
                session_id=turn.get("session_id"))
            if identity is not None:
                errors.append(f"{identity}: {name}")
            if judgments:
                actual = turn.get("expectations") or []
                if _judgments(actual) != Counter(_criterion(e) for e in criteria):
                    errors.append("criterion denominator mismatch: " + name)
                if any(len(e.get("answers") or []) != 2 for e in actual):
                    errors.append("judge answer denominator mismatch: " + name)
                if json.dumps(frozen.get("expectations", []), sort_keys=True, allow_nan=False) != (
                        json.dumps(actual, sort_keys=True, allow_nan=False)):
                    errors.append("frozen/report judgments differ: " + name)
    return {"status": "ERROR" if errors else "PASS", "expected": counts,
            "observed": observed, "errors": list(dict.fromkeys(errors))}


def _canonical_outcome(trajectory: Any, *, unit: Any, wake_id: Any,
                       session_id: Any) -> tuple[str, str | None]:
    """This wake's settlement row in its captured effects: (state, ref)."""
    effects = trajectory.get("effects") if isinstance(trajectory, dict) else None
    items = effects.get("items") if isinstance(effects, dict) else None
    if (not isinstance(items, list) or effects.get("status") != "observed"
            or not isinstance(unit, str) or effects.get("unit") != unit):
        return "absent", None
    for item in items:
        if not isinstance(item, dict) \
                or item.get("source") != "cognition_wake_outcome":
            continue
        row = item.get("row")
        if not isinstance(row, dict) \
                or not (type(wake_id) is int and type(row.get("wake_id")) is int
                        and row["wake_id"] == wake_id):
            continue
        ref = item.get("ref")
        ref = ref if isinstance(ref, str) and ref else None
        if row.get("unit") != unit:
            return "foreign", ref
        if row.get("outcome") != "closed":
            return "failed", ref
        if session_id is None:
            same_session = "session_id" in row and row["session_id"] is None
        else:
            same_session = (type(session_id) is int
                            and type(row.get("session_id")) is int
                            and row["session_id"] == session_id)
        if not same_session:
            return "foreign", ref
        return ("closed", ref) if ref is not None else ("absent", None)
    return "absent", None


def outcome(report: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    """The three result axes, from the same rows the denominators counted.

    Execution answers whether every expected wake ran and settled — a
    wake counts only on the closed `cognition_wake_outcome` its own
    captured effects recorded for its candidate wake, unit and session
    (NULL for a typed no-session); a failed or foreign receipt is an
    error, a missing one stays unestablished, never assumed. Validity
    answers whether the measurement reached its consumers intact:
    coverage plus the evidence receipt together, never paired event
    counts alone. Behaviour carries the protocol verdict counts under
    the path's own claim scope — `UNMEASURED` as a production behaviour
    claim.
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
        inspect_only = run.get("mode") == "inspect-only"
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
                    continue
                state, ref = _canonical_outcome(
                    wake.get("trajectory"), unit=case.get("unit"),
                    wake_id=wake.get("wake_id"),
                    session_id=wake.get("session_id"))
                if state == "closed":
                    settled += 1
                elif state in ("failed", "foreign"):
                    errored += 1
                    causes.append(
                        f"{case.get('name')}:{index} canonical outcome "
                        f"{state}: {ref or 'uncited'}")
                elif (inspect_only
                      and any(v for v in
                              ((wake.get("trajectory") or {}).get("blocks") or {}).values())):
                    # Inspect-only has no session to settle: its positive
                    # claim is the inspection's own assembled context.
                    settled += 1
                else:
                    unestablished += 1
                    causes.append(
                        f"{case.get('name')}:{index} has no closed "
                        "canonical outcome")
            turns = (rows.get(case.get("name")) or {}).get("turns") or []
            for index in range(len(case.get("turns") or ())):
                turn = turns[index] if index < len(turns) else None
                if not isinstance(turn, dict):
                    unestablished += 1
                    continue
                step = turn.get("step")
                if step not in (None, "report"):
                    errored += 1
                    causes.append(
                        f"{case.get('name')}:turn-{index} failed at {step}: "
                        f"{turn.get('error')}")
                    continue
                # Only confirmed delivery, recorded abstention or declined engagement counts.
                if turn.get("outcome") in ("delivered",
                                           "intentional_non_delivery",
                                           "recorded"):
                    settled += 1
                else:
                    unestablished += 1
                    causes.append(
                        f"{case.get('name')}:turn-{index} has no typed "
                        "turn outcome")
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
        occasions = (list(case.get("wakes") or [])
                     + list(case.get("turns") or [])) \
            if isinstance(case, dict) else []
        for occasion in occasions:
            for entry in occasion.get("expectations") or []:
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
            or report["outcome"].get("cause") == "outcome projection failed" \
            or report["outcome"]["execution"]["status"] != "PASS" \
            or report["outcome"]["validity"]["status"] != "PASS":
        # A required axis still unknown or errored is never a report PASS.
        report["status"] = "ERROR"
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
