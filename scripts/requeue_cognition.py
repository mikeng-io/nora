"""Operator requeue of one parked stimulus; writes an append-only requeue receipt."""
from __future__ import annotations

import argparse
import asyncio
import os

from nora.infra.sql.comprehend import SqlComprehendRecords
from nora.infra.sql.engine import create_sql_engine


async def requeue(unit: str, stimulus: int) -> None:
    engine = create_sql_engine(os.environ["NORA_DATABASE_URL"])
    try:
        records = SqlComprehendRecords(engine, media_join=None,
                                      rooms_by_unit={}, persons_by_unit={})
        await records.failures(unit, stimulus, reset=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--stimulus", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(requeue(args.unit, args.stimulus))
