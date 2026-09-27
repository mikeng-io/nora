"""Run the Tier 4 forecast's committed instrument against the LIVE derived
stays: a rolling-origin backtest per subject, one local day at a time, each
day predicted only from stays that ended before it began, scored against the
place the stays actually covered most in each hour, beside the trivial
«always their usual place» baseline (design/27 §4.1).

Read-only, writes nothing. Its output is a measurement by ONE party; per
design/27 §4.1 a figure it prints is evidence only once someone other than
the forecaster's author has re-run it. It carries one stated leak — place
identities are today's — see `backtest_forecast`'s docstring.

    uv run python scripts/backtest_presence_forecast.py [--days 28] [--subject grant_carver]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from nora.domain.mobility_forecast import backtest_forecast
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.schema import PRESENCE_STAYS

_DAY_MS = 86_400_000


async def main() -> None:
    parser = argparse.ArgumentParser(prog="backtest_presence_forecast")
    parser.add_argument("--days", type=int, default=28, help="test days, ending today")
    parser.add_argument("--subject", action="append", help="subject_id (repeatable)")
    parser.add_argument("--tz", default="Asia/Hong_Kong")
    args = parser.parse_args()

    database_url = os.environ.get("NORA_DATABASE_URL", os.environ.get("DATABASE_URL", ""))
    if not database_url:
        print("NORA_DATABASE_URL or DATABASE_URL must be set", file=sys.stderr)
        raise SystemExit(2)
    engine = create_sql_engine(database_url)
    store = SqlMobilityStore(engine)
    try:
        subjects = args.subject
        if not subjects:
            async with engine.connect() as conn:
                rows = await conn.execute(sa.select(PRESENCE_STAYS.c.subject_id).distinct())
                subjects = sorted(r.subject_id for r in rows)
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - args.days * _DAY_MS
        print(f"{'subject':<24}{'days':>6}{'bins':>7}{'forecast':>10}{'baseline':>10}")
        for subject in subjects:
            stays = await store.stays_for_subject(subject_id=subject)
            result = backtest_forecast(
                stays, tz_name=args.tz, start_ms=start_ms, end_ms=end_ms,
            )
            if not result.bins:
                print(f"{subject:<24}{0:>6}{0:>7}{'-':>10}{'-':>10}")
                continue
            print(
                f"{subject:<24}{len(result.days):>6}{result.bins:>7}"
                f"{result.accuracy:>10.1%}{result.baseline_accuracy:>10.1%}"
            )
    finally:
        await store.aclose()


if __name__ == "__main__":
    asyncio.run(main())
