"""`python -m evals CORPUS...` — run selected corpora, print the measurements.

Runs whichever corpora you name, not "everything." A run over 150 cases is
hours (5 judged corpora at ~211s/sample x 3 samples each), and there is no
reason a one-line change should cost that every time — `--corpus` /
`--class` / `--all` are three ways to say how much you actually want.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from pathlib import Path
from typing import Any

from evals.backbone import (
    ERROR,
    FAIL,
    NORA_ERROR,
    NORA_POSITION,
    NORA_SHOWN,
    PASS,
    RUN_ID,
    UNSTABLE,
    Spec,
    base_name,
    drain_draws,
    drain_spend,
    nora_task,
    read,
    sample_read,
)
from evals.rebuilt.grading import DEFAULT_JUDGE_MODEL, DEFAULT_SAMPLES, Judge
from evals.scenarios import (
    CORPUS_ENTRY,
    JUDGE_REQUIRED,
    JUDGED_CORPORA,
    census,
    experiment_for,
    load_corpus,
)
from evals.subject_config import materialise
from nora.subject import (
    SharedDatabaseMutatedError,
    SourceRevisionMismatchError,
    assert_shared_databases_unchanged,
    close_private_blank_templates,
    refuse_if_source_diverges_from_head,
    shared_database_versions,
    sweep_stale_draw_workspaces,
)

#: Coarse S-class grouping for `--class`, per `10-the-scenario-map.md` §2/§15.
#: Approximate on purpose where a corpus is not wholly one class (e.g. only
#: `proactive_decide`'s two-room pair is really "S1"; the rest of it is
#: closer to a general reminder-write correctness check) — good enough to
#: pick "which corpora" without re-deriving the map's own nuance here.
#: S2/S4/S5 have no corpus at all (hand-written `Case`s only, not part of
#: `evals/corpora/`), so they are not selectable through this script.
CORPUS_CLASS: dict[str, str] = {
    "proactive_decide": "S1",
    "proactive_act": "S1",
    "being_contradicted": "S3",
    "identity_grounding": "S3",
    "source_scope": "S3",
    # ADR 0015 §4: what she SAYS after a tool returns (judged).
    "tool_reporting": "S3",
    # S2, tool routing: which tool EXECUTED (design/tool-catalogue §2c).
    "tool_selection": "S2",
    "note_candidates": "S6",
    "no_trigger": "S7",
    "near_miss": "S7",
    "trigger": "S7",
    "relevance_gate": "S7",
    "participation": "S7",
    "injections": "S9",
    "transcript_injection": "S9",
    "presence": "S3",
    "appearance": "S3",
    # What a WRITE keeps: `carry` is #228's shape (an unrelated pass lost a
    # thread it did not touch), the nearest class the map has. Its `stale`
    # cases are S5-shaped (time misread) — approximate, as this map says.
    "standing": "S6",
}

#: The entry points that honour `metadata.print_input` — render the model
#: call's input, record it as `shown`, and call no model.
_PRINTS_INPUT = frozenset({"standing"})


def _list_corpora() -> None:
    for name in sorted(CORPUS_ENTRY):
        judged = "judged" if name in JUDGED_CORPORA else "structural"
        cls = CORPUS_CLASS.get(name, "?")
        print(f"{name:24} entry={CORPUS_ENTRY[name]:14} {cls:3} {judged}")


_REPO_ROOT = Path(__file__).resolve().parents[1]

#: The clone's name on the deploy host, and the loopback publish its compose
#: file gives the database container (`ports: ["127.0.0.1:5435:5432"]`).
EVAL_CLONE = "nora_eval"
_DEPLOY_DB = "127.0.0.1:5435"


def source_url_for(stated: str, deployment_dir: Path) -> str:
    """The source URL, or a refusal.

    Stated wins; otherwise it is derived from the deployment's own `.env`
    (`POSTGRES_PASSWORD`) and the clone's name — so on the deploy host the
    default is right and nowhere else is there one. Either way the database
    NAME must be a clone's: the live database is `nora`, and one flag away
    from running 150 cases' seeds and turns into production is not a
    default anybody should be able to reach.
    """
    from sqlalchemy.engine import make_url

    if not stated:
        env = deployment_dir / ".env"
        password = ""
        try:
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("POSTGRES_PASSWORD="):
                    password = line.split("=", 1)[1].strip()
        except OSError as exc:
            raise ValueError(
                f"--source-url not given and {env} is unreadable ({exc}); "
                "the default source is the deploy host's clone, derived from "
                "its own .env"
            ) from exc
        if not password:
            raise ValueError(f"--source-url not given and {env} states no POSTGRES_PASSWORD")
        stated = f"postgresql+asyncpg://nora:{password}@{_DEPLOY_DB}/{EVAL_CLONE}"
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
    unknown = selected - set(CORPUS_ENTRY)
    if unknown:
        print(f"unknown corpus/class selection: {sorted(unknown)}", file=sys.stderr)
        raise SystemExit(2)
    return sorted(selected)


def _print_spend() -> None:
    """What this corpus actually spent, and whether any call was TRUNCATED.

    Printed on every run because it is free — the numbers ride on spans this
    process already captures for `agent.prefill` — and because the
    alternative is what happened on 2026-09-22: a truncation was diagnosed by
    changing a ceiling and re-running, twice, while the call's own
    `finish_reason` sat in memory unread.
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
    # This is the per-turn unit `design/tool-catalogue/05` §3 gates on.
    turn = spend.get("turn") or {}
    if turn.get("turns"):
        n = turn["turns"]
        print(
            f"  turn:  {turn['calls']} call(s) over {n} turn(s) "
            f"({turn['calls'] / n:.2f}/turn), in/turn={turn['input'] // n} "
            f"(fresh {turn['fresh'] // n}, cached {turn['cached'] // n}), "
            f"out/turn={turn['output'] // n}"
        )


def _print_memory_overhead() -> None:
    """What giving every draw its own Honcho workspace cost (issue #462):
    the create and the delete, per draw, and any workspace left behind.

    Printed on every run for `_print_spend`'s reason: it is free, and the
    question it answers ("what does isolation cost, and did it clean up")
    otherwise costs a second run.
    """
    draws = drain_draws()
    if not draws:
        return
    create = sorted(d.create_ms for d in draws)
    delete = sorted(d.delete_ms for d in draws if d.delete_ms is not None)
    left = [d.id for d in draws if not d.deleted]
    line = (
        f"  honcho: {len(draws)} draw workspace(s), create median "
        f"{statistics.median(create):.0f}ms (max {create[-1]:.0f}ms)"
    )
    if delete:
        line += f", delete median {statistics.median(delete):.0f}ms (max {delete[-1]:.0f}ms)"
    if left:
        line += f"   NOT DELETED: {left}"
    print(line)


def _sweep_stale_workspaces() -> None:
    """Delete draw workspaces a killed run left behind, before this run
    starts. Never fatal: a sweep that cannot reach Honcho says so, and every
    draw's own create will then fail and read ERROR."""
    try:
        swept = asyncio.run(sweep_stale_draw_workspaces())
    except Exception as exc:  # noqa: BLE001 — reported, never a reading
        print(f"honcho: stale draw-workspace sweep failed: {type(exc).__name__}: {exc}")
        return
    if swept:
        print(f"honcho: swept {len(swept)} stale draw workspace(s) left by a killed run")


def _case_state(case_row: dict[str, Any], name: str) -> Any:
    """One named value off a report row's `actual_environment_state`. A row
    IS the `EvaluationData` the grader saw (`model_dump()`), so the state the
    task recorded rides on it."""
    for entry in case_row.get("actual_environment_state") or ():
        if entry.get("name") == name:
            return entry.get("state")
    return None


def _print_positions(report: Any) -> None:
    """Every draw's WRITTEN position, with its length (issue #445).

    The judge's reason says a finding was left out and cannot say WHY: a fold
    that never tried and a fold that was squeezed out by `[standing]
    max_chars` read the same in a reason line. The length against the cap is
    the first thing that tells them apart, and the text is the second.
    Printed on the terminal only — never paste it into a PR or an issue; it
    is a room's own standing.
    """
    for case_row, outputs in zip(report.cases, report.detailed_results, strict=True):
        position = _case_state(case_row, NORA_POSITION)
        label = "/".join(str(getattr(out, "label", "?")) for out in outputs)
        size = "none" if position is None else f"{len(position)} chars"
        print(f"  [{label}] {case_row['name']}  ({size})")
        if position:
            for line in str(position).splitlines():
                print(f"      | {line}")


def _run_one(
    name: str, *, source_url: str, judge: Judge | None, sample: int,
    only: frozenset[str] | None = None, all_reasons: bool = False,
    positions: bool = False,
) -> bool:
    experiment, task = experiment_for(
        name, source_url=source_url, judge=judge, sample=sample, only=only,
    )
    report = experiment.run_evaluations(task=task)
    scope = f" [{len(experiment.cases)} selected]" if only else ""
    print(f"--- {name}: {census(name)}{scope} ---")
    _print_spend()
    _print_memory_overhead()
    if positions:
        _print_positions(report)
    if sample > 1:
        readings = sample_read(report)
        # THE SPLIT, not just the verdict. `UNSTABLE` says the draws
        # disagreed and nothing more; 2 PASS / 1 FAIL and 1 PASS / 2 ERROR
        # are different facts about the same word, and the second is a case
        # that mostly did not RUN. Printed for every non-PASS verdict, so
        # the next question ("how unstable, and was any draw an error?") does
        # not cost another run of the corpus.
        reasons: dict[str, list[str]] = {}
        for case_row, outputs in zip(report.cases, report.detailed_results, strict=True):
            for out in outputs:
                if all_reasons or getattr(out, "label", None) != PASS:
                    reasons.setdefault(base_name(case_row["name"]), []).append(
                        f"{out.label}: {out.reason}"
                    )
        ok = True
        for case_name, r in sorted(readings.items()):
            split = (
                "" if r.verdict == PASS
                else f"  [{r.passed} pass / {r.failed} fail / {r.errored} error]"
            )
            print(f"  {case_name:40} {r.verdict}{split}")
            # One line per DISTINCT reason: three draws failing the same way
            # is one fact, and printing it three times hides that it is.
            for reason in dict.fromkeys(reasons.get(case_name, ())):
                print(f"      {reason}")
            ok = ok and r.verdict not in (FAIL, ERROR, UNSTABLE)
        return ok
    reading = read(report)
    print(f"  passed={reading.passed} failed={reading.failed} errored={reading.errored}")
    if reading.failed or reading.errored:
        for case_row, outputs in zip(report.cases, report.detailed_results, strict=True):
            for out in outputs:
                if getattr(out, "label", None) != PASS:
                    print(f"  {case_row['name']:40} {out.label:6} {out.reason}")
    return reading.failed == 0 and reading.errored == 0


def _print_input(name: str, case_name: str, *, source_url: str) -> int:
    """Render one case's model input and print it; no model is called.

    `design/standing-compact/20-verification.md` §1: «before any model call,
    each arm's rendered input for one case is printed and diffed. A suite that
    cannot see the change measures noise.» Run it once on each arm's checkout
    and `diff` the two outputs. The master arm is master with the harness
    commit applied — the corpus, `evals/` and `nora/subject/standing_fold.py`
    apply to it unchanged — and the branch arm is the branch; the corpus
    header (`evals/corpora/standing.yaml`) states the whole recipe.

    The case runs the ONE path — sandbox, snapshot, frozen clock — with the
    fold's model client swapped for a recorder, so what prints is exactly what
    the deployed fold would have sent. It is always an ERROR as a verdict; the
    exit code says only whether the input was rendered.
    """
    if CORPUS_ENTRY[name] not in _PRINTS_INPUT:
        print(
            f"--print-input is honoured by {sorted(_PRINTS_INPUT)} entry points; "
            f"{name!r} enters at {CORPUS_ENTRY[name]!r}",
            file=sys.stderr,
        )
        return 2
    matches = [c for c in load_corpus(name) if c.name == case_name]
    if not matches:
        print(f"{name}: no active case named {case_name!r}", file=sys.stderr)
        return 2
    [case] = matches
    case = case.model_copy(update={"metadata": {**(case.metadata or {}), "print_input": True}})
    result = nora_task(Spec(source_url=source_url, entry=CORPUS_ENTRY[name]))(case)
    state = {e.name: e.state for e in result.get("environment_state") or ()}
    shown = state.get(NORA_SHOWN) or ""
    if not shown:
        print(f"nothing was rendered: {state.get(NORA_ERROR)}", file=sys.stderr)
        return 1
    print(shown)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m evals")
    ap.add_argument("corpus", nargs="*", help="corpus name(s) — see --list")
    ap.add_argument("--class", dest="cls", action="append",
                     help="run every corpus in this S-class (e.g. S7); repeatable")
    ap.add_argument("--all", action="store_true", help="run every corpus")
    ap.add_argument("--list", action="store_true", help="list corpora and exit")
    ap.add_argument(
        "--case", action="append", dest="cases", metavar="NAME",
        help="run only these cases of the named corpus; repeatable. A "
             "re-measurement asks about the cases that moved, and paying for "
             "a whole corpus to re-draw four of them is how a sample count "
             "gets chosen by budget instead of by the question.",
    )
    ap.add_argument(
        "--print-input", metavar="CASE",
        help="render CASE's model input (the standing fold's system and user "
             "prompt) and print it, calling no model — run on each arm and "
             "diff, before any suite is paid for. One corpus only. The MASTER "
             "arm is master with the harness commit applied (it applies "
             "unchanged: evals/, nora/subject/standing_fold.py, the corpus); "
             "the branch arm is the branch. See evals/corpora/standing.yaml.",
    )
    ap.add_argument(
        "--source-url", default=os.environ.get("NORA_EVAL_SOURCE_URL", ""),
        help="the deployment's CLONE (nora_eval), read and never written; "
             "every case composes against a sandbox database cloned beside "
             "it. Defaults to the clone on the deploy host, from its .env. "
             "A name that is not a clone's is refused.",
    )
    ap.add_argument(
        "--deployment-config",
        default=os.environ.get("NORA_CONFIG")
        or str(Path.home() / "workspace" / "nora" / "config.toml"),
        help="the DEPLOYMENT's config.toml. Every subject is composed from it "
             "(rebuilt into config.evaluation.toml per run), because the "
             "example ships with almost everything off and running against it "
             "measures a different Nora.",
    )
    ap.add_argument(
        "--judge", nargs="?", const=DEFAULT_JUDGE_MODEL, default=None,
        help=(
            "grade JUDGED_CORPORA (S3/S9) with a model, not just structurally. "
            "Optional value overrides the judge model"
        ),
    )
    ap.add_argument("--judge-base-url", default="http://127.0.0.1:20128/v1")
    ap.add_argument(
        "--judge-samples", type=int, default=DEFAULT_SAMPLES,
        help=(
            f"judge draws per case (default {DEFAULT_SAMPLES}). MEASURED at "
            "~211s a draw — lower it deliberately for a cheap run and say so"
        ),
    )
    ap.add_argument(
        "--positions", action="store_true",
        help="print every draw's written standing position and its length "
             "(the standing corpus; #445). Terminal only: it is a room's own "
             "position, never paste it anywhere public",
    )
    ap.add_argument(
        "--reasons", action="store_true",
        help="print every case's grader reason, PASS included — for a "
             "routing corpus that is the list of tools each draw executed",
    )
    ap.add_argument(
        "--sample", type=int, default=1,
        help=(
            "draw each case this many times (evals.backbone.sampled/"
            "sample_read) rather than once. One draw of a stochastic "
            "process is not a reading of it; the default of 1 is a cheap "
            "smoke run and says so"
        ),
    )
    args = ap.parse_args()

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
    if args.print_input and len(names) != 1:
        print("--print-input names a case OF ONE corpus", file=sys.stderr)
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

    # THE GUARD (2026-09-25 hazard). Recorded here, before any case, and
    # re-read in the `finally` below through `assert_shared_databases_unchanged`
    # — a run must never change the alembic version of `nora_eval` or
    # `nora_eval_blank`, the two databases every OTHER session on the host
    # also reads. This does not trust that `_blank_template`'s own guard held;
    # it measures the outcome.
    shared_before = shared_database_versions(source_url)

    try:
        # THE SUBJECT'S CONFIG, REBUILT FOR THIS RUN, BEFORE ANY CASE.
        #
        # Every subject composes `config.evaluation.toml`, and this is the
        # only thing that writes it. Regenerated per run rather than reused:
        # a stale file is a run measuring a Nora the deployment has since
        # stopped being, and nothing downstream would say so.
        #
        # It is also the one gate that can compare the two — `materialise()`
        # REFUSES when the generated config disagrees with the deployment
        # outside `ALLOWED_TO_DIFFER`, so a run never starts against a Nora
        # nobody deployed. Fail-closed here, or every number after it is
        # unattributable.
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
                "(design/evaluation/01-boundaries.md §5). Run this where the "
                "deployment lives, or point --deployment-config at its "
                "config.toml.",
                file=sys.stderr,
            )
            return 2
        except ValueError as exc:
            print(f"refusing to run: {exc}", file=sys.stderr)
            return 2
        print(f"subject config rebuilt from {args.deployment_config} -> {generated}")
        # AFTER the rebuild: the sweep reads Honcho's address and the
        # deployment's workspace off the config every subject composes.
        print(f"run {RUN_ID}: every draw gets its own Honcho workspace")
        _sweep_stale_workspaces()

        if args.print_input:
            return _print_input(names[0], args.print_input, source_url=source_url)

        judge = None if not args.judge else Judge(
            base_url=args.judge_base_url, model=args.judge, samples=args.judge_samples,
        )

        ok = True
        for name in names:
            ok = _run_one(
                name, source_url=source_url, judge=judge, sample=args.sample,
                only=only, all_reasons=args.reasons, positions=args.positions,
            ) and ok
        return 0 if ok else 1
    finally:
        # Drop this run's own private blank template, if `_blank_template`
        # ever had to build one (branch-ahead/behind divergence) — then
        # ASSERT the two SHARED databases moved not at all. A `finally`, not
        # only the success path: a case that raised or a killed run must not
        # skip the check that would have caught a mutation, and cleanup runs
        # before the assertion so a private copy never counts as "left
        # behind" against the shared names it was built to protect.
        asyncio.run(close_private_blank_templates())
        try:
            assert_shared_databases_unchanged(source_url, shared_before)
        except SharedDatabaseMutatedError as exc:
            # Loud on purpose: this must never read as merely a nonzero exit
            # among the run's own — it is a DIFFERENT session's next run that
            # pays for it. Re-raised, not swallowed into a return code: ERROR
            # is never a PASS (CLAUDE.md), and this is caught by nothing
            # upstream of `if __name__ == "__main__":`, so it is the last
            # word this process prints.
            print(f"ERROR: {exc}", file=sys.stderr)
            raise


if __name__ == "__main__":
    raise SystemExit(main())
