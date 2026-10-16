"""Run the doctrine check over its labelled set
against a live model, and print a confusion table.

    ssh -f -N -L 20128:127.0.0.1:20128 norty
    export OMNIROUTE_API_KEY=...   # from norty's .env, never pasted
    uv run python scripts/doctrine_check_labelled.py --model nora --samples 3

Each case is judged `--samples` times; a case passes only if every sample agrees
with its label. An `error` counts against the case (an instrument error is never
a pass). Exit 0 when every case passes, 1 otherwise, 2 on a setup error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openai import AsyncOpenAI  # noqa: E402

from nora.app.composition import _RelevanceGateModelClient  # noqa: E402
from nora.doctrine.loader import DoctrineStore  # noqa: E402
from nora.language.doctrine_check import DoctrineCheck  # noqa: E402
from nora.obs.metrics import Metrics  # noqa: E402

CASES = ROOT / "tests" / "fixtures" / "doctrine_check_labelled.json"
OBJECTIVE = ROOT / "nora" / "dreamer" / "objectives" / "doctrine-check.md"


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("---", 2)[2].strip() if text.startswith("---") else text


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="nora")
    ap.add_argument("--base-url", default="http://127.0.0.1:20128/v1")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--extra", type=Path, help="a file of more lessons, one per line, "
                    "expected consistent (e.g. live ones; never commit it)")
    args = ap.parse_args()
    key = os.environ.get("OMNIROUTE_API_KEY", "")
    if not key:
        print("OMNIROUTE_API_KEY is not set", file=sys.stderr)
        return 2
    store = DoctrineStore(ROOT / "nora" / "doctrine", metrics=Metrics())
    check = DoctrineCheck(
        # The production client (`nora.app.composition`, `[lessons]` wiring).
        client=_RelevanceGateModelClient(
            client=AsyncOpenAI(base_url=args.base_url, api_key=key), model=args.model,
            max_tokens=2048, temperature=0.0, timeout_seconds=61.0,
            reasoning_effort=args.effort,  # type: ignore[arg-type]
        ),
        law=store.law_rules, instruction=lambda: _body(OBJECTIVE), timeout_seconds=60.0,
    )
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if args.extra is not None:
        cases += [{"id": f"x{i:02d}", "expect": "consistent", "lesson": line}
                  for i, line in enumerate(args.extra.read_text(encoding="utf-8")
                                           .strip().splitlines(), 1)]
    print(f"model={args.model} effort={args.effort} samples={args.samples} "
          f"rules={len(check.rules())} cases={len(cases)}")
    failed = 0
    table = {(e, g): 0 for e in ("conflict", "consistent")
             for g in ("conflict", "consistent", "error")}
    for case in cases:
        got = [await check.judge(case["lesson"]) for _ in range(args.samples)]
        for v in got:
            table[(case["expect"], v.outcome)] += 1
        ok = all(v.outcome == case["expect"] for v in got)
        failed += not ok
        rules = sorted({v.rule.file for v in got if v.rule is not None})
        print(f"{'PASS' if ok else 'FAIL'} {case['id']} expect={case['expect']} "
              f"got={[v.outcome for v in got]} rules={rules}")
    print("confusion (expect, got): " + ", ".join(f"{k}={n}" for k, n in table.items()))
    print(f"RESULT cases_failed={failed}/{len(cases)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
