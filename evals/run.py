"""Eval gate entry point: `python -m evals.run --suite {trigger,no_trigger,all}`.

Runs every case in the named suite through `evals.harness.run_case`, grades the
resulting `Trajectory` with the deterministic hard-gate evaluators appropriate to the
case (spec E.4), prints a pass/fail summary, and exits 0 only if everything passed.

Scope note: this task (Task 40) wires the offline/scripted code path only — see
`evals.harness`'s module docstring. Without a real model plugged in, every case here
runs with an empty script (`run_case(case, script=None)`), so no-trigger cases pass
trivially (nothing was ever sent) while trigger cases legitimately fail (nothing was
ever sent, either — there's no model to produce a reply). That's an honest reflection
of "no live model is wired up yet," not a bug: the M7 stage's live/network run of this
same command is a separate, later, out-of-scope-for-this-task piece of work.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from evals.evaluators import (ExactlyOnceSendReply, HonchoFedOncePerReply, NoRedundantRead,
                              NoSendWithoutTrigger, ToolBudget)
from evals.harness import Case, load_cases, run_case

CASES_DIR = Path(__file__).resolve().parent / "cases"

TRIGGER_EVALUATORS = (ExactlyOnceSendReply(), ToolBudget(), NoRedundantRead(),
                      HonchoFedOncePerReply())
NO_TRIGGER_EVALUATORS = (NoSendWithoutTrigger(),)

SUITES = {
    "trigger": (CASES_DIR / "trigger.yaml",),
    "no_trigger": (CASES_DIR / "no_trigger.yaml",),
    "all": (CASES_DIR / "trigger.yaml", CASES_DIR / "no_trigger.yaml"),
}


def _evaluators_for(case: Case):
    return TRIGGER_EVALUATORS if case.trigger else NO_TRIGGER_EVALUATORS


async def _run_suite(paths: tuple[Path, ...]) -> bool:
    all_passed = True
    for path in paths:
        for case in load_cases(path):
            trajectory = await run_case(case)
            for evaluator in _evaluators_for(case):
                result = evaluator.evaluate(trajectory)
                status = "PASS" if result.passed else "FAIL"
                print(f"[{status}] {path.stem}/{case.name} — {type(evaluator).__name__}"
                      + (f": {result.reason}" if result.reason else ""))
                all_passed = all_passed and result.passed
    return all_passed


def main(suite: str) -> int:
    paths = SUITES.get(suite)
    if paths is None:
        print(f"unknown suite {suite!r}; expected one of {sorted(SUITES)}", file=sys.stderr)
        return 1
    ok = asyncio.run(_run_suite(paths))
    print("GATE PASSED" if ok else "GATE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Nora deterministic eval gate.")
    parser.add_argument("--suite", choices=sorted(SUITES), default="all")
    args = parser.parse_args()
    sys.exit(main(args.suite))
