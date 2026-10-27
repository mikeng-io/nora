"""Enqueue historical sources and Nora context without calling a model.

DATABASE_URL=... uv run python scripts/extraction_backfill.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.app.composition import _note_unit_of
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.extraction import admission, enqueue, source_key
from nora.infra.sql.schema import EXTRACTION as BATCH
from nora.infra.sql.schema import EXTRACTION_ATTEMPT as ATTEMPT
from nora.infra.sql.schema import EXTRACTION_CLAIM as CLAIM
from nora.infra.sql.schema import EXTRACTION_QUEUE as QUEUE
from nora.infra.sql.schema import MESSAGE_DELETE, MESSAGES
from nora.policy import Policy, load_policy

CHUNK_SIZE = 500


async def backfill(
    engine: AsyncEngine, policy: Policy, *, days: int = 61,
    before_ms: int | None = None, dry_run: bool = False,
) -> dict[str, Any]:
    if days <= 0:
        raise ValueError("days must be positive")
    before_ms = before_ms if before_ms is not None else int(datetime.now(UTC).timestamp() * 1000)
    since_ms = before_ms - int(timedelta(days=days).total_seconds() * 1000)
    unit_of = _note_unit_of(policy)
    counts: Counter[str] = Counter(
        eligible=0, already_queued=0, would_enqueue=0, enqueued=0,
        context_eligible=0, context_already_queued=0, context_would_enqueue=0, context_enqueued=0,
    )
    skipped: Counter[str] = Counter()
    async with engine.begin() as conn:
        if dry_run:
            await conn.execute(sa.text("SET TRANSACTION READ ONLY"))
        # Metadata only: source text is never needed for admission or output.
        stmt = sa.select(
            MESSAGES.c.id, MESSAGES.c.platform, MESSAGES.c.conversation_id,
            MESSAGES.c.sender_id, MESSAGES.c.ts, MESSAGES.c.platform_message_id,
            MESSAGES.c.is_nora, MESSAGES.c.hidden_at,
            sa.exists().where(MESSAGE_DELETE.c.message_id == MESSAGES.c.id).label("deleted"),
        ).where(
            MESSAGES.c.ts >= since_ms, MESSAGES.c.ts < before_ms,
        ).order_by(MESSAGES.c.ts, MESSAGES.c.id)
        # Visibility is enforced again at batch formation/publication by source_rows.
        candidates = (await conn.execute(stmt)).mappings().all()
        queued = (await conn.execute(sa.select(
            QUEUE.c.message_id, QUEUE.c.platform, QUEUE.c.origin_room, QUEUE.c.source_key,
        ))).mappings().all()
        ids = {r["message_id"] for r in queued}
        keys = {(r["platform"], r["origin_room"], r["source_key"]) for r in queued}
    for offset in range(0, len(candidates), CHUNK_SIZE):
        async with engine.begin() as conn:
            if dry_run:
                await conn.execute(sa.text("SET TRANSACTION READ ONLY"))
            for row in candidates[offset:offset + CHUNK_SIZE]:
                unit, reason = admission(row, unit_of, sources_only=False)
                if reason is not None:
                    skipped[reason] += 1
                    continue
                if unit is None:
                    raise ValueError("extraction owner unavailable")
                prefix = "context_" if row["is_nora"] else ""
                counts[prefix + "eligible"] += 1
                key = (row["platform"], row["conversation_id"], source_key(row))
                if row["id"] in ids or key in keys:
                    counts[prefix + "already_queued"] += 1
                    continue
                counts[prefix + "would_enqueue"] += 1
                if not dry_run:
                    counts[prefix + "enqueued"] += int(
                        await enqueue(conn, row=row, unit=unit, historical=True)
                    )
                ids.add(row["id"])
                keys.add(key)
    return {"since_ms": since_ms, "before_ms": before_ms, **counts, "skipped": dict(skipped)}


async def status(engine: AsyncEngine) -> dict[str, Any]:
    """All marked history, including pruned sources and requeued batches."""
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="REPEATABLE READ")
        async with conn.begin():
            await conn.execute(sa.text("SET TRANSACTION READ ONLY"))
            state = sa.case(
                (sa.and_(QUEUE.c.message_id.is_(None), QUEUE.c.extraction_id.is_(None)), "retired"),
                else_=sa.func.coalesce(BATCH.c.state, "unbatched"),
            )
            states = {(state, is_source): count for state, is_source, count in (await conn.execute(
                sa.select(state, QUEUE.c.is_source, sa.func.count())
                .select_from(QUEUE.outerjoin(BATCH, QUEUE.c.extraction_id == BATCH.c.id))
                .where(QUEUE.c.historical.is_(True))
                .group_by(state, QUEUE.c.is_source)
            )).all()}
            batch_ids = sa.select(QUEUE.c.extraction_id).where(QUEUE.c.historical.is_(True))
            claims = (await conn.execute(sa.select(sa.func.count()).select_from(CLAIM).where(
                CLAIM.c.extraction_id.in_(batch_ids), CLAIM.c.retired_at.is_(None),
            ))).scalar_one()
            failures = (await conn.execute(
                sa.select(BATCH.c.id, sa.func.coalesce(ATTEMPT.c.failure_stage, "unknown"),
                          sa.func.count(ATTEMPT.c.id))
                .select_from(BATCH.outerjoin(ATTEMPT, sa.and_(
                    ATTEMPT.c.extraction_id == BATCH.c.id,
                    ATTEMPT.c.outcome.in_(["error", "started"]),
                )))
                .where(BATCH.c.id.in_(batch_ids), BATCH.c.state == "failed")
                .group_by(BATCH.c.id, ATTEMPT.c.failure_stage).order_by(BATCH.c.id)
            )).all()
    failed: dict[int, dict[str, int]] = {}
    for batch_id, stage, count in failures:
        failed.setdefault(batch_id, {})[stage] = count
    return {
        "rows": {state: states.get((state, True), 0) for state in
                 ("unbatched", "pending", "done", "failed", "retired")},
        "context_rows": {state: states.get((state, False), 0) for state in
                         ("unbatched", "pending", "done", "failed", "retired")},
        "claims_written": claims,
        "failed_batches": [{"id": key, "failure_stages": value} for key, value in failed.items()],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    policy = None if args.status else load_policy(args.policy)
    engine = create_sql_engine(os.environ["DATABASE_URL"])
    try:
        if args.status:
            return await status(engine)
        if policy is None:
            raise ValueError("policy unavailable")
        return await backfill(engine, policy, days=args.days, before_ms=args.before_ms,
                              dry_run=args.dry_run)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=Path("policy.toml"))
    parser.add_argument("--days", type=int, default=61)
    parser.add_argument("--before-ms", type=int,
                        help="exclusive message timestamp; freeze across runs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="counts only; read-only transaction")
    mode.add_argument("--status", action="store_true",
                      help="all historical queue rows, no time window")
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days must be positive")
    print(json.dumps(asyncio.run(run(args)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
