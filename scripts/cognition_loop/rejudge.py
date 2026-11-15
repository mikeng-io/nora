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
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from opentelemetry import trace

from evals.backbone import _prefill_capture
from evals.services import load_services
from nora.config import load
from nora.subject.eval_evidence import EvidenceError, RunEvidence, read_events, validate_events
from nora.subject.sandbox import eval_host_lock
from scripts.cognition_loop.accounting import (
    frozen_wake_identity,
    manifest,
    reconcile,
)
from scripts.cognition_loop.accounting import write_report as _write_report
from scripts.cognition_loop.cases import load_case
from scripts.cognition_loop.html import render
from scripts.cognition_loop.judges import (
    JUDGE_TOKENS_PER_CALL,
    evidence_gap,
    judge_expectation,
)
from scripts.cognition_loop.state import Entry, KindDiff, StateDiff, UnitState
from scripts.cognition_loop.verdicts import ERROR, WAKE_ERRED, ExpectationVerdict, rollup
from scripts.memory_graph_spike import Judges, measured_run_budget

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
        *([] if evidence_ok and not report.get("errors") else [ERROR]),
        *([] if report.get("rejudge_evidence", {"status": "PASS"}).get("status") == "PASS"
          else [ERROR]))


def _preflight(report: dict[str, Any], output: Path, case: Any) -> tuple[list, list[str]]:
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
    return rows, errors


async def _judge_wakes(output: Path, row: dict[str, Any], case: Any,
                       judges: Any, models: tuple[str, str]) -> None:
    statuses = []
    earlier_wakes: tuple[tuple[Any, dict[str, Any]], ...] = ()
    for index, wake_id in enumerate(case.wakes):
        path = output / f"wake-{index}-{wake_id}.json"
        frozen = json.loads(path.read_text(encoding="utf-8"))
        diff, after = frozen_diff(frozen)
        wakes = row.get("wakes") or []
        recorded = wakes[index] if index < len(wakes) else {}
        identity = frozen_wake_identity(
            frozen, unit=case.unit, recorded_wake_id=wake_id,
            at_ms=recorded.get("at_ms"), step=recorded.get("step"),
            wake_id=recorded.get("wake_id"),
            session_id=recorded.get("session_id"))
        # The case names the unit; an old freeze never captured the rooms
        # that belong to it, and null there means unknown, never empty.
        trajectory = {**(frozen.get("trajectory") or {}),
                      "at_ms": recorded.get("at_ms"), "unit": case.unit}
        judged = []
        # A step failure, a missing after-state, or a canonical evidence
        # gap cannot judge: ERROR with its cause durable, no judge call
        # spent, and the invalidity carried into history — never empty.
        stale = unmeasured(frozen, recorded)
        gap = evidence_gap(trajectory, earlier_wakes)
        judgeable = identity is None and not stale and not gap
        if identity is not None:
            cause = identity
        elif stale:
            cause = (f"frozen wake is unmeasured at step "
                     f"{(frozen.get('error') or {}).get('step', 'unknown')}")
        else:
            cause = "; ".join(gap)
        if cause:
            row.setdefault("errors", []).append(
                f"wake {index} (recorded {wake_id}): {cause}")
        if judgeable:
            for expectation in case.expectations:
                if expectation.wake != index:
                    continue
                try:
                    verdict = await judge_expectation(
                        judges, models, expectation, diff, after,
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
        if not judgeable and "error" not in frozen:
            frozen["error"] = {"step": "evidence", "message": cause}
        path.write_text(json.dumps(frozen, ensure_ascii=False, default=str),
                        encoding="utf-8")
        status = rollup(ERROR if not judgeable else "PASS",
                        *(j["verdict"] for j in judged))
        if index < len(row.get("wakes") or []):
            row["wakes"][index]["status"] = status
            row["wakes"][index]["expectations"] = judged
        statuses.append(status)
        earlier_wakes += ((diff, trajectory) if judgeable
                          else (None, {"invalid": cause}),)
    row["status"] = case_status(row, statuses)


def _failed(report: dict[str, Any], rows: list, errors: list[str]) -> None:
    for row in rows:
        row["status"] = ERROR
        row.setdefault("errors", []).extend(errors)
    report.setdefault("errors", []).extend(errors)
    report["status"] = ERROR


async def _isolated_judging(output: Path, report: dict[str, Any], case: Any,
                           config: Path, models: tuple[str, str], services: Path | None) -> None:
    inputs = load_services(services) if services is not None else None
    if inputs is None:
        raise EvidenceError("external_responses_missing: " + case.name)
    recording = inputs.corpora.get("cognition-rejudge", {}).get(case.name)
    if recording is None:
        raise EvidenceError("external_responses_missing: " + case.name)
    cfg = load(config, require_secrets=False)
    _prefill_capture()
    with eval_host_lock() as lock_fd:
        evidence = RunEvidence(run_id="cognition-rejudge", metadata={
            "mode": "protocol-rehearsal", "behavior": "UNMEASURED",
            "source": report["rejudged"], "judges": models,
        }, lock_fd=lock_fd, private_root=output)
        report["rejudge_evidence"] = {"status": ERROR, "path": str(evidence.path)}
        measured: dict[str, Any] = {}
        requests = 2 * len(case.expectations)
        cutoff = requests * JUDGE_TOKENS_PER_CALL
        try:
            with (evidence.capture(trace.get_tracer_provider()),
                  measured_run_budget(SimpleNamespace(stop_after_tokens=cutoff), evidence,
                                      evidence.path, measured, max_requests=requests,
                                      max_output_tokens=requests * 4096,
                                      deadline=time.monotonic() + requests
                                      * cfg.llm.timeout_seconds)):
                inputs.record(evidence)
                with evidence.case(case.name, input=manifest([case]), metadata={"frozen": True}):
                    async with recording.open("sqlite://") as environment:
                        judges = Judges(environment.bind_config(cfg), list(models), config)
                        row = next(r for r in report["cases"] if r["case"] == case.name)
                        await _judge_wakes(output, row, case, judges, models)
                        evidence.result(row)
                    evidence.exit_code = 0 if row["status"] == "PASS" else 1
        finally:
            report["rejudge_run_budget"] = measured.get("run_budget")
            try:
                receipt = validate_events(read_events(evidence.path))
            except EvidenceError as exc:
                receipt = {"status": ERROR, "causes": [str(exc)]}
            report["rejudge_evidence"] = {**receipt, "path": str(evidence.path)}


async def rejudge(output: Path, case_path: Path, config: Path,
                  judge_a: str, judge_b: str, *, services: Path | None = None) -> dict[str, Any]:
    output, report = _measurement(output, case_path)
    case = load_case(output / "case.json")
    report["rejudged"].update(case=case.name, judges=[judge_a, judge_b], mode="protocol-rehearsal",
                               behavior="UNMEASURED", execution=ERROR)
    report["rejudge_evidence"] = {"status": ERROR, "causes": ["not_started"]}
    rows, errors = _preflight(report, output, case)
    try:
        if errors:
            _failed(report, rows, errors)
        else:
            await _isolated_judging(output, report, case, config, (judge_a, judge_b), services)
            report["rejudged"]["execution"] = "PASS"
            report["status"] = run_status_after(report)
    except BaseException as exc:
        _failed(report, rows, [f"rejudge: {type(exc).__name__}: {exc}"])
        if not isinstance(exc, Exception):
            raise
    finally:
        _write_report(output, report)
        render(output)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.cognition_loop.rejudge")
    parser.add_argument("output", type=Path)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--services", type=Path,
                        help="private cognition-rejudge/case HTTP recordings; protocol only")
    parser.add_argument("--judge-a", default="nora-judge")
    parser.add_argument("--judge-b", default="nora-judge-2")
    args = parser.parse_args(argv)
    report = asyncio.run(rejudge(args.output, args.case, args.config,
                                 args.judge_a, args.judge_b, services=args.services))
    print(report["status"])
    print(report["rejudged"]["output"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
