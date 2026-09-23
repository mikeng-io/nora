"""`python -m evals CORPUS...` — run selected corpora, print the measurements.

Runs whichever corpora you name, not "everything." A run over 150 cases is
hours (5 judged corpora at ~211s/sample x 3 samples each), and there is no
reason a one-line change should cost that every time — `--corpus` /
`--class` / `--all` are three ways to say how much you actually want.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from evals.backbone import (
    ERROR,
    FAIL,
    PASS,
    UNSTABLE,
    base_name,
    drain_spend,
    read,
    sample_read,
)
from evals.rebuilt.grading import DEFAULT_JUDGE_MODEL, DEFAULT_SAMPLES, Judge
from evals.scenarios import CORPUS_ENTRY, JUDGED_CORPORA, census, experiment_for
from evals.subject_config import materialise

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
    "note_candidates": "S6",
    "no_trigger": "S7",
    "near_miss": "S7",
    "trigger": "S7",
    "relevance_gate": "S7",
    "participation": "S7",
    "injections": "S9",
    "transcript_injection": "S9",
}


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


def _run_one(
    name: str, *, source_url: str, judge: Judge | None, sample: int,
    only: frozenset[str] | None = None, all_reasons: bool = False,
) -> bool:
    experiment, task = experiment_for(
        name, source_url=source_url, judge=judge, sample=sample, only=only,
    )
    report = experiment.run_evaluations(task=task)
    scope = f" [{len(experiment.cases)} selected]" if only else ""
    print(f"--- {name}: {census(name)}{scope} ---")
    _print_spend()
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
        "--source-url", default=os.environ.get("NORA_EVAL_SOURCE_URL", ""),
        help="the deployment's CLONE (nora_eval), read and never written; "
             "every case composes against a sandbox database cloned beside "
             "it. Defaults to the clone on the deploy host, from its .env. "
             "A name that is not a clone's is refused.",
    )
    ap.add_argument(
        "--deployment-config",
        default=os.environ.get("NORA_CONFIG", "/home/norty/workspace/nora/config.toml"),
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
    try:
        source_url = source_url_for(args.source_url, Path(args.deployment_config).parent)
    except ValueError as exc:
        print(f"refusing to run: {exc}", file=sys.stderr)
        return 2

    # THE SUBJECT'S CONFIG, REBUILT FOR THIS RUN, BEFORE ANY CASE.
    #
    # Every subject composes `config.evaluation.toml`, and this is the only
    # thing that writes it. Regenerated per run rather than reused: a stale
    # file is a run measuring a Nora the deployment has since stopped being,
    # and nothing downstream would say so.
    #
    # It is also the one gate that can compare the two — `materialise()`
    # REFUSES when the generated config disagrees with the deployment outside
    # `ALLOWED_TO_DIFFER`, so a run never starts against a Nora nobody
    # deployed. Fail-closed here, or every number after it is unattributable.
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

    judge = None if not args.judge else Judge(
        base_url=args.judge_base_url, model=args.judge, samples=args.judge_samples,
    )

    ok = True
    for name in names:
        ok = _run_one(
            name, source_url=source_url, judge=judge, sample=args.sample,
            only=only, all_reasons=args.reasons,
        ) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
