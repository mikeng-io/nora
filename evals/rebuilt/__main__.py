"""`python -m evals.rebuilt CORPUS...` — run cases, print the measurements.

A suite is a set of cases, so this takes FILES and has no suite names in it.
Adding a suite is adding a corpus file; nothing here changes.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import sys
from pathlib import Path

from .grading import DEFAULT_JUDGE_MODEL, Judge
from .report import render
from .runner import load, run


async def _main(
    paths: list[Path], *, database_url: str, model: str | None,
    judge: Judge | None, sample: int | None,
) -> int:
    overrides: dict[str, object] = {}
    if model:
        overrides["llm.model"] = model
    verdicts = []
    for path in paths:
        for case in load(path):
            if sample is not None:
                case = dataclasses.replace(case, sample=sample)
            verdicts.append(await run(case, database_url=database_url,
                                      overrides=overrides or None, judge=judge))
            print(".", end="", flush=True, file=sys.stderr)
    print(file=sys.stderr)
    # `subject=` travels with the number into logs and pastes, where prose in a
    # document does not (#310). There is exactly ONE subject now, and naming it
    # is what keeps that true: the moment a second appears, this line is where
    # it shows up.
    passed, text = render(
        verdicts, subject="nora.subject.obtain",
        judge=None if judge is None else judge.model,
        sample=sample,
    )
    print(text)
    return 0 if passed else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.rebuilt")
    ap.add_argument("corpora", nargs="+", type=Path)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--model", default=None,
                    help="override [llm] model — must name a DEPLOYED alias")
    ap.add_argument(
        "--judge", nargs="?", const=DEFAULT_JUDGE_MODEL, default=None,
        help=(
            "grade what she SAID, not just that she said something. Without "
            "this the adversarial corpora are close to vacuous: every case "
            "expects `reply`, so an injection passes on the string the attack "
            "wanted. Optional value overrides the judge model; it must differ "
            "from --model, because grading with the tier under test is circular"
        ),
    )
    ap.add_argument("--judge-base-url", default="http://127.0.0.1:20128/v1")
    ap.add_argument(
        "--sample", type=int, default=None,
        help=(
            "override every case's `sample:`. The corpora ask for five and are "
            "right to — one draw of a stochastic process is not a reading of "
            "it. This exists so a cheap smoke run is POSSIBLE AND SAYS SO: "
            "without it a run at one draw could not be reproduced from the "
            "tree, because the tree says five"
        ),
    )
    args = ap.parse_args()
    if not args.database_url:
        print("--database-url or DATABASE_URL is required", file=sys.stderr)
        return 2
    # REFUSED, not warned: grading a model with itself is circular, and a run
    # that did it would report a number nobody could read back.
    if args.judge and args.model and args.judge == args.model:
        print(f"--judge and --model are both {args.model!r}; grading with the "
              "tier under test is circular", file=sys.stderr)
        return 2
    judge = None if not args.judge else Judge(
        base_url=args.judge_base_url, model=args.judge,
    )
    return asyncio.run(_main(args.corpora, database_url=args.database_url,
                             model=args.model, judge=judge, sample=args.sample))


if __name__ == "__main__":
    raise SystemExit(main())
