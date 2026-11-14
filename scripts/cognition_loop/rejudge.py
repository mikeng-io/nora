"""`python -m scripts.cognition_loop.rejudge` — judge a finished run again.

Each invocation creates a private version under `rejudgements/`, retaining
the source report and frozen wakes unchanged. Only the two judges run;
no session or sandbox is restored. The after-state comes from the frozen diff.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nora.config import load
from scripts.cognition_loop.accounting import manifest, reconcile
from scripts.cognition_loop.accounting import write_report as _write_report
from scripts.cognition_loop.cases import load_case
from scripts.cognition_loop.html import render
from scripts.cognition_loop.judges import judge_expectation
from scripts.cognition_loop.state import Entry, KindDiff, StateDiff, UnitState
from scripts.cognition_loop.verdicts import ERROR, WAKE_ERRED, ExpectationVerdict, rollup
from scripts.memory_graph_spike import Judges

_STATUSES = ("created", "revised", "retired", "unchanged")


def _measurement(output: Path, case_path: Path) -> tuple[Path, dict[str, Any]]:
    """Create a private measurement version without changing source artifacts."""
    target = output / "rejudgements" / uuid.uuid4().hex
    target.mkdir(mode=0o700, parents=True)
    for path in [output / "report.json", output / "run_budget.json",
                 *output.glob("wake-*.json")]:
        if path.is_file():
            copied = target / path.name
            shutil.copyfile(path, copied)
            copied.chmod(0o600)
    shutil.copyfile(case_path, target / "case.json")
    (target / "case.json").chmod(0o600)
    report = json.loads((target / "report.json").read_text(encoding="utf-8"))
    report["rejudged"] = {
        "source": str(output.resolve()), "output": str(target.resolve()),
        "source_status": report.get("status"),
        "case_sha256": hashlib.sha256((target / "case.json").read_bytes()).hexdigest(),
        "source_report_sha256": hashlib.sha256((target / "report.json").read_bytes()).hexdigest(),
    }
    return target, report


def frozen_diff(frozen: dict[str, Any]) -> tuple[StateDiff, UnitState]:
    kinds: dict[str, KindDiff] = {}
    after: dict[str, dict[str, Any]] = {}
    for kind, parts in (frozen.get("diff") or {}).items():
        entries = {s: tuple(Entry(**e) for e in parts.get(s) or ()) for s in _STATUSES}
        kinds[kind] = KindDiff(kind, **entries)
        after[kind] = {
            e.entry_id: e.after or {}
            for s in ("created", "revised", "unchanged") for e in entries[s]
        }
    return StateDiff(kinds), UnitState(kinds=after)


def unmeasured(frozen: dict[str, Any], recorded: dict[str, Any]) -> bool:
    """A wake judging again cannot clear: a step failed before the session
    settled, or no after-state was frozen to judge against."""
    step = (frozen.get("error") or {}).get("step") or recorded.get("step")
    return "diff" not in frozen or step not in (None, "report") or (
        step is None and recorded.get("status") == ERROR
        and not recorded.get("expectations"))


def case_status(row: dict[str, Any], statuses: list[str]) -> str:
    """Judging again never clears a step that failed: case errors roll up."""
    if not statuses:
        return ERROR
    kept = [e for e in row.get("errors") or [] if e != WAKE_ERRED]
    return rollup(*statuses, *([ERROR] if kept else []))


def run_status_after(report: dict[str, Any]) -> str:
    """The run's status with its own errors and evidence receipt kept."""
    evidence_ok = (report.get("evidence") or {}).get("status") == "PASS"
    return rollup(
        *(c.get("status") or ERROR for c in report.get("cases") or [{}]),
        *([] if evidence_ok and not report.get("errors") else [ERROR]))


async def rejudge(output: Path, case_path: Path, config: Path,
                  judge_a: str, judge_b: str) -> dict[str, Any]:
    output, report = _measurement(output, case_path)
    case = load_case(output / "case.json")
    report["rejudged"]["judges"] = [judge_a, judge_b]
    recorded = report.get("cases")
    rows = [c for c in recorded if isinstance(c, dict) and c.get("case") == case.name] if (
        isinstance(recorded, list)) else []
    errors = reconcile(report, output, judgments=False)["errors"]
    if len(rows) != 1:
        errors.append("case report identity mismatch")
    if not case.expectations:
        errors.append("no judging criteria")
    expected = report.get("expected_cases")
    planned = [c for c in expected if isinstance(c, dict) and c.get("name") == case.name] if (
        isinstance(expected, list)) else []
    if planned != manifest([case]):
        errors.append("case differs from expected manifest")
    if errors:
        for row in rows:
            row["status"] = ERROR
            row.setdefault("errors", []).extend(errors)
        report.setdefault("errors", []).extend(errors)
        report["status"] = ERROR
        _write_report(output, report)
        render(output)
        return report
    row = rows[0]
    judges = Judges(load(config), [judge_a, judge_b], config)
    statuses = []
    earlier_wakes: tuple[tuple[Any, dict[str, Any]], ...] = ()
    for index, wake_id in enumerate(case.wakes):
        path = output / f"wake-{index}-{wake_id}.json"
        frozen = json.loads(path.read_text(encoding="utf-8"))
        diff, after = frozen_diff(frozen)
        wakes = row.get("wakes") or []
        recorded = wakes[index] if index < len(wakes) else {}
        # The case names the unit; an old freeze never captured the rooms
        # that belong to it, and null there means unknown, never empty.
        trajectory = {**(frozen.get("trajectory") or {}),
                      "at_ms": recorded.get("at_ms"), "unit": case.unit}
        judged = []
        # A wake a step failure or a missing after-state cannot judge:
        # it stays ERROR and no judge call is spent on it.
        if not unmeasured(frozen, recorded):
            for expectation in case.expectations:
                if expectation.wake != index:
                    continue
                try:
                    verdict = await judge_expectation(
                        judges, (judge_a, judge_b), expectation, diff, after,
                        trajectory, earlier_wakes)
                except Exception as exc:  # noqa: BLE001 — a judge failure is ERROR
                    verdict = ExpectationVerdict(
                        verdict=ERROR, answers=(), detail=f"judge: {exc!r}")
                judged.append({
                    "expectation": {"polarity": expectation.polarity,
                                    "kind": expectation.kind,
                                    "text": expectation.text},
                    "verdict": verdict.verdict,
                    "detail": verdict.detail,
                    "answers": [asdict(a) for a in verdict.answers],
                })
        frozen["expectations"] = judged
        path.write_text(json.dumps(frozen, ensure_ascii=False, default=str),
                        encoding="utf-8")
        status = rollup(ERROR if unmeasured(frozen, recorded) else "PASS",
                        *(j["verdict"] for j in judged))
        if index < len(row.get("wakes") or []):
            row["wakes"][index]["status"] = status
            row["wakes"][index]["expectations"] = judged
        statuses.append(status)
        if not unmeasured(frozen, recorded):
            earlier_wakes += ((diff, trajectory),)
    row["status"] = case_status(row, statuses)
    report["status"] = run_status_after(report)
    _write_report(output, report)
    render(output)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.cognition_loop.rejudge")
    parser.add_argument("output", type=Path)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--judge-a", default="nora-judge")
    parser.add_argument("--judge-b", default="nora-judge-2")
    args = parser.parse_args(argv)
    report = asyncio.run(rejudge(args.output, args.case, args.config,
                                 args.judge_a, args.judge_b))
    print(report["status"])
    print(report["rejudged"]["output"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
