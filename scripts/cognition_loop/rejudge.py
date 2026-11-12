"""`python -m scripts.cognition_loop.rejudge` — judge a finished run again.

Re-runs only the two judges over the frozen wake files of an output
directory, against the case's current expectations, and re-renders
report.html. No session runs and no sandbox is restored: a judge-side fix
is checked for a handful of judge calls, not a whole wake's spend. The
wake's after-state is rebuilt from its frozen diff (created, revised and
unchanged entries at their after values).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nora.config import load
from scripts.cognition_loop.cases import load_case
from scripts.cognition_loop.html import render
from scripts.cognition_loop.judges import judge_expectation
from scripts.cognition_loop.state import Entry, KindDiff, StateDiff, UnitState
from scripts.cognition_loop.verdicts import ERROR, ExpectationVerdict, rollup
from scripts.memory_graph_spike import Judges

_STATUSES = ("created", "revised", "retired", "unchanged")


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


async def rejudge(output: Path, case_path: Path, config: Path,
                  judge_a: str, judge_b: str) -> dict[str, Any]:
    case = load_case(case_path)
    judges = Judges(load(config), [judge_a, judge_b], config)
    report_path = output / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    row = next(c for c in report["cases"] if c.get("case") == case.name)
    statuses = []
    for index, wake_id in enumerate(case.wakes):
        path = output / f"wake-{index}-{wake_id}.json"
        if not path.is_file():
            continue
        frozen = json.loads(path.read_text(encoding="utf-8"))
        diff, after = frozen_diff(frozen)
        judged = []
        for expectation in case.expectations:
            if expectation.wake != index:
                continue
            try:
                verdict = await judge_expectation(
                    judges, (judge_a, judge_b), expectation, diff, after,
                    frozen.get("trajectory") or {})
            except Exception as exc:  # noqa: BLE001 — a judge failure is ERROR
                verdict = ExpectationVerdict(
                    verdict=ERROR, answers=(), detail=f"judge: {exc!r}")
            judged.append({
                "expectation": {"polarity": expectation.polarity,
                                "kind": expectation.kind,
                                "text": expectation.text},
                "verdict": verdict.verdict,
                "answers": [asdict(a) for a in verdict.answers],
            })
        frozen["expectations"] = judged
        path.write_text(json.dumps(frozen, ensure_ascii=False, default=str),
                        encoding="utf-8")
        status = rollup(ERROR if frozen.get("error") else "PASS",
                        *(j["verdict"] for j in judged))
        if index < len(row.get("wakes") or []):
            row["wakes"][index]["status"] = status
            row["wakes"][index]["expectations"] = judged
        statuses.append(status)
    row["status"] = rollup(*statuses) if statuses else ERROR
    report["status"] = rollup(*(c.get("status") or ERROR for c in report["cases"]))
    report["rejudged"] = {"judges": [judge_a, judge_b], "case": str(case_path)}
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False,
                                      default=str) + "\n", encoding="utf-8")
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
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
