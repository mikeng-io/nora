"""`python -m evals.rebuilt CORPUS...` — run cases, print the measurements.

A suite is a set of cases, so this takes FILES and has no suite names in it.
Adding a suite is adding a corpus file; nothing here changes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .report import render
from .runner import load, run


async def _main(paths: list[Path], *, database_url: str, model: str | None) -> int:
    overrides: dict[str, object] = {}
    if model:
        overrides["llm.model"] = model
    verdicts = []
    for path in paths:
        for case in load(path):
            verdicts.append(await run(case, database_url=database_url,
                                      overrides=overrides or None))
            print(".", end="", flush=True, file=sys.stderr)
    print(file=sys.stderr)
    # `subject=` travels with the number into logs and pastes, where prose in a
    # document does not (#310). There is exactly ONE subject now, and naming it
    # is what keeps that true: the moment a second appears, this line is where
    # it shows up.
    passed, text = render(verdicts, subject="nora.subject.obtain")
    print(text)
    return 0 if passed else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.rebuilt")
    ap.add_argument("corpora", nargs="+", type=Path)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--model", default=None,
                    help="override [llm] model — must name a DEPLOYED alias")
    args = ap.parse_args()
    if not args.database_url:
        print("--database-url or DATABASE_URL is required", file=sys.stderr)
        return 2
    return asyncio.run(_main(args.corpora, database_url=args.database_url,
                             model=args.model))


if __name__ == "__main__":
    raise SystemExit(main())
