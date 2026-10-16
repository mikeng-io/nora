"""Re-derive stays at a few candidate (stay_radius_m, stay_min_dwell_seconds)
pairs against the LIVE presence_fixes corpus and report fragmentation
(stay count — too many = the radius is too tight) and bridging (max
radius_m any stay actually reached — approaching the candidate radius = it
is too loose) per candidate, so an operator can pick a validated value
instead of the inherited guess `stay_radius_m = 120` / `stay_min_dwell_seconds
= 600`.

Run deliberately, read-only, writes nothing:

    uv run python scripts/revalidate_stay_params.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from nora.domain.mobility import CHUNK_GAP_MS, RawFix, Stay, segment_stays
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import PRESENCE_FIXES

CANDIDATES = [
    (80.0, 600.0), (100.0, 600.0), (120.0, 600.0), (150.0, 600.0),
    (120.0, 300.0), (120.0, 900.0),
]


def _chunk_by_gap(fixes: list[RawFix], gap_ms: int) -> list[list[RawFix]]:
    """Split ts-ascending `fixes` wherever the gap to the next fix exceeds
    `gap_ms`.

    `MobilityRollup.run_once` does exactly this before it hands anything to
    `segment_stays`, and this script has to reproduce it or its numbers are
    not comparable to production's. `segment_stays` is purely SPATIAL — it
    has no gap-awareness at all — so a subject who returns to the same
    coordinates night after night has every visit merged into ONE
    pathologically long "stay" spanning all the dead time between them.
    That inflates `radius_m`/dwell and collapses the stay count, which is
    precisely the two numbers this script reports.

    Deliberately a local copy rather than an import of the rollup's private
    `_chunk_by_gap`: this repo does not reach across modules for a private
    function. Only the public `CHUNK_GAP_MS` is shared.
    """
    if not fixes:
        return []
    chunks: list[list[RawFix]] = [[fixes[0]]]
    for prev, cur in pairwise(fixes):
        if cur.ts - prev.ts > gap_ms:
            chunks.append([])
        chunks[-1].append(cur)
    return chunks


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


def _stays_for(
    fixes: list[RawFix], *, person_id: str, radius_m: float, min_dwell_ms: int
) -> list[Stay]:
    """Every stay `MobilityRollup.run_once` would derive from `fixes`.

    Mirrors `run_once`'s segmentation block: chunk by gap, segment each
    chunk independently, and — the part that is easy to miss — also count a
    NON-FINAL chunk's trailing open group, subject to the same min-dwell
    gate. That group is open only because its chunk ran out of fixes; the
    next chunk's first fix is an OBSERVED departure, so `run_once` closes
    it. Counting only `result.closed` silently drops one real stay per
    chunk, which is a bigger distortion than the un-chunked merging it was
    meant to fix.

    The FINAL chunk's open group is included on the same min-dwell test.
    `run_once` additionally requires `settled_seconds` to have elapsed
    against wall-clock `now`; this script reads a historical corpus whose
    last fix is minutes-to-days old, so that gate is already satisfied for
    every person but the one currently mid-stay — at most one stay across
    the whole run, far below the resolution of what is being compared here.
    """
    stays: list[Stay] = []
    for chunk in _chunk_by_gap(fixes, CHUNK_GAP_MS):
        result = segment_stays(
            chunk, subject_id=person_id, person_id=person_id, circle_id="",
            radius_m=radius_m, min_dwell_ms=min_dwell_ms,
        )
        stays.extend(result.closed)
        open_stay = result.open_stay
        if (
            open_stay is not None
            and open_stay.ended_ts - open_stay.started_ts >= min_dwell_ms
        ):
            stays.append(open_stay)
    return stays


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
            # Chunk FIRST, exactly as run_once does — see _chunk_by_gap.
            min_dwell_ms = int(min_dwell_s * 1000)
            stays_for_person = _stays_for(
                fixes, person_id=person_id, radius_m=radius_m,
                min_dwell_ms=min_dwell_ms,
            )
            total_stays += len(stays_for_person)
            max_radius_seen = max(
                max_radius_seen,
                max((s.radius_m for s in stays_for_person), default=0.0),
            )
        print(f"{radius_m:>10.0f} {min_dwell_s:>12.0f} {total_stays:>12} {max_radius_seen:>16.1f}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
