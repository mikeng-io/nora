"""`python -m evals CORPUS...` — run selected corpora, print the measurements.

`evals.rebuilt.__main__`'s new-spine sibling, one flag set narrower: this
runs whichever corpora you name, not "everything." A run over 150 cases is
hours (5 judged corpora at ~211s/sample x 3 samples each), and there is no
reason a one-line change should cost that every time — `--corpus` /
`--class` / `--all` are three ways to say how much you actually want.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from evals.backbone import ERROR, FAIL, PASS, UNSTABLE, read, sample_read
from evals.rebuilt.grading import DEFAULT_JUDGE_MODEL, DEFAULT_SAMPLES, Judge
from evals.scenarios import CORPUS_ENTRY, JUDGED_CORPORA, experiment_for
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


def _run_one(name: str, *, database_url: str, judge: Judge | None, sample: int) -> bool:
    experiment, task = experiment_for(name, database_url=database_url, judge=judge, sample=sample)
    report = experiment.run_evaluations(task=task)
    print(f"--- {name} ---")
    if sample > 1:
        readings = sample_read(report)
        ok = True
        for case_name, sample_reading in sorted(readings.items()):
            print(f"  {case_name:40} {sample_reading.verdict}")
            ok = ok and sample_reading.verdict not in (FAIL, ERROR, UNSTABLE)
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
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
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
    if not names:
        print("no corpus selected — pass names, --class, --all, or --list", file=sys.stderr)
        return 2
    if not args.database_url:
        print("--database-url or DATABASE_URL is required", file=sys.stderr)
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
            name, database_url=args.database_url, judge=judge, sample=args.sample,
        ) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
