"""Replay the LIVE presence corpus through the CURRENT rollup code, tick by
tick, and print proxy accuracy metrics per subject — the instrument behind
design/27 §1.4's before/after table.

Read-only: it reads `presence_fixes` and derives into an in-memory store; it
writes nothing to the database. Run it on the deploy host (or with a tunnel),
once on the code you want to compare against and once on the change:

    uv run python scripts/replay_presence_corpus.py [--days 15] [--wander 400|none]

The tick is the daemon's own (120 s), and a fix becomes visible only once its
`recorded_at` clears the grace window, exactly as `fixes_since` serves it: a
batch replay (seed everything, tick once) passes on bugs the tick path has
(design/27 §1.3). Metrics, all proxies because nothing here is labelled:

- `phantom`: trips under 500 m or under 5 km/h — stops misread as movement.
- `split`: consecutive stays within 300 m and 31.7 min of each other — one
  visit cut in two.
- `stay_cov`: share of the replayed span covered by stays.
- `unproc`: fixes the watermark never passed — a stall.

Output is aggregate counts only; never paste per-subject rows into an issue
or PR (AGENTS.md).
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
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
from nora.domain.mobility import DWELL_CAP_MS, RawFix
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import PRESENCE_FIXES
from nora.testing.mobility import FakeMobilityStore

_TICK_MS = 120_000


class _ReplayStore(FakeMobilityStore):
    """The contract-tested fake, with an id-bisecting `fixes_since` so a
    15-day, 10,000-tick replay is minutes rather than hours."""

    def load(self, rows: list[Any]) -> None:
        self._rows = sorted(rows, key=lambda r: r.id)
        self._ids = [r.id for r in self._rows]

    async def fixes_since(
        self, *, person_ids: list[str], after_fix_id: int, grace_ms: int,
        now_ms: int, limit: int,
    ) -> list[RawFix]:
        cut = now_ms - grace_ms
        out: list[RawFix] = []
        for r in self._rows[bisect.bisect_right(self._ids, after_fix_id):]:
            if len(out) >= limit:
                break
            if r.recorded_at < cut:
                out.append(RawFix(
                    id=r.id, ts=r.ts, lat=r.lat, lng=r.lng, address=r.address,
                    is_driving=None if r.is_driving is None else bool(r.is_driving),
                ))
        return out


def _haversine_m(a: float, b: float, c: float, d: float) -> float:
    p1, p2 = math.radians(a), math.radians(c)
    h = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


async def _replay(subject: str, rows: list[Any], cfg: RollupConfig) -> dict[str, float]:
    store = _ReplayStore()
    store.load(rows)
    rollup = MobilityRollup(store=store, config=cfg)
    start = min(r.ts for r in rows)
    end = max(r.ts for r in rows) + 3_600_000
    now = start
    while now < end:
        now += _TICK_MS
        await rollup.run_once(
            subject_id=subject, person_ids=[subject], circle_id="replay", now_ms=now,
        )
    stays = await store.stays_for_subject(subject_id=subject)
    trips = await store.trips_for_subject(subject_id=subject)
    last_fix_id, _ = await store.watermark(subject_id=subject)
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
        "unproc": sum(1 for r in rows if r.id > last_fix_id),
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
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
    finally:
        await engine.dispose()

    by_person: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        by_person[r.person_id].append(r)
    totals: dict[str, float] = defaultdict(float)
    print(f"{'subject':<16}{'stays':>6}{'trips':>6}{'phantom':>8}{'split':>6}{'stay_cov':>9}{'unproc':>7}")
    for subject in sorted(by_person):
        m = await _replay(subject, by_person[subject], cfg)
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
