"""The eval command line's surface: every flag `python -m evals` takes."""

import argparse
import math
import os
from pathlib import Path

from evals.rebuilt.grading import DEFAULT_JUDGE_MODEL, DEFAULT_SAMPLES

BUDGET_MINUTES = 60


def judge_timeout(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise argparse.ArgumentTypeError("judge timeout must be finite and >0 and <=300 seconds")
    return seconds


def eval_parser() -> argparse.ArgumentParser:
    """Every flag `python -m evals` takes."""
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
             "it. Defaults to the clone on the deploy host, derived from its "
             "credentials (process env > .env > secret.toml). "
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
    ap.add_argument("--coverage-timeout", type=float, default=90.0,
                    help="coverage wall-clock seconds per batch; plan must fit 55 minutes")
    ap.add_argument("--judge-base-url", default="http://127.0.0.1:20128/v1")
    ap.add_argument("--grading-contract", type=Path)
    ap.add_argument("--grading-contract-sha256")
    ap.add_argument("--config", type=Path,
                    help="config.toml selecting the eval_judge provider and credentials")
    ap.add_argument("--judge-timeout", type=judge_timeout, default=300.0,
                    help="wall-clock seconds per non-coverage judge draw (>0 and <=300)")
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
    ap.add_argument(
        "--memory-index", action="store_true",
        help="compose every subject with [memory.index] read = true: the "
             "memory graph's tool surface (memory_search, no Honcho memory "
             "verb; DEC-316). The after arm of a memory routing run while the "
             "deployment runs the Honcho surface. A subject override, so the "
             "drift gate is unchanged. Needs the embedder's loopback publish "
             "and a source holding the model's vectors (make eval-source); "
             "each sandbox's index is built and gets the source's vectors",
    )
    ap.add_argument(
        "--allow-long", action="store_true",
        help=f"start a run estimated over {BUDGET_MINUTES} minutes. Only when the "
             "operator asked for a full run: the host's eval lock is shared",
    )
    ap.add_argument("--context-arm", choices=("full", "rules", "flash"), default="full",
                    help="experiment-only tag exposure filter; retrieval stays unchanged")
    ap.add_argument("--context-report", type=Path,
                    help="write selector receipts and exposure sizes, without room content")
    _run_args(ap)
    return ap


def _run_args(ap: argparse.ArgumentParser) -> None:
    """The frozen-coverage, subject-override and run-budget flags."""
    ap.add_argument("--coverage-export", type=Path, metavar="PRIVATE_OUTPUT",
                    help="no-call frozen input export using readonly stores; refuses overwriting")
    ap.add_argument("--coverage-inventory", type=Path)
    ap.add_argument("--coverage-semantic", type=Path)
    ap.add_argument("--coverage-recovery", type=Path)
    ap.add_argument("--coverage-identity", type=Path)
    ap.add_argument("--coverage-freeze-input", type=Path,
                    help="no-call pin of ORIGINAL export reconciled to original source files")
    ap.add_argument("--coverage-manifest", type=Path,
                    help="exclusive private manifest destination; never derived from input name")
    ap.add_argument(
        "--subject-overrides", type=Path, metavar="FILE",
        help="a JSON object of dotted `SubjectSpec.overrides` keys applied to "
             "every composed subject (e.g. `computer.scoped.enabled`, "
             "`computer.scoped.registry`). The file's content and sha256 are "
             "recorded in `run_started` before any case composes — a declared "
             "subject seam, not a hidden patch.")
    ap.add_argument(
        "--run-budget", metavar="REQUESTS[,IN[,OUT[,MINUTES]]]",
        help="a hard dispatch bound installed before the first case composes: "
             "max model requests, max input tokens, max output tokens, max "
             "active minutes (defaults 40/measured/40000/30). An empty or "
             "`none` IN field means input is measured from provider usage, "
             "not capped — a strict input-token bound has no honest "
             "pre-dispatch measure and refuses every call. `chat.completions` "
             "calls that would cross it raise RunBudgetError BEFORE leaving — "
             "budget exhaustion is ERROR, never PASS.")
