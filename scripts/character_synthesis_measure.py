"""Measure whether Character accounts are SYNTHESES or COMPRESSIONS.

Each account is read whole by a model with a frozen rubric, `--samples`
times: `instance_share` is the fraction of its text spent on specific
instances (verbatim quotes, single exchanges, named items, prices, dates)
rather than general patterns and their reasons, and `patterns` counts the
distinct patterns it states. A spread over 0.2 across samples is UNSTABLE and
a failed call is ERROR; neither is averaged in.

The score is per account, not per sentence: an account can state a trait in
every sentence and then bury it under quotes and instances, which a
per-sentence label cannot see.

Input is a JSON file of `[{"key": ..., "kind": "room|unit|person|nora", "body": ...}]`
exported by the operator (private: never committed). Output: per account and per
kind, the mean instance share, with the counts.

    ssh -f -N -L 20128:127.0.0.1:20128 norty
    export OMNIROUTE_API_KEY=...
    uv run python scripts/character_synthesis_measure.py accounts.json --model nora
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

from openai import AsyncOpenAI

from nora.secret import credentials

RUBRIC = (
    "You read one account Nora wrote of a friend group, a person in it, or herself. "
    "A SYNTHESIS states a few general patterns (how they talk, decide, treat each other "
    "or her) and why, with at most a brief example now and then. A COMPRESSION packs in "
    "specific instances: verbatim quotes, particular exchanges, single events, named "
    "items, prices, dates, lists of names, with a pattern attached. Estimate "
    "`instance_share`: the fraction of the account's text (0.0 to 1.0) spent on specific "
    "instances rather than on general patterns and their reasons. Count `patterns`: how "
    "many distinct general patterns it states. Reply with JSON only: "
    '{"instance_share": <0..1>, "patterns": <int>}.'
)


async def judge(
    client: AsyncOpenAI, model: str, body: str, effort: str,
) -> tuple[float, int] | None:
    try:
        r = await client.chat.completions.create(
            model=model, temperature=0.0, max_tokens=4096, reasoning_effort=effort,  # type: ignore[arg-type]
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": RUBRIC},
                      {"role": "user", "content": body}],
        )
        if r.choices[0].finish_reason == "length":
            print("judge error: cut off at max_tokens", file=sys.stderr)
            return None
        got = json.loads(r.choices[0].message.content or "")
        share, patterns = float(got["instance_share"]), int(got["patterns"])
        if not 0.0 <= share <= 1.0 or patterns < 0:
            return None
        return share, patterns
    except Exception as exc:  # noqa: BLE001 - a failed call is an ERROR, never a score
        print(f"judge error: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
        return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("accounts", type=Path)
    ap.add_argument("--model", default="nora")
    ap.add_argument("--config", type=Path, help="use configured provider and purpose route")
    ap.add_argument("--base-url", default="http://127.0.0.1:20128/v1")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--effort", default="low")
    args = ap.parse_args()
    key = credentials().get("OMNIROUTE_API_KEY", "")
    if args.config is not None:
        from nora.config import load
        cfg = load(args.config, require_secrets=False)
        args.base_url, key, args.model = cfg.endpoint("eval_judge", args.model)
    if not key:
        print("selected model credential is missing or empty", file=sys.stderr)
        return 2
    client = AsyncOpenAI(base_url=args.base_url, api_key=key, max_retries=1, timeout=120)
    accounts = json.loads(args.accounts.read_text(encoding="utf-8"))
    sem = asyncio.Semaphore(6)

    accounts = [a for a in accounts if a.get("body", "").strip()]

    async def one(acc: dict) -> tuple[dict, list[tuple[float, int] | None]]:
        async with sem:
            return acc, [await judge(client, args.model, acc["body"], args.effort)
                         for _ in range(args.samples)]

    by_kind: dict[str, list[float]] = defaultdict(list)
    for acc, draws in await asyncio.gather(*(one(a) for a in accounts)):
        if any(d is None for d in draws):
            print(f"{acc['kind']:7} {acc['key'][:18]:18} ERROR")
            continue
        shares = sorted(d[0] for d in draws if d is not None)
        pats = sorted(d[1] for d in draws if d is not None)
        median = shares[len(shares) // 2]
        state = "UNSTABLE" if shares[-1] - shares[0] > 0.2 else "ok"
        print(f"{acc['kind']:7} {acc['key'][:18]:18} chars={len(acc['body']):5} "
              f"instance_share={median:.2f} (draws {', '.join(f'{x:.2f}' for x in shares)}) "
              f"patterns={pats[len(pats) // 2]} {state}")
        if state == "ok":
            by_kind[acc["kind"]].append(median)
    for kind, vals in sorted(by_kind.items()):
        print(f"KIND {kind}: n={len(vals)} median_instance_share="
              f"{sorted(vals)[len(vals) // 2]:.2f} mean={sum(vals) / len(vals):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
