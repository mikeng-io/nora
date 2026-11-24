"""The eval command line's surface: every flag `python -m evals` takes."""

import argparse
import math
import os
import time
from pathlib import Path
from typing import Any

from evals.measurement import MEASURES
from evals.rebuilt.grading import DEFAULT_JUDGE_MODEL, DEFAULT_SAMPLES

BUDGET_MINUTES = 60


def run_input_bytes(value: str) -> int:
    """`--run-input-bytes`: a positive count of serialized wire bytes."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "input byte bound must be an integer") from None
    if n <= 0:
        raise argparse.ArgumentTypeError(
            "input byte bound must be positive")
    return n


def run_budget_for(value: str | None,
                   input_bytes: int | None = None) -> Any | None:
    """The parsed `--run-budget` bound, or None when the flag was not given.

    `input_bytes` is the parsed `--run-input-bytes` cap on cumulative
    serialized request bytes; it binds `RunBudget.max_input_bytes` and is
    refused on its own — a byte bound with no installed budget would
    silently gate nothing.
    """
    if value is None:
        if input_bytes is not None:
            raise ValueError("--run-input-bytes requires --run-budget")
        return None
    from nora.domain.run_budget import RunBudget

    if input_bytes is not None and input_bytes <= 0:
        raise ValueError("--run-input-bytes must be a positive integer")
    parts = value.split(",")
    if not 1 <= len(parts) <= 4:
        raise ValueError("--run-budget takes 1-4 fields")
    defaults: list[int | None] = [40, None, 40000, 30]
    vals: list[int | None] = []
    bad = False
    for i, p in enumerate(parts):
        p = p.strip()
        if not p or p.lower() in ("none", "-"):
            vals.append(defaults[i])
            continue
        try:
            vals.append(int(p))
        except ValueError:
            bad = True
    if bad or any(v is not None and v <= 0 for v in vals):
        raise ValueError(
            "--run-budget fields must be positive integers "
            "(empty or `none` IN = measured, not capped)")
    vals += defaults[len(vals):]
    return RunBudget(
        max_requests=vals[0], max_input_tokens=vals[1],
        max_output_tokens=vals[2],
        max_input_bytes=input_bytes,
        deadline=time.monotonic() + vals[3] * 60,
        output_reserve=4000)


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
    ap.add_argument("--services", type=Path,
                    help="private per-corpus/draw HTTP recordings for protocol rehearsal")
    ap.add_argument(
        "--journey-cohort", type=Path, metavar="FILE",
        help="the declared private journey cohort: a versioned file of "
             "version-2 live-clock cases and the two judge aliases, "
             "required by the autonomous_journey corpus (selected by name; "
             "never part of --all or --class). Its plans are finite "
             "generated worlds declared per case in --services, and the "
             "run requires an explicit --run-budget. Generated-world input "
             "is protocol evidence only — behavior stays UNMEASURED")
    ap.add_argument("--measure", choices=MEASURES, default="protocol",
                    help="the bound claim: `protocol` rehearsal (behavior "
                         "UNMEASURED, including declared gateway fixtures) or "
                         "`behavioral` — journeys with a declared gateway")
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
    ap.add_argument("--judge-base-url", default="http://127.0.0.1:20128/v1")
    ap.add_argument("--grading-contract", type=Path)
    ap.add_argument("--grading-contract-sha256")
    ap.add_argument("--config", type=Path,
                    help="config.toml selecting the eval_judge provider and credentials")
    ap.add_argument("--judge-timeout", type=judge_timeout, default=300.0,
                    help="wall-clock seconds per judge draw (>0 and <=300)")
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
            "draw each case this many times (evals.reading.sampled/"
            "sample_read) rather than once. One draw of a stochastic "
            "process is not a reading of it; the default of 1 is a cheap "
            "smoke run and says so"
        ),
    )
    ap.add_argument(
        "--memory-index", action="store_true",
        help="enable [memory.index] read in the eval subject and select the "
             "index-enabled routing cases. Requires the loopback embedder "
             "and a source with its model's vectors (make eval-source).",
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
    """The subject-override and run-budget flags."""
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
    ap.add_argument(
        "--run-input-bytes", type=run_input_bytes, metavar="BYTES",
        help="cumulative bound on serialized model-request bytes for the "
             "run, measured on the FINAL wire bodies at transport admission "
             "(RunBudget.max_input_bytes) — a size cap in bytes, never a "
             "provider-token or spend estimate. Requires --run-budget: the "
             "flag alone installs nothing and refuses rather than being "
             "ignored.")
