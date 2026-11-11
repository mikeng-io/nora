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
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any

from evals.subject_config import materialise
from nora.config import load
from nora.infra.sql.engine import create_sql_engine
from nora.policy import load_policy
from nora.subject.eval_evidence import (
    RunEvidence,
    evaluation_export_for,
    fingerprint,
    read_events,
    revision,
    validate_events,
)
from scripts.cognition_loop.cases import Case, load_case
from scripts.cognition_loop.run import run_case
from scripts.cognition_loop.verdicts import ERROR, PASS, rollup
from scripts.memory_graph_spike import (
    Judges,
    measured_run_budget,
    private_directory,
    snapshot_only,
)

#: A cognition session's token spend per wake, as a planning bound; the
#: measured `RunBudget` cut-off still applies at `stop_after_total_tokens`.
DEFAULT_TOKENS_PER_WAKE = 200_000


_REPO = Path(__file__).resolve().parents[2]


def cases_in(directory: Path) -> list[Case]:
    cases = [load_case(p) for p in sorted(directory.glob("*.json"))]
    if not cases:
        raise ValueError(f"no cases under {directory}")
    return cases


def token_budget(cases: list[Case], per_wake: int) -> int:
    return sum(len(c.wakes) for c in cases) * per_wake


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


def run_status(cases: list[dict[str, Any]], evidence: dict[str, Any]) -> str:
    """The run's verdict: the cases' own PASS/FAIL/REVIEW/ERROR rolled up
    fail-closed, plus ERROR for unreadable or invalid evidence — never a
    collapsed "done"."""
    statuses = [
        c.get("status") if c.get("status") in {PASS, "FAIL", "REVIEW", ERROR}
        else ERROR
        for c in cases
    ]
    if evidence.get("status") != PASS:
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
            f"wake(s) at {args.per_wake_tokens}/wake; the budget is "
            f"{args.token_budget} — refuse rather than start"
        )

    # The config under test is the run's --config: materialise it into
    # `config.evaluation.toml`, refusing when the generated file differs
    # from the deployment — the drift-check the standard eval entry runs.
    generated = materialise(
        example=_REPO / "config.example.toml",
        deployment=args.config,
        target=_REPO / "config.evaluation.toml",
    )

    from opentelemetry import trace

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
    args.deployment_workspace = cfg.honcho.workspace_id
    args.run_id = f"cl-{uuid.uuid4().hex[:8]}"
    export = evaluation_export_for(args.config)
    _prefill_capture()
    with TemporaryFile() as lock:
        evidence = RunEvidence(
            run_id=f"cognition-loop-{uuid.uuid4().hex}",
            metadata={
                **revision(_REPO),
                "config_sha256": fingerprint(args.config),
                "subject_config_sha256": fingerprint(generated),
                "honcho_workspace": "per-case draw",
                "token_budget": budget,
            },
            lock_fd=lock.fileno(),
            private_root=output,
        )
        report: dict[str, Any] = {"status": "ERROR"}
        try:
            with evidence.capture(trace.get_tracer_provider(), export=export):
                source = create_sql_engine(args.database_url)
                try:
                    policy = load_policy(args.policy)
                    with measured_run_budget(args, evidence, output, report):
                        report["cases"] = [
                            await run_case(
                                args, case, source, judges, policy,
                                evidence, output,
                            )
                            for case in cases
                        ]
                finally:
                    await source.dispose()
        finally:
            try:
                receipt = validate_events(read_events(evidence.path))
            except Exception:  # noqa: BLE001 — unreadable evidence is an ERROR receipt
                receipt = {"status": "ERROR", "causes": ["unreadable_record"]}
            report["evidence"] = {**receipt, "ref": evidence.path.name}
            report["status"] = run_status(
                report.get("cases", []), report["evidence"])
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n"
    )
    return report


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
    parser.add_argument("--judge-a", default="nora-judge")
    parser.add_argument("--judge-b", default="nora-judge-2")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--per-wake-tokens", type=int,
                        default=DEFAULT_TOKENS_PER_WAKE)
    parser.add_argument("--token-budget", type=int, default=4_000_000)
    parser.add_argument("--stop-after-tokens", type=int)
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
    report = asyncio.run(run(args))
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
