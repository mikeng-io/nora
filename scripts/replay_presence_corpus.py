"""Replay the LIVE presence corpus through the CURRENT rollup code and print
proxy accuracy metrics per subject, for a before/after comparison of rollup
code.

Read-only: it reads `presence_fixes` and derives into an in-memory store; it
writes nothing to the database. Run it on the deploy host (or with a tunnel),
once on the code you want to compare against and once on the change:

    uv run python scripts/replay_presence_corpus.py [--days 15] [--wander 400|none]

The rollup is a fold over the Record — each person's fixes and the collector's successful polls,
one event at a time, on the Record's own clock — so how the replay is cut
into ticks cannot change what it computes (`tests/unit/test_rollup_
determinism.py` is the gate): it folds the corpus in one run. Metrics, all
proxies because nothing here is labelled:

- `phantom`: trips under 500 m or under 5 km/h — stops misread as movement.
- `split`: consecutive stays within 300 m and 31.7 min of each other — one
  visit cut in two.
- `stay_cov`: share of the replayed span covered by stays.
- `unproc`: fixes the fold still holds undecided at the end — a stall.

Output is aggregate counts only; never paste per-subject rows into an issue
or PR (AGENTS.md).
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
import time
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from nora.analytics.rollup import MobilityRollup, RollupConfig
from nora.config import AnalyticsConfig
from nora.domain.mobility import DWELL_CAP_MS
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import PRESENCE_FIXES, SOURCE_HEALTH
from nora.testing.mobility import FakeMobilityStore, _SeededFix, _SeededPoll


def _load(store: FakeMobilityStore, rows: list[Any], polls: list[Any]) -> None:
    """The corpus into the contract-tested fake's tables."""
    for r in rows:
        store._t.fixes.append(_SeededFix(
            id=r.id, person_id=r.person_id, ts=r.ts, lat=r.lat, lng=r.lng,
            accuracy_m=50.0, wire_state="live", recorded_at=r.recorded_at,
            address=r.address,
            is_driving=None if r.is_driving is None else bool(r.is_driving),
        ))
    for p in polls:
        store._t.polls.append(_SeededPoll(
            id=p.id, scope="replay", outcome="ok", at_ms=p.at_ms,
            recorded_ms=int(p.recorded_at.timestamp() * 1000),
        ))


def _haversine_m(a: float, b: float, c: float, d: float) -> float:
    p1, p2 = math.radians(a), math.radians(c)
    h = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


async def _replay(
    subject: str, rows: list[Any], polls: list[Any], cfg: RollupConfig,
) -> dict[str, float]:
    store = FakeMobilityStore()
    _load(store, rows, polls)
    rollup = MobilityRollup(store=store, config=cfg)
    start = min(r.ts for r in rows)
    end = max(max(r.recorded_at for r in rows), max((p.at_ms for p in polls), default=0))
    await rollup.run_once(
        subject_id=subject, person_ids=[subject], circle_id="replay", through_ms=end,
    )
    stays = await store.stays_for_subject(subject_id=subject)
    trips = await store.trips_for_subject(subject_id=subject)
    state = await store.fold_state(subject_id=subject)
    held = set(state.window_ids) if state is not None else set()
    return {
        "stays": len(stays),
        "trips": len(trips),
        "phantom": sum(1 for t in trips if t.distance_m < 500 or t.avg_speed_kmh < 5),
        "split": sum(
            1 for a, b in pairwise(stays)
            if b.started_ts - a.ended_ts <= DWELL_CAP_MS
            and _haversine_m(a.lat, a.lng, b.lat, b.lng) <= 300
        ),
        "stay_cov": sum(s.ended_ts - s.started_ts for s in stays) / (end - start),
        "unproc": sum(1 for r in rows if r.id in held),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(prog="replay_presence_corpus")
    parser.add_argument("--days", type=int, default=15)
    parser.add_argument("--wander", default=None, help="stay_wander_radius_m override, or 'none'")
    args = parser.parse_args()

    analytics = AnalyticsConfig()
    fields = {
        k: getattr(analytics, k) for k in RollupConfig.__dataclass_fields__
        if hasattr(analytics, k)
    }
    if args.wander is not None:
        fields["stay_wander_radius_m"] = None if args.wander == "none" else float(args.wander)
    cfg = RollupConfig(**fields)

    database_url = os.environ.get("NORA_DATABASE_URL", os.environ.get("DATABASE_URL", ""))
    if not database_url:
        print("NORA_DATABASE_URL or DATABASE_URL must be set", file=sys.stderr)
        raise SystemExit(2)
    engine = create_sql_engine(database_url)
    since = int(time.time() * 1000) - args.days * 86_400_000
    stmt = sa.select(
        PRESENCE_FIXES.c.person_id, PRESENCE_FIXES.c.id, PRESENCE_FIXES.c.ts,
        PRESENCE_FIXES.c.lat, PRESENCE_FIXES.c.lng, PRESENCE_FIXES.c.recorded_at,
        PRESENCE_FIXES.c.address, PRESENCE_FIXES.c.is_driving,
    ).where(
        PRESENCE_FIXES.c.ts >= since,
        PRESENCE_FIXES.c.accuracy_m <= 100.0,
        PRESENCE_FIXES.c.wire_state == "live",
    )
    polls_stmt = sa.select(
        SOURCE_HEALTH.c.id, SOURCE_HEALTH.c.at_ms, SOURCE_HEALTH.c.recorded_at,
    ).where(
        SOURCE_HEALTH.c.source == "presence", SOURCE_HEALTH.c.event == "poll",
        SOURCE_HEALTH.c.outcome == "ok", SOURCE_HEALTH.c.at_ms >= since,
    )
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
            polls = (await conn.execute(polls_stmt)).all()
    finally:
        await engine.dispose()

    by_person: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        by_person[r.person_id].append(r)
    totals: dict[str, float] = defaultdict(float)
    print(f"{'subject':<16}{'stays':>6}{'trips':>6}{'phantom':>8}{'split':>6}{'stay_cov':>9}{'unproc':>7}")
    for subject in sorted(by_person):
        m = await _replay(subject, by_person[subject], polls, cfg)
        for k, v in m.items():
            totals[k] += v
        print(
            f"{subject:<16}{m['stays']:>6.0f}{m['trips']:>6.0f}{m['phantom']:>8.0f}"
            f"{m['split']:>6.0f}{m['stay_cov']:>9.2f}{m['unproc']:>7.0f}"
        )
    n = max(1, len(by_person))
    print(
        f"{'TOTAL':<16}{totals['stays']:>6.0f}{totals['trips']:>6.0f}"
        f"{totals['phantom']:>8.0f}{totals['split']:>6.0f}"
        f"{totals['stay_cov'] / n:>9.2f}{totals['unproc']:>7.0f}"
    )


if __name__ == "__main__":
    asyncio.run(main())
