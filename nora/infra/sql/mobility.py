"""`MobilityStore` over SQLAlchemy Core — the DERIVED-layer read/write seam
(docs/superpowers/specs/2026-08-18-presence-analytics-substrate-design.md
§2, §4). A SECOND store in the SAME `nora` database as `PresenceStore`, over
the same kind of `AsyncEngine`.

This store does not classify or schedule — that is `nora.domain.mobility`
(pure) and `nora.analytics.rollup` (the loop) above it. It reads/writes
rows, applies the fail-closed watermark rules (id-keyed, recorded_at-grace
windowed), and nothing else.
"""

from __future__ import annotations

import time
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.mobility import (
    DerivedPlace,
    RawFix,
    Stay,
    TaggedPlace,
    Trip,
    best_corridor_match,
    hour_of_week_bins,
)
from nora.infra.sql.schema import (
    ANALYTICS_WATERMARK,
    PRESENCE_CORRIDORS,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_FIXES,
    PRESENCE_OCCUPANCY,
    PRESENCE_PLACE_RHYTHM,
    PRESENCE_PLACES,
    PRESENCE_STAYS,
    PRESENCE_TRIPS,
)

DERIVER_NAME = "mobility"

# The web consumer is deliberately bounded independently of the rollup's
# batch sizes.  These are the same operational limits as the portal's history
# endpoint: a 90-day dashboard window and at most 5,000 derived rows.
_MAX_ANALYTICS_WINDOW_MS = 90 * 24 * 60 * 60 * 1000
_MAX_ANALYTICS_POINTS = 5_000
_MAX_ANALYTICS_PLACES = 100
_ANALYTICS_TZ_NAME = "Asia/Hong_Kong"


def _trip_from_row(r: Any) -> Trip:
    """One `presence_trips` row -> `Trip`. `sequence` is JSONB (a JSON list
    of road names, or NULL on a row written before migration 0020) and
    `Trip.corridor` is a tuple, so the conversion is explicit."""
    return Trip(
        subject_id=r.subject_id, person_id=r.person_id, circle_id=r.circle_id,
        started_ts=r.started_ts, ended_ts=r.ended_ts,
        distance_m=r.distance_m, avg_speed_kmh=r.avg_speed_kmh,
        fix_count=r.fix_count,
        corridor=tuple(r.sequence) if r.sequence else (),
        corridor_id=r.corridor_id,
    )


class SqlMobilityStore:
    def __init__(self, engine: AsyncEngine, *, metrics: Any = None) -> None:
        """`metrics` is INJECTED, never imported — see `PresenceStore`'s
        docstring: `nora.infra` sits below `nora.obs` in the import-linter
        layers, so importing `nora.obs.metrics` here would break that
        boundary. `None` means this store does not count."""
        self._engine = engine
        self._metrics = metrics

    async def places_with_aggregates(self, *, subject_id: str) -> list[dict[str, Any]]:
        """Read derived places and their stay aggregates for one subject.

        Aggregates follow `presence_stays.place_id` — the link the rollup
        wrote — not a second containment test. The old read re-derived
        membership as «any stay within `radius_m` of the stored point» over
        the newest 5,000 stays, which disagreed with the rollup's own links
        (a stay near two places counted at both; a place whose row had moved
        counted stays it never linked) and silently truncated history (audit
        2026-09-27). SELECT-only; the web consumer never re-runs derivation.
        """
        # The cap keeps the most RECENTLY seen places: before DEC-190 a
        # subject held at most two rows and the cap was unreachable; places
        # are unbounded now, and an id-ascending cut would drop the newest.
        places_stmt = (
            sa.select(PRESENCE_DERIVED_PLACES)
            .where(PRESENCE_DERIVED_PLACES.c.subject_id == subject_id)
            .order_by(
                PRESENCE_DERIVED_PLACES.c.last_seen_ts.desc(),
                PRESENCE_DERIVED_PLACES.c.id.asc(),
            )
            .limit(_MAX_ANALYTICS_PLACES)
        )
        agg_stmt = (
            sa.select(
                PRESENCE_STAYS.c.place_id,
                sa.func.count().label("stay_count"),
                sa.func.coalesce(
                    sa.func.sum(
                        sa.func.greatest(
                            PRESENCE_STAYS.c.ended_ts - PRESENCE_STAYS.c.started_ts, 0,
                        )
                    ),
                    0,
                ).label("total_dwell_ms"),
            )
            .where(
                PRESENCE_STAYS.c.subject_id == subject_id,
                PRESENCE_STAYS.c.place_id.is_not(None),
            )
            .group_by(PRESENCE_STAYS.c.place_id)
        )
        async with self._engine.connect() as conn:
            places = (await conn.execute(places_stmt)).all()
            aggs = {r.place_id: r for r in (await conn.execute(agg_stmt)).all()}

        out: list[dict[str, Any]] = []
        for place in sorted(places, key=lambda p: p.id):
            agg = aggs.get(place.id)
            out.append(
                {
                    "id": place.id,
                    "kind": place.kind,
                    "label": place.label,
                    "saved": place.saved_place_id is not None,
                    "lat": place.lat,
                    "lng": place.lng,
                    "radius_m": place.radius_m,
                    "confidence": place.confidence,
                    "first_seen_ts": place.first_seen_ts,
                    "last_seen_ts": place.last_seen_ts,
                    "stay_count": int(agg.stay_count) if agg is not None else 0,
                    "total_dwell_ms": int(agg.total_dwell_ms) if agg is not None else 0,
                }
            )
        return out

    async def rhythm_bins(self, *, subject_id: str, window_ms: int) -> list[dict[str, int]]:
        """Stays inside a trailing window, dwell SPLIT across the local
        hours-of-week each one covers.

        It used to credit a stay's WHOLE dwell to the hour it started in, so a
        10-hour overnight stay beginning Monday 22:00 put all ten hours on
        «Monday 22:00» and none on the small hours — the heatmap showed
        people as absent exactly when they were most reliably somewhere
        (audit 2026-09-27). `hour_of_week_bins` is the same split Tier 3's
        occupancy uses, clipped to the window; `stay_count` counts the stays
        that touch a bin."""
        window_ms = min(max(window_ms, 0), _MAX_ANALYTICS_WINDOW_MS)
        if window_ms == 0:
            return []
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - window_ms
        stmt = (
            sa.select(PRESENCE_STAYS.c.started_ts, PRESENCE_STAYS.c.ended_ts)
            .where(
                PRESENCE_STAYS.c.subject_id == subject_id,
                PRESENCE_STAYS.c.ended_ts >= cutoff_ms,
            )
            .order_by(PRESENCE_STAYS.c.started_ts.desc())
            .limit(_MAX_ANALYTICS_POINTS)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()

        bins: dict[int, dict[str, int]] = {}
        for row in rows:
            split = hour_of_week_bins(
                max(row.started_ts, cutoff_ms), min(row.ended_ts, now_ms),
                _ANALYTICS_TZ_NAME,
            )
            for how, dwell in split.items():
                bucket = bins.setdefault(how, {"stay_count": 0, "total_dwell_ms": 0})
                bucket["stay_count"] += 1
                bucket["total_dwell_ms"] += dwell
        return [
            {
                "hour_of_week": how,
                "stay_count": bins[how]["stay_count"],
                "total_dwell_ms": bins[how]["total_dwell_ms"],
            }
            for how in sorted(bins)
        ]

    async def stays_for_subject_window(
        self, *, subject_id: str, window_ms: int,
    ) -> list[dict[str, Any]]:
        """Read bounded stays for ONE subject inside a trailing window, with
        the derived place's `kind`/`label` when the substrate linked one.

        The `day` consumer's read (the presence-analytics P1 spec §3, DEC-155):
        the same shape as `rhythm_bins` — one SELECT, clamped, `window_ms=0`
        answers empty — plus a LEFT JOIN onto `presence_derived_places` so a
        stay carries the KIND/LABEL of the place the substrate filed it under.
        Coordinates ARE selected (DEC-156): the `day` arm carries them so the
        model can fetch an exact location via a street-view/maps tool. They
        never enter a REPLY (R-28's speech half stands); the joined
        `kind`/`label` remain the speakable part of the linkage.
        SELECT-only, bounded by `_MAX_ANALYTICS_POINTS`, like every read the
        web portal serves.
        """
        window_ms = min(max(window_ms, 0), _MAX_ANALYTICS_WINDOW_MS)
        if window_ms == 0:
            return []
        cutoff_ms = int(time.time() * 1000) - window_ms
        stmt = (
            sa.select(
                PRESENCE_STAYS.c.started_ts, PRESENCE_STAYS.c.ended_ts,
                PRESENCE_STAYS.c.lat, PRESENCE_STAYS.c.lng,
                PRESENCE_DERIVED_PLACES.c.kind.label("place_kind"),
                PRESENCE_DERIVED_PLACES.c.label.label("place_label"),
            )
            .select_from(
                PRESENCE_STAYS.outerjoin(
                    PRESENCE_DERIVED_PLACES,
                    PRESENCE_STAYS.c.place_id == PRESENCE_DERIVED_PLACES.c.id,
                )
            )
            .where(
                PRESENCE_STAYS.c.subject_id == subject_id,
                PRESENCE_STAYS.c.started_ts >= cutoff_ms,
            )
            .order_by(PRESENCE_STAYS.c.started_ts.desc())
            .limit(_MAX_ANALYTICS_POINTS)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            {
                "started_ts": row.started_ts,
                "ended_ts": row.ended_ts,
                "lat": row.lat,
                "lng": row.lng,
                "place_kind": row.place_kind,
                "place_label": row.place_label,
            }
            for row in rows
        ]

    async def trips_with_corridors(
        self, *, subject_id: str, window_ms: int,
    ) -> dict[str, list[dict[str, Any]]]:
        """Read bounded trips with optional corridor metadata in one SELECT."""
        window_ms = min(max(window_ms, 0), _MAX_ANALYTICS_WINDOW_MS)
        if window_ms == 0:
            return {"trips": [], "corridors": []}
        cutoff_ms = int(time.time() * 1000) - window_ms
        stmt = (
            sa.select(
                PRESENCE_TRIPS.c.id.label("trip_id"),
                PRESENCE_TRIPS.c.started_ts,
                PRESENCE_TRIPS.c.ended_ts,
                PRESENCE_TRIPS.c.distance_m,
                PRESENCE_TRIPS.c.avg_speed_kmh,
                PRESENCE_TRIPS.c.fix_count,
                PRESENCE_TRIPS.c.corridor_id,
                PRESENCE_CORRIDORS.c.sequence.label("corridor_sequence"),
                PRESENCE_CORRIDORS.c.sample_count.label("corridor_sample_count"),
                PRESENCE_CORRIDORS.c.first_seen_ts.label("corridor_first_seen_ts"),
                PRESENCE_CORRIDORS.c.last_seen_ts.label("corridor_last_seen_ts"),
            )
            .select_from(
                PRESENCE_TRIPS.outerjoin(
                    PRESENCE_CORRIDORS,
                    PRESENCE_TRIPS.c.corridor_id == PRESENCE_CORRIDORS.c.id,
                )
            )
            .where(
                PRESENCE_TRIPS.c.subject_id == subject_id,
                PRESENCE_TRIPS.c.started_ts >= cutoff_ms,
            )
            .order_by(PRESENCE_TRIPS.c.started_ts.desc())
            .limit(_MAX_ANALYTICS_POINTS)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()

        trips: list[dict[str, Any]] = []
        corridors: dict[int, dict[str, Any]] = {}
        for row in rows:
            trips.append(
                {
                    "id": row.trip_id,
                    "started_ts": row.started_ts,
                    "ended_ts": row.ended_ts,
                    "distance_m": row.distance_m,
                    "avg_speed_kmh": row.avg_speed_kmh,
                    "fix_count": row.fix_count,
                    "corridor_id": row.corridor_id,
                    "corridor_sample_count": row.corridor_sample_count,
                }
            )
            if row.corridor_id is not None and row.corridor_id not in corridors:
                corridors[row.corridor_id] = {
                    "id": row.corridor_id,
                    "sample_count": row.corridor_sample_count,
                    "first_seen_ts": row.corridor_first_seen_ts,
                    "last_seen_ts": row.corridor_last_seen_ts,
                    "sequence": list(row.corridor_sequence or ()),
                }
        return {
            "trips": trips,
            "corridors": sorted(corridors.values(), key=lambda corridor: corridor["id"]),
        }

    async def watermark(self, *, subject_id: str) -> tuple[int, int]:
        stmt = sa.select(
            ANALYTICS_WATERMARK.c.last_fix_id, ANALYTICS_WATERMARK.c.last_cluster_id
        ).where(
            ANALYTICS_WATERMARK.c.deriver_name == DERIVER_NAME,
            ANALYTICS_WATERMARK.c.subject_id == subject_id,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
        return (row.last_fix_id, row.last_cluster_id) if row is not None else (0, 0)

    async def fixes_since(
        self, *, person_ids: list[str], after_fix_id: int, grace_ms: int,
        now_ms: int, limit: int,
    ) -> list[RawFix]:
        cutoff = now_ms - grace_ms
        stmt = (
            sa.select(
                PRESENCE_FIXES.c.id, PRESENCE_FIXES.c.ts,
                PRESENCE_FIXES.c.lat, PRESENCE_FIXES.c.lng,
                PRESENCE_FIXES.c.address, PRESENCE_FIXES.c.is_driving,
            )
            .where(
                PRESENCE_FIXES.c.person_id.in_(person_ids),
                PRESENCE_FIXES.c.id > after_fix_id,
                PRESENCE_FIXES.c.accuracy_m <= 100.0,
                PRESENCE_FIXES.c.wire_state == "live",
                PRESENCE_FIXES.c.recorded_at < cutoff,
            )
            .order_by(PRESENCE_FIXES.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            RawFix(
                id=r.id, ts=r.ts, lat=r.lat, lng=r.lng, address=r.address,
                is_driving=(
                    bool(r.is_driving) if r.is_driving is not None else None
                ),
            )
            for r in rows
        ]

    async def write_stays(self, stays: list[Stay]) -> None:
        if not stays:
            return
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(PRESENCE_STAYS),
                [
                    {
                        "subject_id": s.subject_id, "person_id": s.person_id,
                        "circle_id": s.circle_id, "started_ts": s.started_ts,
                        "ended_ts": s.ended_ts, "lat": s.lat, "lng": s.lng,
                        "radius_m": s.radius_m, "fix_count": s.fix_count,
                    }
                    for s in stays
                ],
            )

    async def stays_for_subject(self, *, subject_id: str) -> list[Stay]:
        stmt = (
            sa.select(PRESENCE_STAYS)
            .where(PRESENCE_STAYS.c.subject_id == subject_id)
            .order_by(PRESENCE_STAYS.c.started_ts.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            Stay(
                subject_id=r.subject_id, person_id=r.person_id, circle_id=r.circle_id,
                started_ts=r.started_ts, ended_ts=r.ended_ts,
                lat=r.lat, lng=r.lng, radius_m=r.radius_m, fix_count=r.fix_count,
                place_id=r.place_id,
            )
            for r in rows
        ]

    async def write_trips(self, trips: list[Trip]) -> None:
        if not trips:
            return
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(PRESENCE_TRIPS),
                [
                    {
                        "subject_id": t.subject_id, "person_id": t.person_id,
                        "circle_id": t.circle_id, "started_ts": t.started_ts,
                        "ended_ts": t.ended_ts, "distance_m": t.distance_m,
                        "avg_speed_kmh": t.avg_speed_kmh, "fix_count": t.fix_count,
                        # `list(...)` for the same reason `upsert_corridor`
                        # converts `PRESENCE_CORRIDORS.sequence` — JSONB
                        # takes a JSON array, and a Python tuple is not one.
                        "sequence": list(t.corridor),
                        "corridor_id": t.corridor_id,
                    }
                    for t in trips
                ],
            )

    async def trips_for_subject(self, *, subject_id: str) -> list[Trip]:
        """Every trip for the subject, oldest first, WITH its real road-name
        sequence.

        `corridor` used to come back hardcoded `()` here, justified by
        "`presence_trips` has no column for it and no caller re-reads it."
        Migration 0020 added `sequence` and `_record_corridors` now re-reads
        exactly this data every cycle, so both halves of that reasoning are
        gone. A store method that silently discarded a column it now has
        would be a trap for the next caller."""
        stmt = (
            sa.select(PRESENCE_TRIPS)
            .where(PRESENCE_TRIPS.c.subject_id == subject_id)
            .order_by(PRESENCE_TRIPS.c.started_ts.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_trip_from_row(r) for r in rows]

    async def unclustered_named_trips_for_subject(
        self, *, subject_id: str, limit: int
    ) -> list[Trip]:
        """The corridor-clustering POOL: this subject's trips that have a
        road-name sequence and are not linked to a corridor yet, newest
        first, capped at `limit`.

        `corridor_id IS NULL` is the durable "not yet processed" marker —
        the same shape `presence_stays.place_id IS NULL` already plays for
        Tier 3's first-place backfill — which is why Tier 5 needs no
        separate watermark column. A trip leaves this pool exactly when
        `set_trip_corridor_ids` links it.

        **The empty-sequence exclusion lives HERE, not in the caller.**
        `edit_distance((), x) == len(x)`, so an address-less trip is a
        similarity-neighbour of nearly any short real corridor: letting one
        into `cluster_trips` either discards a whole real cluster or
        contaminates a corridor's sample count and speed percentiles,
        depending on input order (Task 11 review, Important #1). A NULL
        `sequence` (a row predating migration 0020) is excluded by the same
        predicate and for the stronger reason that its sequence is not
        merely empty but unknown.

        **Newest first, so the cap drops the OLDEST trips.** A trip that
        has sat unlinked for a long time is one nothing has ever matched;
        ageing it out of the window is what stops a pool of permanent
        non-matches from crowding out the trips that just arrived."""
        stmt = (
            sa.select(PRESENCE_TRIPS)
            .where(
                PRESENCE_TRIPS.c.subject_id == subject_id,
                PRESENCE_TRIPS.c.corridor_id.is_(None),
                PRESENCE_TRIPS.c.sequence.is_not(None),
                sa.func.jsonb_array_length(PRESENCE_TRIPS.c.sequence) > 0,
            )
            .order_by(PRESENCE_TRIPS.c.started_ts.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_trip_from_row(r) for r in rows]

    async def set_trip_corridor_ids(
        self, *, subject_id: str, started_ts_in: list[int], corridor_id: int
    ) -> None:
        """Fills `corridor_id` ONLY where it is currently NULL — the exact
        same fill-once-never-overwrite shape `set_place_ids` established
        for `presence_stays.place_id` (Tier 3): a trip's link to a
        corridor, once set, is never silently reassigned by a later,
        different cluster touching the same address sequence."""
        if not started_ts_in:
            return
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(PRESENCE_TRIPS)
                .where(
                    PRESENCE_TRIPS.c.subject_id == subject_id,
                    PRESENCE_TRIPS.c.started_ts.in_(started_ts_in),
                    PRESENCE_TRIPS.c.corridor_id.is_(None),
                )
                .values(corridor_id=corridor_id)
            )

    async def existing_corridor(
        self, *, subject_id: str, sequence: tuple[str, ...], max_edit_distance: int,
    ) -> tuple[int, tuple[str, ...]] | None:
        """Identity by SIMILARITY, not exact match — a cluster's own
        representative sequence can legitimately vary trip to trip. Edit
        distance is not a Postgres-native operation over a JSONB array
        without an extension this project does not have, so this reads
        every corridor row for the subject (bounded: a person has a
        handful of real corridors, not thousands) and computes
        `edit_distance` in Python — the same "SQL narrows, Python decides"
        shape `nora/infra/sql/geocode_cache.py`'s `nearest_within` already
        established for a different metric (haversine).

        The DECISION itself is `nora.domain.mobility.best_corridor_match`,
        shared with `FakeMobilityStore` (final whole-branch review,
        Important #3): each store used to carry its own copy of the loop,
        and the two copies broke a distance tie differently — this one on
        whatever order Postgres returned its heap in, the fake's on dict
        insertion order. Ties break on the LOWEST corridor id, and that
        rule is now in ONE place both stores call. `ORDER BY id ASC` is
        kept as well: correctness no longer depends on it, but it costs
        nothing on an indexed column and it makes the SQL read as the same
        rule the Python enforces."""
        stmt = (
            sa.select(PRESENCE_CORRIDORS.c.id, PRESENCE_CORRIDORS.c.sequence)
            .where(PRESENCE_CORRIDORS.c.subject_id == subject_id)
            .order_by(PRESENCE_CORRIDORS.c.id.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return best_corridor_match(
            ((row.id, tuple(row.sequence)) for row in rows),
            sequence=sequence, max_edit_distance=max_edit_distance,
        )

    async def upsert_corridor(
        self, *, subject_id: str, sequence: tuple[str, ...], sample_count: int,
        now_ms: int, existing_id: int | None,
    ) -> int:
        """UPDATEs in place when `existing_id` is given, preserving the
        row's `id` — Tier 3 Critical #1's own lesson (`upsert_place`,
        commit 9616af3): delete-then-insert on every re-cluster cycle
        churns the id and orphans downstream data keyed off it
        (`presence_trips.corridor_id` here). INSERTs fresh otherwise."""
        async with self._engine.begin() as conn:
            if existing_id is not None:
                await conn.execute(
                    sa.update(PRESENCE_CORRIDORS).where(
                        PRESENCE_CORRIDORS.c.id == existing_id,
                    ).values(sample_count=sample_count, last_seen_ts=now_ms)
                )
                return existing_id
            result = await conn.execute(
                sa.insert(PRESENCE_CORRIDORS).values(
                    subject_id=subject_id, sequence=list(sequence),
                    sample_count=sample_count, first_seen_ts=now_ms,
                    last_seen_ts=now_ms,
                ).returning(PRESENCE_CORRIDORS.c.id)
            )
            return int(result.scalar_one())

    async def trip_speeds_for_corridor(
        self, *, subject_id: str, corridor_id: int,
    ) -> list[tuple[int, float]]:
        """Every (started_ts, avg_speed_kmh) pair for this corridor — the
        caller (rollup, Task 11) groups by hour-of-week itself, the same
        division of responsibility `stay_durations_for_place` (Tier 3)
        already has: this store reads rows, `nora.domain.mobility` decides
        what a timestamp means in local time."""
        stmt = sa.select(PRESENCE_TRIPS.c.started_ts, PRESENCE_TRIPS.c.avg_speed_kmh).where(
            PRESENCE_TRIPS.c.subject_id == subject_id,
            PRESENCE_TRIPS.c.corridor_id == corridor_id,
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [(r.started_ts, r.avg_speed_kmh) for r in rows]

    async def places_for_subject(self, *, subject_id: str) -> list[DerivedPlace]:
        """Every stored place for the subject, id-ascending — any number of
        them, two homes included (DEC-190 lifted the one-row-per-kind
        ceiling this store used to hold)."""
        stmt = (
            sa.select(PRESENCE_DERIVED_PLACES)
            .where(PRESENCE_DERIVED_PLACES.c.subject_id == subject_id)
            .order_by(PRESENCE_DERIVED_PLACES.c.id.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            DerivedPlace(
                id=r.id, subject_id=r.subject_id, circle_id=r.circle_id,
                lat=r.lat, lng=r.lng, radius_m=r.radius_m, kind=r.kind,
                confidence=r.confidence, label=r.label,
                saved_place_id=r.saved_place_id,
                first_seen_ts=r.first_seen_ts, last_seen_ts=r.last_seen_ts,
            )
            for r in rows
        ]

    async def place_by_id(self, *, place_id: int) -> DerivedPlace | None:
        """One derived place by id, whoever's it is: `route` resolves a
        place a presence read named this turn through it (design/27 §4.4 M3), and the CALLER decides,
        by the place's `subject_id`, whether this room may be told it."""
        stmt = sa.select(PRESENCE_DERIVED_PLACES).where(
            PRESENCE_DERIVED_PLACES.c.id == place_id,
        )
        async with self._engine.connect() as conn:
            r = (await conn.execute(stmt)).first()
        if r is None:
            return None
        return DerivedPlace(
            id=r.id, subject_id=r.subject_id, circle_id=r.circle_id,
            lat=r.lat, lng=r.lng, radius_m=r.radius_m, kind=r.kind,
            confidence=r.confidence, label=r.label,
            saved_place_id=r.saved_place_id,
            first_seen_ts=r.first_seen_ts, last_seen_ts=r.last_seen_ts,
        )

    async def save_place(
        self, *, subject_id: str, circle_id: str, place_id: int | None,
        lat: float, lng: float, radius_m: float,
        kind: str, confidence: float, label: str | None,
        saved_place_id: str | None, first_seen_ts: int, last_seen_ts: int,
    ) -> int:
        """UPDATE in place when `place_id` is given — the id is what
        `presence_stays.place_id`, `presence_occupancy` and
        `presence_place_rhythm` key off, so it is never churned — else INSERT.
        `kind='unlabeled'` is a stored value, never a deletion (DEC-190 rule
        4): the place is still where the person goes."""
        values = {
            "lat": lat, "lng": lng, "radius_m": radius_m, "kind": kind,
            "confidence": confidence, "label": label,
            "saved_place_id": saved_place_id,
            "first_seen_ts": first_seen_ts, "last_seen_ts": last_seen_ts,
        }
        async with self._engine.begin() as conn:
            if place_id is not None:
                await conn.execute(
                    sa.update(PRESENCE_DERIVED_PLACES)
                    .where(
                        PRESENCE_DERIVED_PLACES.c.id == place_id,
                        PRESENCE_DERIVED_PLACES.c.subject_id == subject_id,
                    )
                    .values(**values)
                )
                return place_id
            result = await conn.execute(
                sa.insert(PRESENCE_DERIVED_PLACES)
                .values(subject_id=subject_id, circle_id=circle_id, **values)
                .returning(PRESENCE_DERIVED_PLACES.c.id)
            )
            return int(result.scalar_one())

    async def delete_places(self, *, subject_id: str, place_ids: list[int]) -> None:
        """Drop places no stay resolves to any more, WITH their occupancy and
        rhythm rows — a Tier 3 row keyed on a vanished place is a ghost no
        consumer can resolve. The caller unlinks the stays first."""
        if not place_ids:
            return
        async with self._engine.begin() as conn:
            for table in (PRESENCE_OCCUPANCY, PRESENCE_PLACE_RHYTHM):
                await conn.execute(
                    sa.delete(table).where(
                        table.c.subject_id == subject_id,
                        table.c.place_id.in_(place_ids),
                    )
                )
            await conn.execute(
                sa.delete(PRESENCE_DERIVED_PLACES).where(
                    PRESENCE_DERIVED_PLACES.c.subject_id == subject_id,
                    PRESENCE_DERIVED_PLACES.c.id.in_(place_ids),
                )
            )

    async def relink_stays(
        self, *, subject_id: str, links: dict[int, int | None],
    ) -> None:
        """Set each stay's `place_id` (keyed by `started_ts`) to EXACTLY the
        given value, overwriting what is there. This replaces the fill-NULL-
        only `set_place_ids`, whose additivity is what let 88 stays stay
        linked to rows that had moved 3.8-12.1 km away (issue #339): a link
        must follow the place picture the reconcile computed, not whichever
        writer got there first."""
        if not links:
            return
        by_place: dict[int | None, list[int]] = {}
        for started_ts, place_id in links.items():
            by_place.setdefault(place_id, []).append(started_ts)
        async with self._engine.begin() as conn:
            for place_id, started in by_place.items():
                await conn.execute(
                    sa.update(PRESENCE_STAYS)
                    .where(
                        PRESENCE_STAYS.c.subject_id == subject_id,
                        PRESENCE_STAYS.c.started_ts.in_(started),
                    )
                    .values(place_id=place_id)
                )

    async def tagged_places(self) -> list[TaggedPlace]:
        """Every Life360 saved place, EVERY circle — duplicate tags merge
        across circles (design/27 §3.7), so the read cannot be circle-scoped."""
        stmt = sa.select(PRESENCE_PLACES).order_by(
            PRESENCE_PLACES.c.circle_id, PRESENCE_PLACES.c.place_id,
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            TaggedPlace(
                circle_id=r.circle_id, place_id=r.place_id, name=r.name,
                lat=r.lat, lng=r.lng, radius_m=r.radius_m,
            )
            for r in rows
        ]

    async def replace_occupancy(
        self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int,
    ) -> None:
        """Occupancy for one place, recomputed WHOLESALE from its linked
        stays. It used to ACCUMULATE per closed stay, which made it wrong the
        moment a link changed (the dwell stayed on the old place) and double
        counted any replay; a full replace is idempotent and follows the
        links."""
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(PRESENCE_OCCUPANCY).where(
                    PRESENCE_OCCUPANCY.c.subject_id == subject_id,
                    PRESENCE_OCCUPANCY.c.place_id == place_id,
                )
            )
            if bins:
                await conn.execute(
                    sa.insert(PRESENCE_OCCUPANCY),
                    [
                        {
                            "subject_id": subject_id, "place_id": place_id,
                            "hour_of_week": how, "dwell_ms": dwell,
                            "updated_ts": now_ms,
                        }
                        for how, dwell in sorted(bins.items())
                    ],
                )

    async def upsert_rhythm(
        self, *, subject_id: str, place_id: int, mean_arrival_minute: float,
        mean_departure_minute: float, duration_median_ms: int,
        duration_mad_ms: int, sample_count: int, now_ms: int,
    ) -> None:
        stmt = pg_insert(PRESENCE_PLACE_RHYTHM).values(
            subject_id=subject_id, place_id=place_id,
            mean_arrival_minute=mean_arrival_minute,
            mean_departure_minute=mean_departure_minute,
            duration_median_ms=duration_median_ms, duration_mad_ms=duration_mad_ms,
            sample_count=sample_count, updated_ts=now_ms,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[PRESENCE_PLACE_RHYTHM.c.subject_id, PRESENCE_PLACE_RHYTHM.c.place_id],
            set_={
                "mean_arrival_minute": stmt.excluded.mean_arrival_minute,
                "mean_departure_minute": stmt.excluded.mean_departure_minute,
                "duration_median_ms": stmt.excluded.duration_median_ms,
                "duration_mad_ms": stmt.excluded.duration_mad_ms,
                "sample_count": stmt.excluded.sample_count,
                "updated_ts": stmt.excluded.updated_ts,
            },
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def advance_fix_watermark(self, *, subject_id: str, fix_id: int) -> None:
        await self._upsert_watermark(subject_id=subject_id, last_fix_id=fix_id)

    async def advance_cluster_watermark(self, *, subject_id: str, fix_id: int) -> None:
        await self._upsert_watermark(subject_id=subject_id, last_cluster_id=fix_id)

    async def _upsert_watermark(
        self, *, subject_id: str, last_fix_id: int | None = None,
        last_cluster_id: int | None = None,
    ) -> None:
        async with self._engine.begin() as conn:
            existing = (
                await conn.execute(
                    sa.select(
                        ANALYTICS_WATERMARK.c.last_fix_id,
                        ANALYTICS_WATERMARK.c.last_cluster_id,
                    ).where(
                        ANALYTICS_WATERMARK.c.deriver_name == DERIVER_NAME,
                        ANALYTICS_WATERMARK.c.subject_id == subject_id,
                    )
                )
            ).first()
            new_fix = last_fix_id if last_fix_id is not None else (
                existing.last_fix_id if existing is not None else 0
            )
            new_cluster = last_cluster_id if last_cluster_id is not None else (
                existing.last_cluster_id if existing is not None else 0
            )
            if existing is None:
                await conn.execute(
                    sa.insert(ANALYTICS_WATERMARK),
                    {
                        "deriver_name": DERIVER_NAME, "subject_id": subject_id,
                        "last_fix_id": new_fix, "last_cluster_id": new_cluster,
                    },
                )
            else:
                await conn.execute(
                    sa.update(ANALYTICS_WATERMARK)
                    .where(
                        ANALYTICS_WATERMARK.c.deriver_name == DERIVER_NAME,
                        ANALYTICS_WATERMARK.c.subject_id == subject_id,
                    )
                    .values(
                        last_fix_id=new_fix, last_cluster_id=new_cluster,
                        updated_at=sa.text("now()"),
                    )
                )

    async def _seed_fix_for_test(self, **kw: Any) -> None:
        """Contract-suite seeding ONLY (tests/contract/…) — this store does
        not write presence_fixes in production; `PresenceStore.record_fix`
        does. Exists so the shared contract suite can seed identical rows
        against BOTH implementations without importing PresenceStore."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(PRESENCE_FIXES), {"circle_id": "c1", **kw})

    async def _seed_tagged_place_for_test(self, tag: TaggedPlace) -> None:
        """Contract-suite seeding ONLY — `presence_places` is written by the
        presence collector in production, never by this store."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(PRESENCE_PLACES), {
                "circle_id": tag.circle_id, "place_id": tag.place_id,
                "name": tag.name, "lat": tag.lat, "lng": tag.lng,
                "radius_m": tag.radius_m, "updated_ms": 0,
            })

    async def aclose(self) -> None:
        await self._engine.dispose()
