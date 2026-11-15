"""`python -m scripts.cognition_loop` — the Cognition loop eval.

Reads case files (private, host-side), restores a per-case sandbox from a
read-only clone of the corpus, runs each case's recorded wakes in order
through the deployment's live Cognition session, diffs every owned store
around each wake, and rolls two judges' answers up fail-closed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tomllib
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from evals.services import load_services
from evals.subject_config import materialise
from nora.config import load
from nora.infra.sql.engine import create_sql_engine
from nora.policy import load_policy
from nora.subject.eval_evidence import (
    RunEvidence,
    fingerprint,
    read_events,
    revision,
    validate_events,
)
from nora.subject.sandbox import close_private_blank_templates, eval_host_lock
from scripts.cognition_loop import html as report_html
from scripts.cognition_loop.accounting import manifest
from scripts.cognition_loop.accounting import write_report as _write_report
from scripts.cognition_loop.cases import Case, load_case
from scripts.cognition_loop.judges import JUDGE_TOKENS_PER_CALL
from scripts.cognition_loop.run import run_case
from scripts.cognition_loop.verdicts import ERROR, PASS, rollup
from scripts.memory_graph_spike import (
    Judges,
    measured_run_budget,
    private_directory,
    snapshot_only,
)

#: A cognition session's token spend per wake — the measured live figure.
DEFAULT_TOKENS_PER_WAKE = 800_000


_REPO = Path(__file__).resolve().parents[2]


def cases_in(directory: Path) -> list[Case]:
    cases = [load_case(p) for p in sorted(directory.glob("*.json"))]
    if not cases:
        raise ValueError(f"no cases under {directory}")
    return cases


def token_budget(cases: list[Case], per_wake: int) -> int:
    """The run's stated budget: wakes x per-wake plus the judge allowance
    (every expectation judged by two judges)."""
    return (
        sum(len(c.wakes) for c in cases) * per_wake
        + sum(len(c.expectations) for c in cases) * 2 * JUDGE_TOKENS_PER_CALL
    )


def parse_overrides(pairs: list[str]) -> dict[str, Any]:
    """`--override key=value` pairs, values TOML-typed (`a.b = 1` parses 1,
    not "1")."""
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--override takes key=value, got {pair!r}")
        out[key.strip()] = tomllib.loads(f"x = {value}")["x"]
    return out


def run_status(
    cases: list[dict[str, Any]], evidence: dict[str, Any],
    errors: list[dict[str, Any]] | None = None,
) -> str:
    """The run's verdict: the cases' own PASS/FAIL/REVIEW/ERROR rolled up
    fail-closed, plus ERROR for a failed step or unreadable evidence —
    never a collapsed "done"."""
    statuses = [
        c["status"] if c.get("status") in {PASS, "FAIL", "REVIEW", ERROR}
        else ERROR
        for c in cases
    ]
    if evidence.get("status") != PASS or errors:
        statuses.append(ERROR)
    return rollup(*statuses) if statuses else ERROR


async def run(args: argparse.Namespace) -> dict[str, Any]:
    cases = [load_case(args.case)] if args.case else cases_in(args.case_dir)
    if args.smoke:
        cases = cases[:1]
    budget = token_budget(cases, args.per_wake_tokens)
    if budget > args.token_budget:
        raise ValueError(
            f"the run states {budget} tokens for {sum(len(c.wakes) for c in cases)} "
            f"wake(s) at {args.per_wake_tokens}/wake plus the judge "
            f"allowance; the budget is {args.token_budget} — refuse "
            "rather than start"
        )
    print("mode: recorded-wake protocol rehearsal; model behavior: UNMEASURED")
    # The stated budget is printed before any model call — the figure the
    # run plans against, never a post-hoc measure.
    print(
        f"stated budget: {budget} tokens = "
        f"{sum(len(c.wakes) for c in cases)} wakes x "
        f"{args.per_wake_tokens}/wake + "
        f"{sum(len(c.expectations) for c in cases)} expectations x 2 judges x "
        f"{JUDGE_TOKENS_PER_CALL}"
    )

    # The config under test is the run's --config: materialise it into
    # `config.evaluation.toml`, refusing when the generated file differs
    # from the deployment — the drift-check the standard eval entry runs.
    generated = materialise(
        example=_REPO / "config.example.toml",
        deployment=args.config,
        target=_REPO / "config.evaluation.toml",
    )

    from evals.backbone import _prefill_capture

    cfg = load(args.config)
    from nora.secret import credentials
    snapshot_only(
        args.database_url, credentials()[cfg.store.database_url_env],
        args.snapshot,
    )
    output = private_directory(args.output)
    judges = None if args.inspect_only else Judges(
        cfg, [args.judge_a, args.judge_b], args.config,
    )
    args.run_id = f"cl-{uuid.uuid4().hex[:8]}"
    export = None
    _prefill_capture()
    report: dict[str, Any] = {
        "status": "ERROR", "errors": [], "expected_cases": manifest(cases)}
    # report.json and report.html are written in EVERY outcome — a failed
    # run (a model error the evidence capture raises on, a crashed case)
    # still leaves its report, its cause named at the top.
    try:
        try:
            args.service_inputs = (load_services(args.services)
                                   if getattr(args, "services", None) else None)
        except (OSError, ValueError, TypeError) as exc:
            report["errors"].append({"step": "external_services", "message": str(exc)})
        else:
            await _run_locked(
                args, cases, judges, export, budget, output, cfg, generated, report)
    finally:
        _write_report(output, report)
        try:
            report_html.render(output)
        except Exception as exc:  # noqa: BLE001 — report.json stands; the run's own error is not masked
            print(f"report.html not rendered: {exc!r}", file=sys.stderr)
            report.setdefault("errors", []).append(
                {"step": "report_html", "message": repr(exc)})
            report["status"] = ERROR
            _write_report(output, report)
    return report


async def _run_cases(
    args: argparse.Namespace, cases: list[Case], source: Any, judges: Any,
    policy: Any, evidence: Any, output: Path, report: dict[str, Any],
) -> None:
    with measured_run_budget(SimpleNamespace(stop_after_tokens=args.token_budget),
                             evidence, output, report):
        report["cases"] = []
        for case in cases:
            try:
                report["cases"].append(await run_case(
                    args, case, source, judges, policy, evidence, output,
                ))
            except Exception as exc:  # noqa: BLE001 — ERROR, never a crash
                report["cases"].append({
                    "status": ERROR, "case": case.name, "unit": case.unit,
                    "errors": [f"the case run failed: {exc!r}"],
                    "wakes": [],
                })
                break


async def _run_locked(
    args: argparse.Namespace, cases: list[Case], judges: Any, export: Any,
    budget: int, output: Path, cfg: Any, generated: Path,
    report: dict[str, Any],
) -> None:
    from opentelemetry import trace

    with eval_host_lock() as lock_fd:
        evidence: Any = None
        try:
            evidence = RunEvidence(
                run_id=f"cognition-loop-{uuid.uuid4().hex}",
                metadata={
                    **revision(_REPO),
                    "config_sha256": fingerprint(args.config),
                    "subject_config_sha256": fingerprint(generated),
                    "external_services": "case-owned",
                    "token_budget": budget,
                },
                lock_fd=lock_fd, private_root=output,
            )
            report["run"] = {
                "run_id": args.run_id,
                "evidence_run_id": getattr(evidence, "run_id", None),
                "mode": "inspect-only" if args.inspect_only else "recorded-wake-protocol",
                "behavior": "UNMEASURED", "claim": "recorded-wake-replay",
                "execution": "inspect-only" if args.inspect_only else "recorded-session",
                "token_budget": budget,
                "per_wake_tokens": args.per_wake_tokens,
                **revision(_REPO),
                "config_sha256": fingerprint(args.config),
                "subject_config_sha256": fingerprint(generated),
            }
            with evidence.capture(trace.get_tracer_provider(), export=export):
                source = create_sql_engine(args.database_url)
                try:
                    if args.service_inputs is not None:
                        args.service_inputs.record(evidence)
                    policy = load_policy(args.policy)
                    await _run_cases(
                        args, cases, source, judges, policy, evidence, output, report)
                finally:
                    try:
                        async with AsyncExitStack() as closing:
                            closing.push_async_callback(close_private_blank_templates)
                            closing.push_async_callback(source.dispose)
                    except BaseException as exc:
                        evidence.record("sql_teardown", status="ERROR", cause=type(exc).__name__)
                        raise
                    else:
                        evidence.record("sql_teardown", status="PASS")
        except BaseException as exc:
            # The evidence cause (e.g. `model_error`) is named at the top
            # of the report the caller's finally still writes.
            report["errors"].append({
                "step": "run",
                "error": type(exc).__name__,
                "message": str(exc) or type(exc).__name__,
            })
            raise
        finally:
            if evidence is not None:
                try:
                    receipt = validate_events(read_events(evidence.path))
                except Exception:  # noqa: BLE001 — unreadable evidence is an ERROR receipt
                    receipt = {"status": "ERROR", "causes": ["unreadable_record"]}
                report["evidence"] = {**receipt, "ref": evidence.path.name}
            else:
                report["evidence"] = {
                    "status": "ERROR", "causes": ["evidence_not_started"]}
            report["status"] = run_status(
                report.get("cases", []), report["evidence"], report["errors"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.cognition_loop")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--snapshot", action="store_true",
                        help="acknowledge the database is a snapshot, never production")
    parser.add_argument("--case", type=Path)
    parser.add_argument("--case-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--services", type=Path,
                        help="private cognition-loop/case HTTP recordings; protocol only")
    parser.add_argument("--judge-a", default="nora-judge")
    parser.add_argument("--judge-b", default="nora-judge-2")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--per-wake-tokens", type=int,
                        default=DEFAULT_TOKENS_PER_WAKE,
                        help="the stated per-wake token figure the run "
                             "budgets against (the measured live figure "
                             "is the default)")
    parser.add_argument("--token-budget", type=int, default=4_000_000)
    parser.add_argument("--stop-after-tokens", type=int,
                        help="refused: the run's model calls are parallel "
                             "and a measured cutoff needs sequential "
                             "calls — it would only fail mid-run")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--override", action="append", default=[],
                        metavar="KEY=VALUE",
                        help="arm override applied to the composed subject's config")
    args = parser.parse_args(argv)
    try:
        args.overrides = parse_overrides(args.override)
    except ValueError as exc:
        parser.error(str(exc))
    if args.case is None and args.case_dir is None:
        parser.error("one of --case or --case-dir is required")
    if args.stop_after_tokens is not None:
        parser.error(
            "--stop-after-tokens is refused: a session's model calls are "
            "parallel, and a measured cutoff only works against sequential "
            "calls — the run would start, then fail mid-wake")
    report = asyncio.run(run(args))
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
