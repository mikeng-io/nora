"""Re-derive stays at a few candidate (stay_radius_m, stay_min_dwell_seconds)
pairs against the LIVE presence_fixes corpus and report fragmentation
(stay count — too many = the radius is too tight) and bridging (max
radius_m any stay actually reached — approaching the candidate radius = it
is too loose) per candidate, so an operator can pick a validated value
instead of the inherited guess `stay_radius_m = 120` / `stay_min_dwell_seconds
= 600` (design/27 §7.1, issue #6).

Run deliberately, read-only, writes nothing:

    uv run python scripts/revalidate_stay_params.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from nora.domain.mobility import RawFix, segment_stays
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import PRESENCE_FIXES

CANDIDATES = [
    (80.0, 600.0), (100.0, 600.0), (120.0, 600.0), (150.0, 600.0),
    (120.0, 300.0), (120.0, 900.0),
]


async def _fixes_for(engine, person_id: str) -> list[RawFix]:
    stmt = (
        sa.select(
            PRESENCE_FIXES.c.id, PRESENCE_FIXES.c.ts,
            PRESENCE_FIXES.c.lat, PRESENCE_FIXES.c.lng,
        )
        .where(
            PRESENCE_FIXES.c.person_id == person_id,
            PRESENCE_FIXES.c.accuracy_m <= 100.0,
            PRESENCE_FIXES.c.wire_state == "live",
        )
        .order_by(PRESENCE_FIXES.c.ts.asc())
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return [RawFix(id=r.id, ts=r.ts, lat=r.lat, lng=r.lng) for r in rows]


async def _subjects(engine) -> list[str]:
    stmt = sa.select(PRESENCE_FIXES.c.person_id).distinct()
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return [r.person_id for r in rows]


async def main() -> None:
    database_url = os.environ.get("NORA_DATABASE_URL", os.environ.get("DATABASE_URL", ""))
    if not database_url:
        print("NORA_DATABASE_URL or DATABASE_URL must be set", file=sys.stderr)
        sys.exit(1)
    engine = create_sql_engine(database_url)
    subjects = await _subjects(engine)
    print(f"{'radius_m':>10} {'min_dwell_s':>12} {'total_stays':>12} {'max_radius_seen':>16}")
    for radius_m, min_dwell_s in CANDIDATES:
        total_stays = 0
        max_radius_seen = 0.0
        for person_id in subjects:
            fixes = await _fixes_for(engine, person_id)
            if not fixes:
                continue
            result = segment_stays(
                fixes, subject_id=person_id, person_id=person_id, circle_id="",
                radius_m=radius_m, min_dwell_ms=int(min_dwell_s * 1000),
            )
            total_stays += len(result.closed)
            max_radius_seen = max(
                max_radius_seen, max((s.radius_m for s in result.closed), default=0.0)
            )
        print(f"{radius_m:>10.0f} {min_dwell_s:>12.0f} {total_stays:>12} {max_radius_seen:>16.1f}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
