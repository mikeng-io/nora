"""Re-queue one terminal extraction after repair.

With DATABASE_URL set: uv run python scripts/extraction_requeue.py ID.
"""

from __future__ import annotations

import argparse
import asyncio

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.extraction import SqlExtraction
from nora.secret import credentials


async def requeue(extraction_id: int) -> bool:
    engine = create_sql_engine(credentials()["DATABASE_URL"])
    try:
        store = SqlExtraction(engine)
        return await store.requeue(extraction_id)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extraction_id", type=int)
    args = parser.parse_args()
    changed = asyncio.run(requeue(args.extraction_id))
    print("requeued" if changed else "not_failed")
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
