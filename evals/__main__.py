"""`python -m evals CORPUS...` — run selected corpora, print the measurements.

Runs whichever corpora you name, not "everything." A full run over every case
is hours of judged samples, so `--corpus` / `--class` / `--all` are three ways
to say how much you actually want.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals import pseudonyms
from evals.accounting import accepted, accounted, describe_outcome
from evals.backbone import (
    NORA_ERROR,
    NORA_POSITION,
    NORA_SHOWN,
    NORA_WORKING_SET,
    PASS,
    RUN_ID,
    Spec,
    _prefill_capture,
    drain_spend,
    nora_task,
    retain_report,
)
from evals.cli import BUDGET_MINUTES, eval_parser, run_budget_for
from evals.grading_contract import GradingContract
from evals.journey_cohort import bound_selection
from evals.measurement import PROTOCOL, Measurement
from evals.reading import base_name, read, sample_read
from evals.rebuilt.grading import DEFAULT_SAMPLES, Judge
from evals.scenarios import (
    CORPUS_ENTRY,
    FULL_RESTORE_CORPORA,
    JOURNEY_CORPORA,
    JUDGE_REQUIRED,
    JUDGED_CORPORA,
    census,
    corpus_entry,
    experiment_for,
    load_corpus,
    selected_cases,
)
from evals.services import load_services
from evals.subject_config import materialise
from evals.terminal import safe_print as print
from nora.subject import (
    EMBEDDER_FROM_HOST,
    UNRESOLVED,
    SharedDatabaseMutatedError,
    SourceRevisionMismatchError,
    assert_shared_databases_unchanged,
    check_served_model,
    close_private_blank_templates,
    config_default,
    refuse_if_source_diverges_from_head,
    shared_database_versions,
    source_coverage,
    sweep_stale_private_blank_templates,
)
from nora.subject.context_filter import exposure
from nora.subject.eval_evidence import (
    EvidenceError,
    RunEvidence,
    evaluation_export_for,
    fingerprint,
    revision,
)
from nora.subject.recorded_services import RecordedServices
from nora.subject.sandbox import EvalLockHeldError, eval_host_lock

#: Coarse S-class grouping for `--class`. Approximate on purpose where a
#: corpus is not wholly one class — good enough to pick
#: "which corpora" without re-deriving the map's own nuance here.
#: S2/S4/S5 have no corpus at all (hand-written `Case`s only, not part of
#: `evals/corpora/`), so they are not selectable through this script.
CORPUS_CLASS: dict[str, str] = {
    "proactive_act": "S1",
    "being_contradicted": "S3",
    # What a held lesson does to a reply (judged).
    "lessons": "S3",
    "identity_grounding": "S3",
    "source_scope": "S3",
    # (ADR 0015) what she SAYS after a tool returns (judged).
    "tool_reporting": "S3",
    # S2, tool routing: which tool EXECUTED.
    "reply_delivery": "S2", "tool_selection": "S2",
    "no_trigger": "S7",
    "near_miss": "S7",
    "trigger": "S7",
    "relevance_gate": "S7",
    "participation": "S7",
    "injections": "S9",
    "transcript_injection": "S9",
    "presence": "S3",
    "appearance": "S3",
    # The character cut's instrument: whether an ordinary reply still sounds like her (judged).
    "personality": "S3",
    # Is what she REMEMBERS supported (judged).
    "conversational_recall": "S3",
    # What a WRITE keeps: `carry` is an unrelated pass losing a thread it did
    # not touch, the nearest class the map has. Its `stale`
    # cases are S5-shaped (time misread) — approximate, as this map says.
    "standing": "S6",
    "standing_unit": "S6",
}

#: The entry points that honour `metadata.print_input` — render the model
#: call's input, record it as `shown`, and call no model.
_PRINTS_INPUT = frozenset({"standing", "standing_unit"})


#: What one draw costs, in seconds, for the run budget below. Unjudged is
#: MEASURED on one corpus only: the mean draw of `tool_selection
#: --sample 3` on the deploy host (n=259 draws, 46 s, of which
#: a mean 15.7 s was the sandbox's index build, which a repeat draw reuses,
#: `nora.subject.sandbox_index.IndexCache`). Other corpora and configs
#: use the same estimate. Judged is ~211 s a draw
#: at the default judge samples (`.agents/skills/nora-eval`); the judge's
#: share of it, 211 - 46, is split evenly per judge sample, so
#: `--judge-samples` scales it.
UNJUDGED_DRAW_SECONDS = 46
JUDGED_DRAW_SECONDS = 211
JUDGE_SAMPLE_SECONDS = (JUDGED_DRAW_SECONDS - UNJUDGED_DRAW_SECONDS) / DEFAULT_SAMPLES

#: The longest run `python -m evals` starts without `--allow-long`. The deploy
#: host has ONE eval lock (`/tmp/nora-eval.lock`), shared by every session:
#: a run that holds it past an hour blocks every other session's PR.




@dataclass(frozen=True)
class Estimate:
    """A run's size and its cost bound, before any draw."""

    cases: int
    sample: int
    draws: int
    seconds: int

    @property
    def minutes(self) -> float:
        return self.seconds / 60

    def line(self) -> str:
        return (
            f"estimate: {self.cases} cases x {self.sample} samples x 1 arm = "
            f"{self.draws} draws, ~{self.minutes:.0f} min"
        )


def estimate(
    selection: list[tuple[int, bool]], sample: int, judge_samples: int = DEFAULT_SAMPLES,
) -> Estimate:
    """`selection` is one `(cases, judged)` per corpus the run draws."""
    cases = sum(n for n, _ in selection)
    judged_draw = UNJUDGED_DRAW_SECONDS + judge_samples * JUDGE_SAMPLE_SECONDS
    seconds = round(sum(
        n * sample * (judged_draw if judged else UNJUDGED_DRAW_SECONDS)
        for n, judged in selection
    ))
    return Estimate(cases=cases, sample=sample, draws=cases * sample, seconds=seconds)


def over_budget(est: Estimate, *, allow_long: bool) -> str | None:
    """Why a run this size is refused, or None when it may start."""
    if allow_long or est.minutes <= BUDGET_MINUTES:
        return None
    return (
        f"~{est.minutes:.0f} min is over the {BUDGET_MINUTES}-minute budget. The "
        "deploy host's eval lock is shared, so a run that holds it past an hour "
        "blocks every other session. Narrow it with --case or --class, or lower "
        "--sample; pass --allow-long only when the operator asked for a full run"
    )


def _list_corpora() -> None:
    for name in sorted([*CORPUS_ENTRY, *JOURNEY_CORPORA]):
        judged = ("journey; --journey-cohort + --services worlds; --run-budget"
                  " required; --measure behavioral else behavior UNMEASURED"
                  if name in JOURNEY_CORPORA else
                  "judged" if name in JUDGED_CORPORA else "structural")
        print(f"{name:24} entry={corpus_entry(name):14} "
              f"{CORPUS_CLASS.get(name, '?'):3} {judged}")


_REPO_ROOT = Path(__file__).resolve().parents[1]

#: The clone's name on the deploy host, and the loopback publish its compose
#: file gives the database container (`ports: ["127.0.0.1:5435:5432"]`).
EVAL_CLONE = "nora_eval"
_DEPLOY_DB_HOST = "127.0.0.1"
_DEPLOY_DB_PORT = 5435


def source_url_for(stated: str, deployment_dir: Path) -> str:
    """The source URL, or a refusal.

    Stated wins and no credential file is read. Otherwise the URL is derived
    from the deployment's own credentials — `scripts.secret_delivery.source`'s
    precedence, process environment > `.env` > `secret.toml`, so a
    secret-only deployment still resolves and a present-empty value refuses —
    and the clone's name. On the deploy host the default is right and nowhere
    else is there one. Either way the database NAME must be a clone's: the
    live database is `nora`, and one flag away from running 150 cases' seeds
    and turns into production is not a default anybody should be able to
    reach.
    """
    from sqlalchemy.engine import URL, make_url

    if not stated:
        from scripts.secret_delivery import source

        try:
            password = source(deployment_dir).get("POSTGRES_PASSWORD", "")
        except (OSError, ValueError) as exc:
            raise ValueError(
                "--source-url not given and the deployment's credentials "
                f"under {deployment_dir} are unreadable ({exc}); the default "
                "source is the deploy host's clone, derived from its "
                "secret.toml/.env"
            ) from exc
        if not password:
            raise ValueError(
                "--source-url not given and the deployment's credentials "
                f"under {deployment_dir} state no POSTGRES_PASSWORD"
            )
        stated = URL.create(
            "postgresql+asyncpg",
            username="nora",
            password=password,
            host=_DEPLOY_DB_HOST,
            port=_DEPLOY_DB_PORT,
            database=EVAL_CLONE,
        ).render_as_string(hide_password=False)
    name = make_url(stated).database or ""
    if not name.endswith("_eval"):
        raise ValueError(
            f"--source-url names database {name!r}, which is not a clone "
            f"(`*_eval`). The source is read to seed sandboxes and every "
            "case composes against a sandbox cloned BESIDE it; naming the "
            "deployment's own database here would put 150 cases' seeds on "
            "the same server as production with one rename between them. "
            "Make a clone: scripts/refresh_eval_clone.sh."
        )
    return stated


def _corpora_for(args: argparse.Namespace) -> list[str]:
    if args.all:
        return sorted(CORPUS_ENTRY)
    selected = set(args.corpus or [])
    for cls in args.cls or []:
        selected |= {name for name, c in CORPUS_CLASS.items() if c == cls}
    unknown = selected - set(CORPUS_ENTRY) - JOURNEY_CORPORA
    if unknown:
        print(f"unknown corpus/class selection: {sorted(unknown)}", file=sys.stderr)
        raise SystemExit(2)
    return sorted(selected)


def _print_spend() -> None:
    """What this corpus actually spent, and whether any call was TRUNCATED.

    Printed on every run because it is free — the numbers ride on spans this
    process already captures for `agent.prefill` — and because a truncation is
    otherwise diagnosed by changing a ceiling and re-running while the call's
    own `finish_reason` sits in memory unread.
    """
    spend = drain_spend()
    if not spend["calls"]:
        return
    line = (
        f"  spend: {spend['calls']} model call(s), in={spend['input']} "
        f"(cached {spend['cached']}, fresh {spend['fresh']}), out={spend['output']}"
    )
    if spend["truncated"]:
        line += f"   TRUNCATED: {spend['truncated']}"
    print(line)
    # The SUBJECT'S OWN TURNS only — the calls under her `invoke_agent` span,
    # without background passes the drive also spawns (the standing fold).
    # This is the per-turn unit.
    turn = spend.get("turn") or {}
    if turn.get("turns"):
        n = turn["turns"]
        print(
            f"  turn:  {turn['calls']} call(s) over {n} turn(s) "
            f"({turn['calls'] / n:.2f}/turn), in/turn={turn['input'] // n} "
            f"(fresh {turn['fresh'] // n}, cached {turn['cached'] // n}), "
            f"out/turn={turn['output'] // n}"
        )


def _sweep_stale_private_blanks(source_url: str) -> None:
    """Drop private blank templates (`nora.subject.sandbox._blank_template`'s
    own, `<template>_run_<ts>-<nonce>`) a KILLED run left behind, before this
    run starts. Never fatal: a sweep that cannot
    reach Postgres says so, and this run's own `_blank_template` call would
    hit the exact same connection problem moments later regardless."""
    try:
        swept = asyncio.run(sweep_stale_private_blank_templates(source_url))
    except Exception as exc:  # noqa: BLE001 — reported, never a reading
        print(f"postgres: stale private-blank sweep failed: {type(exc).__name__}: {exc}")
        return
    if swept:
        print(f"postgres: swept {len(swept)} stale private blank template(s) left by a killed run")


def _case_state(case_row: dict[str, Any], name: str) -> Any:
    """One named value off a report row's `actual_environment_state`. A row
    IS the `EvaluationData` the grader saw (`model_dump()`), so the state the
    task recorded rides on it."""
    for entry in case_row.get("actual_environment_state") or ():
        if entry.get("name") == name:
            return entry.get("state")
    return None


def _print_positions(report: Any) -> None:
    """Every draw's WRITTEN position, with its length, and — separately,
    labelled, never folded into the position — its working set.

    The judge's reason says a finding was left out and cannot say WHY: a fold
    that never tried and a fold that was squeezed out by `[standing]
    max_chars` read the same in a reason line. The length against the cap is
    the first thing that tells them apart, and the text is the second. A
    finding that landed in `working_set` instead of `position` is a THIRD
    way a reason can read "left out" while nothing was actually lost, so a
    human reading this output needs to see both to tell the three apart.
    Printed on the terminal only — never paste it into a PR or an issue; it
    is a room's own standing.
    """
    from evals.rebuilt.fold_evidence import render_working_set

    for case_row, outputs in zip(report.cases, report.detailed_results, strict=True):
        position = _case_state(case_row, NORA_POSITION)
        working_set = _case_state(case_row, NORA_WORKING_SET) or ()
        label = "/".join(str(getattr(out, "label", "?")) for out in outputs)
        size = "none" if position is None else f"{len(position)} chars"
        print(f"  [{label}] {case_row['name']}  ({size})")
        if position:
            for line in str(position).splitlines():
                print(f"      | {line}")
        if working_set:
            print(f"      working set ({len(working_set)} item(s), not her position):")
            for line in render_working_set(working_set).splitlines():
                print(f"      | {line}")


def _run_one(
    name: str, *, source_url: str, judge: Judge | None, sample: int,
    only: frozenset[str] | None = None, all_reasons: bool = False,
    positions: bool = False, context_receipts: list[dict] | None = None,
    memory_surface: str = "graph", subject_overrides: dict[str, Any] | None = None,
    contract: GradingContract | None = None, services: dict[str, Any] | None = None,
    cohort: Any = None, measurement: Measurement = PROTOCOL,
) -> bool:
    experiment, task = experiment_for(
        name, source_url=source_url, judge=judge, sample=sample, only=only,
        memory_surface=memory_surface, overrides=subject_overrides,
        contract=contract, services=services,
        cohort=cohort, measurement=measurement,
    )
    report = experiment.run_evaluations(task=task)
    coverage, outcome = accounted(report, experiment, measurement=measurement)
    from nora.subject.eval_evidence import current

    if evidence := current():
        retained = retain_report(evidence, name, report, experiment=experiment,
                                 coverage=coverage, measurement=measurement)
        outcome = retained["outcome"]
        evidence.check()
        evidence.record("corpus_report", **retained)
    context_complete = True
    if context_receipts is not None:
        missing = {c["name"] for c in report.cases} - {
            r.get("case") for r in context_receipts}
        if missing:
            print(f"context experiment ERROR: no measured prefill for {sorted(missing)}")
            context_complete = False
    scope = f" [{len(experiment.cases)} selected]" if only else ""
    print(f"--- {name}: {census(name, cohort=cohort)}{scope} ---")
    print(f"mode: {measurement.phrase}; model behavior: {outcome['behavior']['status']}")
    describe_outcome(coverage, outcome)
    _print_spend()
    if positions:
        _print_positions(report)
    if sample > 1:
        readings = sample_read(report)
        # Keep unresolved judgments distinct from failures and errored draws.
        reasons: dict[str, list[str]] = {}
        for case_row, outputs in zip(report.cases, report.detailed_results, strict=True):
            for out in outputs:
                if all_reasons or getattr(out, "label", None) != PASS:
                    reasons.setdefault(base_name(case_row["name"]), []).append(
                        f"{out.label}: {out.reason}")
        ok = True
        for case_name, r in sorted(readings.items()):
            split = ("" if r.verdict == PASS else
                     f"  [{r.passed} pass / {r.failed} fail / "
                     f"{r.reviewed} review / {r.errored} error]")
            print(f"  {case_name:40} {r.verdict}{split}")
            # One line per DISTINCT reason: three draws failing the same way
            # is one fact, and printing it three times hides that it is.
            for reason in dict.fromkeys(reasons.get(case_name, ())):
                print(f"      {reason}")
            ok = ok and r.verdict == PASS
        return ok and context_complete and accepted(coverage, outcome)
    reading = read(report)
    print(f"  passed={reading.passed} failed={reading.failed} "
          f"reviewed={reading.reviewed} errored={reading.errored}")
    if not reading.accepted:
        for case_row, outputs in zip(report.cases, report.detailed_results, strict=True):
            for out in outputs:
                if getattr(out, "label", None) != PASS:
                    print(f"  {case_row['name']:40} {out.label:6} {out.reason}")
    return (reading.accepted
            and context_complete and accepted(coverage, outcome))


def _print_input(name: str, case_name: str, *, source_url: str,
                 services: dict[str, RecordedServices] | None = None,
                 memory_surface: str = "graph") -> int:
    """Print the declared entry's rendered input under its case-owned services.

    This rendering probe has no behavioral verdict and does not run a journey.
    """
    if corpus_entry(name) not in _PRINTS_INPUT:
        print(
            f"--print-input is honoured by {sorted(_PRINTS_INPUT)} entry points; "
            f"{name!r} enters at {corpus_entry(name)!r}",
            file=sys.stderr,
        )
        return 2
    matches = [c for c in load_corpus(name) if c.name == case_name]
    if not matches:
        print(f"{name}: no active case named {case_name!r}", file=sys.stderr)
        return 2
    [case] = matches
    case = case.model_copy(update={"metadata": {**(case.metadata or {}), "print_input": True}})
    result = nora_task(Spec(
        source_url=source_url, entry=corpus_entry(name),
        full_restore=name in FULL_RESTORE_CORPORA, services=services,
        memory_surface=memory_surface,
    ))(case)
    state = {e.name: e.state for e in result.get("environment_state") or ()}
    shown = state.get(NORA_SHOWN) or ""
    if not shown:
        print(f"nothing was rendered: {state.get(NORA_ERROR)}", file=sys.stderr)
        return 1
    print(shown)
    return 0


def embedder_model_of(config_toml: str) -> str:
    """The stated `[memory.index] embedder_model`, else the code default."""
    stated = tomllib.loads(config_toml).get("memory", {}).get("index", {}).get("embedder_model")
    default = config_default("memory.index.embedder_model")
    if stated is None and default is UNRESOLVED:
        raise ValueError("the embedder model's schema default is unresolvable")
    return str(stated if stated is not None else default)


def graph_surface_lines(source_url: str, model_id: str) -> list[str]:
    """The graph surface's two preconditions, read before the first draw:
    the embedder the subject queries serves `model_id`'s file, and the
    source holds that model's vectors at `COVERAGE_FLOOR`. Raises
    `GraphIndexRefusedError` (the run is refused, never run lexical)."""
    served = asyncio.run(check_served_model(EMBEDDER_FROM_HOST, model_id))
    coverage = asyncio.run(source_coverage(source_url, model_id))
    return [
        f"embedder: {EMBEDDER_FROM_HOST} serves {served}",
        f"source vectors: {coverage.line()}",
    ]


def memory_surface_line(surface: str = "graph") -> str:
    """The run header's account of which eval memory arm it measures, and why."""
    if surface == "off":
        return "off (eval-only control arm; production graph reads stay unconditional)"
    return "graph (the Cognitive Graph's read surface is unconditional)"


def main() -> int:
    """One run per host: the whole run, from its first line to its last, holds
    the host's eval lock (`eval_host_lock`). A run that finds it held is refused
    before it does anything, so no two runs share the shared template."""
    try:
        with eval_host_lock() as lock_fd:
            return _main(lock_fd=lock_fd)
    except (EvalLockHeldError, EvidenceError) as exc:
        print(f"refusing to run: {exc}", file=sys.stderr)
        return 2


def _main(*, lock_fd: int = -1) -> int:
    ap = eval_parser()
    args = ap.parse_args()
    if (args.list or args.print_input) and (
        args.grading_contract or args.grading_contract_sha256
    ):
        ap.error("this zero-dispatch operation does not consume a grading contract")
    if args.list:
        _list_corpora()
        return 0

    names = _corpora_for(args)
    only = frozenset(args.cases) if args.cases else None
    if only and len(names) != 1:
        print(
            "--case names cases OF ONE corpus; pass exactly one corpus name "
            f"(got {names or 'none'})",
            file=sys.stderr,
        )
        return 2
    if not names:
        print("no corpus selected — pass names, --class, --all, or --list", file=sys.stderr)
        return 2
    if (args.context_arm != "full" or args.context_report) and (
        len(names) != 1 or corpus_entry(names[0]) != "tag" or args.print_input
    ):
        print("context experiment requires one tag corpus and no --print-input", file=sys.stderr)
        return 2
    if args.print_input and len(names) != 1:
        print("--print-input names a case OF ONE corpus", file=sys.stderr)
        return 2
    try:
        args.service_inputs = load_services(args.services) if args.services else None
        # THE JOURNEY SELECTION binds and refuses here, before anything
        # composes or a model is called: cohort, per-case worlds, judges,
        # budget, and the measurement claim agreeing with every world.
        args.bound_cohort = bound_selection(args, names, only)
    except (OSError, ValueError, TypeError) as exc:
        print(f"refusing to run: {exc}", file=sys.stderr)
        return 2
    needs_judge = sorted(set(names) & JUDGE_REQUIRED)
    if needs_judge and not args.judge and not args.print_input:
        # Refused BEFORE anything is spent: every case would pay for its
        # model call and then ERROR, because its only reading is the judge's.
        print(
            f"refusing to run: {needs_judge} graded by a judge only — pass --judge",
            file=sys.stderr,
        )
        return 2
    if args.print_input:
        from opentelemetry import trace

        _prefill_capture()
        evidence = RunEvidence(run_id=RUN_ID, lock_fd=lock_fd, metadata={
            "mode": "rendering-probe", "behavior": "UNMEASURED", "corpora": names})
        with evidence.capture(trace.get_tracer_provider()):
            rc = _execute(args, names, only)
            evidence.exit_code = rc
            return rc
    from evals.grading_contract import contract_for
    args.bound_contract = contract_for(
        args, entrypoint="eval",
        judges=((args.judge,) if args.judge else ()) + (
            args.bound_cohort.judges if args.bound_cohort else ()),
        selection={"corpora": names, "only": sorted(only or ()),
                   "sample": args.sample, "judge_samples": args.judge_samples,
                   **({"journey_cohort": args.bound_cohort.sha256,
                       "measurement": args.bound_measurement.mode}
                      if args.bound_cohort else {})},
    )
    from opentelemetry import trace

    from nora.subject.sandbox import EVAL_HOST_LOCK
    _prefill_capture()
    subject_overrides: dict[str, Any] | None = None
    overrides_sha: str | None = None
    if args.subject_overrides is not None:
        try:
            from evals.subject_config import load_overrides

            subject_overrides, overrides_sha = load_overrides(args.subject_overrides)
        except (OSError, ValueError, TypeError) as exc:
            print(f"refusing to run: {exc}", file=sys.stderr)
            return 2
    # Bound dispatches, including model reads during composition.
    try:
        budget = run_budget_for(args.run_budget, input_bytes=args.run_input_bytes)
    except ValueError as exc:
        print(f"refusing to run: {exc}", file=sys.stderr)
        return 2
    export = evaluation_export_for(Path(args.deployment_config))
    try:
        evidence = RunEvidence(run_id=RUN_ID, lock_fd=lock_fd, metadata={
            "grading_contract": args.bound_contract.receipt(),
            **revision(_REPO_ROOT), "corpora": names, "cases": sorted(only or ()),
            "sample": args.sample, "judge": args.judge, "judge_samples": args.judge_samples,
            "deployment_config_sha256": fingerprint(Path(args.deployment_config)),
            "lock_path": str(EVAL_HOST_LOCK), "lock_acquired": True,
            "measurement": args.bound_measurement.mode,
            **({"subject_overrides_file": str(args.subject_overrides),
                "subject_overrides_sha256": overrides_sha,
                "subject_overrides": subject_overrides}
               if subject_overrides is not None else {}),
            **({"run_budget": budget.as_metadata()} if budget is not None else {}),
        })
    except OSError as exc:
        raise EvidenceError(f"cannot prepare private eval evidence ({type(exc).__name__})") from exc
    print(f"private eval evidence: {evidence.path}")
    from nora.domain.run_budget import active_run_budget
    token = active_run_budget.set(budget) if budget is not None else None
    try:
        with evidence.capture(trace.get_tracer_provider(), export=export):
            args.bound_contract.record()
            if args.bound_cohort is not None:
                args.bound_cohort.record(evidence)
            trace_id = trace.get_current_span().get_span_context().trace_id
            print(f"eval trace: {trace_id:032x}")
            rc = _execute(args, names, only, subject_overrides=subject_overrides)
            if budget is not None:
                evidence.record("run_budget_final", **budget.as_metadata())
                # A spent/invalid budget can never read as success — the
                # terminal check turns a last-call overrun into rc 1, never 0.
                if budget.spent is not None:
                    evidence.record("run_budget_spent", reason=budget.spent)
                    rc = 1
            evidence.exit_code = rc
            return rc
    finally:
        if token is not None:
            active_run_budget.reset(token)


def _execute(args: argparse.Namespace, names: list[str], only: frozenset[str] | None,
             *, subject_overrides: dict[str, Any] | None = None) -> int:
    from nora.subject.eval_evidence import current

    args.service_inputs = getattr(args, "service_inputs", None)
    if args.service_inputs and (evidence := current()):
        args.service_inputs.record(evidence)
    try:
        source_url = source_url_for(args.source_url, Path(args.deployment_config).parent)
    except ValueError as exc:
        print(f"refusing to run: {exc}", file=sys.stderr)
        return 2

    # FAIL-CLOSED, BEFORE ANY CASE: the source is read-only and this run will
    # never migrate it, so a branch ahead of (or behind) it would silently
    # misread it rather than write to it — `snapshot()` naming a column the
    # source predates fails deep inside a case with a message that names the
    # column, not the reason (`nora.subject.sandbox.
    # refuse_if_source_diverges_from_head`'s own docstring).
    try:
        refuse_if_source_diverges_from_head(source_url)
    except SourceRevisionMismatchError as exc:
        print(f"refusing to run: {exc}", file=sys.stderr)
        return 2

    # LEAKED PRIVATE BLANKS, before any case: `_blank_template`'s own copies
    # from a run this host `kill -9`'d (or one that raced this run to death
    # some other way) before its `finally` ever ran. Never the shared names
    # — see `sweep_stale_private_blank_templates`'s own age+connection guard.
    _sweep_stale_private_blanks(source_url)

    # THE GUARD. Recorded here, before any case, and
    # re-read in the `finally` below through `assert_shared_databases_unchanged`
    # — a run must never change the alembic version of `nora_eval` or
    # `nora_eval_blank`, the two databases every OTHER session on the host
    # also reads. This does not trust that `_blank_template`'s own guard held;
    # it measures the outcome.
    shared_before = shared_database_versions(source_url)

    # A SIGTERM (`kill <pid>`, no `-9`) must not skip the `finally` below —
    # cleanup and the guard are exactly what a killed run needs most, since
    # nothing else will ever run them for it. Turned into an ordinary
    # `SystemExit` (see `_raise_on_sigterm`), which the `try/finally`
    # structure below handles like any other exception; restored in THIS
    # function's own `finally` so a caller that imports `main()` (a test)
    # never leaves the process's signal table changed.
    previous_sigterm = signal.signal(signal.SIGTERM, _raise_on_sigterm)
    try:
        try:
            # THE SUBJECT'S CONFIG, REBUILT FOR THIS RUN, BEFORE ANY CASE.
            #
            # Every subject composes `config.evaluation.toml`, and this is
            # the only thing that writes it. Regenerated per run rather than
            # reused: a stale file is a run measuring a Nora the deployment
            # has since stopped being, and nothing downstream would say so.
            #
            # It is also the one gate that can compare the two —
            # `materialise()` REFUSES when the generated config disagrees
            # with the deployment outside `ALLOWED_TO_DIFFER`, so a run
            # never starts against a Nora nobody deployed. Fail-closed here,
            # or every number after it is unattributable.
            try:
                generated = materialise(
                    example=_REPO_ROOT / "config.example.toml",
                    deployment=Path(args.deployment_config),
                    target=_REPO_ROOT / "config.evaluation.toml",
                )
            except FileNotFoundError:
                print(
                    f"--deployment-config {args.deployment_config!r} does not exist. "
                    "An eval subject composes the DEPLOYMENT's config, never "
                    "config.example.toml — the example ships with almost everything "
                    "off, and running against it measures a different Nora "
                    "(docs/engineering/evaluation-subject.md). Run this where the "
                    "deployment lives, or point --deployment-config at its "
                    "config.toml.",
                    file=sys.stderr,
                )
                return 2
            except ValueError as exc:
                print(f"refusing to run: {exc}", file=sys.stderr)
                return 2
            from nora.subject.eval_evidence import current

            if evidence := current():
                evidence.record("subject_config", sha256=fingerprint(Path(generated)))
            print(f"subject config rebuilt from {args.deployment_config} -> {generated}")
            measurement = getattr(args, "bound_measurement", None) or PROTOCOL
            print(f"run {RUN_ID}: {measurement.phrase} with case-owned external services")
            # Match case selection to the generated subject or its explicit
            # memory-surface arm. Each case independently checks its subject.
            print(f"memory surface: {memory_surface_line(args.memory_surface)}")
            print(pseudonyms.status())

            if args.print_input:
                return _print_input(names[0], args.print_input, source_url=source_url,
                                    services=(args.service_inputs.corpora.get(names[0])
                                          if args.service_inputs else None),
                                    memory_surface=args.memory_surface)
            # THE BUDGET, before any draw (and before the graph surface's
            # checks): the size of what this run would draw, and a refusal over
            # `BUDGET_MINUTES` unless the operator asked for it.
            cohort = getattr(args, "bound_cohort", None)
            try:
                est = estimate([
                    (len(selected_cases(n, only=only, cohort=cohort)),
                     bool(args.judge) and n in JUDGED_CORPORA)
                    for n in names
                ], args.sample, args.judge_samples)
            except ValueError as exc:
                print(f"refusing to run: {exc}", file=sys.stderr)
                return 2
            print(est.line())
            if (why := over_budget(est, allow_long=args.allow_long)) is not None:
                print(f"refusing to run: {why}", file=sys.stderr)
                return 2
            if contract := getattr(args, "bound_contract", None):
                contract.verify_cases({f"{n}:{c.name}": c for n in names
                    for c in selected_cases(n, only=only, cohort=cohort)})

            judge = _eval_judge(args) if args.judge else None

            ok = True
            for name in names:
                with exposure(args.context_arm, args.context_report) as receipts:
                    ok = _run_one(
                        name, source_url=source_url, judge=judge, sample=args.sample,
                        only=only, all_reasons=args.reasons, positions=args.positions,
                        memory_surface=args.memory_surface,
                        subject_overrides=subject_overrides,
                        context_receipts=(receipts if args.context_arm != "full"
                                          or args.context_report else None),
                        contract=getattr(args, "bound_contract", None),
                        services=(args.service_inputs.corpora.get(name)
                                  if args.service_inputs else None),
                        cohort=getattr(args, "bound_cohort", None),
                        measurement=measurement) and ok
                # Successful Nora replies under fallback cannot pass the selector experiment.
                ok = ok and not any("selector_error" in r for r in receipts)
            return 0 if ok else 1
        finally:
            # Drop this run's own private blank template, if `_blank_template`
            # ever had to build one (branch-ahead/behind divergence) — then
            # ASSERT the two SHARED databases moved not at all. A `finally`,
            # not only the success path: a case that raised, OR a SIGTERM
            # turned into the `SystemExit` above, must not skip the check
            # that would have caught a mutation, and cleanup runs before the
            # assertion so a private copy never counts as "left behind"
            # against the shared names it was built to protect.
            asyncio.run(close_private_blank_templates())
            try:
                assert_shared_databases_unchanged(source_url, shared_before)
            except SharedDatabaseMutatedError as exc:
                # Loud on purpose: this must never read as merely a nonzero
                # exit among the run's own — it is a DIFFERENT session's next
                # run that pays for it. Re-raised, not swallowed into a
                # return code: ERROR is never a PASS (CLAUDE.md), and
                # (whether or not a case ALSO raised — Python chains the two
                # automatically, `__context__`) this is caught by nothing
                # upstream of `if __name__ == "__main__":`, so it is the
                # last word this process prints.
                print(f"ERROR: {exc}", file=sys.stderr)
                raise
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


def _eval_judge(args: argparse.Namespace) -> Judge:
    """The eval judge the corpus runs grade against (`rebuilt.grading.Judge`)."""
    return Judge(
        base_url=args.judge_base_url, model=args.judge, samples=args.judge_samples,
        config_path=args.config, timeout_seconds=args.judge_timeout,
        contract=getattr(args, "bound_contract", None))


def _raise_on_sigterm(signum: int, frame: object) -> None:
    """Turn a SIGTERM into an ordinary `SystemExit`, so `main()`'s
    `try/finally` still runs `close_private_blank_templates` and
    `assert_shared_databases_unchanged` — the ONLY way a `kill <pid>`'d run
    (as opposed to `kill -9`, which no userspace code can intercept) still
    drops its own private database and still checks the shared ones before
    it dies. `SystemExit`, not a plain exception: uncaught, it exits quietly
    with `.code` as the process's exit status (Python's own top-level
    handling for `SystemExit`), rather than printing a traceback that would
    bury the guard's own `ERROR:` line if one fires.

    143 = 128 + `SIGTERM`(15) — the shell's own convention for "died to a
    signal", so a caller checking the exit code sees the ordinary shape.
    """
    raise SystemExit(143)


if __name__ == "__main__":
    raise SystemExit(main())
