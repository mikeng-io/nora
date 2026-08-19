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

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.mobility import LabelResult, RawFix, Stay, Trip
from nora.infra.sql.schema import (
    ANALYTICS_WATERMARK,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_FIXES,
    PRESENCE_OCCUPANCY,
    PRESENCE_PLACE_RHYTHM,
    PRESENCE_STAYS,
    PRESENCE_TRIPS,
)

DERIVER_NAME = "mobility"


class SqlMobilityStore:
    def __init__(self, engine: AsyncEngine, *, metrics: Any = None) -> None:
        """`metrics` is INJECTED, never imported — see `PresenceStore`'s
        docstring: `nora.infra` sits below `nora.obs` in the import-linter
        layers, so importing `nora.obs.metrics` here would break that
        boundary. `None` means this store does not count."""
        self._engine = engine
        self._metrics = metrics

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
                PRESENCE_FIXES.c.address,
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
            RawFix(id=r.id, ts=r.ts, lat=r.lat, lng=r.lng, address=r.address)
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

    async def set_place_ids(
        self, *, subject_id: str, started_ts_in: list[int], place_id: int
    ) -> None:
        """Fills `place_id` ONLY where it is currently NULL — a stay's link
        to a place, once set, is never silently reassigned by a later,
        different cluster touching the same coordinate (Task 10's own
        docstring on why this can only ever be additive)."""
        if not started_ts_in:
            return
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(PRESENCE_STAYS)
                .where(
                    PRESENCE_STAYS.c.subject_id == subject_id,
                    PRESENCE_STAYS.c.started_ts.in_(started_ts_in),
                    PRESENCE_STAYS.c.place_id.is_(None),
                )
                .values(place_id=place_id)
            )

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
                        "corridor_id": t.corridor_id,
                    }
                    for t in trips
                ],
            )

    async def trips_for_subject(self, *, subject_id: str) -> list[Trip]:
        stmt = (
            sa.select(PRESENCE_TRIPS)
            .where(PRESENCE_TRIPS.c.subject_id == subject_id)
            .order_by(PRESENCE_TRIPS.c.started_ts.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            Trip(
                subject_id=r.subject_id, person_id=r.person_id, circle_id=r.circle_id,
                started_ts=r.started_ts, ended_ts=r.ended_ts,
                distance_m=r.distance_m, avg_speed_kmh=r.avg_speed_kmh,
                fix_count=r.fix_count,
                # `presence_trips` has NO `corridor` column of its own
                # (design/27 §4, issue #6 follow-up) — a trip's address
                # sequence is deliberately NOT persisted per-row. The ONLY
                # consumer (Task 11's rollup wiring) calls `cluster_trips`
                # against `extract_trips`'s in-memory `Trip` objects, never
                # against a re-read from this store, and this plan's Global
                # Constraint ("recompute from the full sample set, every
                # time") means there is no later caller that re-clusters
                # from storage either — so there is nothing here that ever
                # needs `corridor` back. Do not "fix" this into a schema
                # change; it would add a JSONB column with no reader.
                corridor=(),
                corridor_id=r.corridor_id,
            )
            for r in rows
        ]

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

    async def existing_place(
        self, *, subject_id: str, kind: str
    ) -> tuple[int, float, float, float, LabelResult, str | None] | None:
        stmt = sa.select(PRESENCE_DERIVED_PLACES).where(
            PRESENCE_DERIVED_PLACES.c.subject_id == subject_id,
            PRESENCE_DERIVED_PLACES.c.kind == kind,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
        if row is None:
            return None
        return (
            row.id, row.lat, row.lng, row.radius_m,
            LabelResult(
                kind=row.kind, confidence=row.confidence,
                most_recent_support_ts=None,  # not persisted; recomputed each cycle
            ),
            row.label,
        )

    async def upsert_place(
        self, *, subject_id: str, circle_id: str, kind: str, lat: float,
        lng: float, radius_m: float, result: LabelResult, now_ms: int,
        label: str | None = None,
    ) -> None:
        """Delete-then-insert ONLY for a genuine kind reassignment or an
        unlabeled downgrade. The common case — `kind == result.kind`, Speed 3
        reconfirming the SAME physical place every re-cluster cycle — UPDATEs
        the existing row in place instead, preserving its `id`. Found by
        Task 10's review: `presence_stays.place_id` / `presence_occupancy` /
        `presence_place_rhythm` all key off this id, and churning it every
        cycle orphaned all of Tier 3's data and left the anomaly baseline
        permanently empty in production. See this method's git history for
        the original delete-then-insert-always docstring/rationale, which is
        still correct for the reassignment/downgrade branches below.

        `kind` is the slot being RE-EVALUATED — independent of
        `result.kind`, the classification the recheck actually produced.
        The delete clears rows matching EITHER `kind` (the slot being
        re-evaluated) OR `result.kind` (the kind about to be written, when
        it is a real kind and differs from `kind`) — never just one or the
        other, and never `IN ('home', 'work')` unconditionally. Two
        distinct bugs live on either side of that: deleting only `kind`
        would leave a stale row behind under `result.kind` if a subject
        already held one there from a DIFFERENT cluster (a `home` recheck
        that resolves straight to `work`, with an existing unrelated `work`
        place already on file, would otherwise leave two `kind='work'`
        rows — `presence_derived_places` has no unique constraint on
        `(subject_id, kind)`, only an index, so `existing_place`'s
        `.first()` read would become non-deterministic from that point);
        deleting unconditionally over BOTH `home` and `work` regresses to
        the original bug this store was built to avoid (a `home` recheck
        must never delete an unrelated, still-valid `work` row it never
        touched).

        A downgrade to `unlabeled` deletes the row entirely rather than
        storing a row with kind='unlabeled' — `existing_place` for a kind
        that no longer holds correctly returns None (Step 1's
        `test_a_stale_place_can_be_downgraded_to_unlabeled_in_place`)."""
        if kind == result.kind and result.kind != "unlabeled":
            async with self._engine.begin() as conn:
                existing = (await conn.execute(
                    sa.select(PRESENCE_DERIVED_PLACES.c.id).where(
                        PRESENCE_DERIVED_PLACES.c.subject_id == subject_id,
                        PRESENCE_DERIVED_PLACES.c.kind == result.kind,
                    )
                )).first()
                if existing is not None:
                    await conn.execute(
                        sa.update(PRESENCE_DERIVED_PLACES).where(
                            PRESENCE_DERIVED_PLACES.c.id == existing.id,
                        ).values(
                            lat=lat, lng=lng, radius_m=radius_m,
                            confidence=result.confidence, label=label,
                            last_seen_ts=now_ms,
                        )
                    )
                    return
        delete_kinds = {kind}
        if result.kind != "unlabeled":
            delete_kinds.add(result.kind)
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(PRESENCE_DERIVED_PLACES).where(
                    PRESENCE_DERIVED_PLACES.c.subject_id == subject_id,
                    PRESENCE_DERIVED_PLACES.c.kind.in_(delete_kinds),
                )
            )
            if result.kind != "unlabeled":
                await conn.execute(
                    sa.insert(PRESENCE_DERIVED_PLACES),
                    {
                        "subject_id": subject_id, "circle_id": circle_id,
                        "lat": lat, "lng": lng, "radius_m": radius_m,
                        "kind": result.kind, "confidence": result.confidence,
                        "label": label, "saved_place_id": None,
                        "first_seen_ts": now_ms, "last_seen_ts": now_ms,
                    },
                )

    async def record_occupancy(
        self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int
    ) -> None:
        if not bins:
            return
        async with self._engine.begin() as conn:
            for hour_of_week, dwell_ms in bins.items():
                stmt = pg_insert(PRESENCE_OCCUPANCY).values(
                    subject_id=subject_id, place_id=place_id,
                    hour_of_week=hour_of_week, dwell_ms=dwell_ms, updated_ts=now_ms,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        PRESENCE_OCCUPANCY.c.subject_id, PRESENCE_OCCUPANCY.c.place_id,
                        PRESENCE_OCCUPANCY.c.hour_of_week,
                    ],
                    set_={
                        "dwell_ms": PRESENCE_OCCUPANCY.c.dwell_ms + stmt.excluded.dwell_ms,
                        "updated_ts": stmt.excluded.updated_ts,
                    },
                )
                await conn.execute(stmt)

    async def stay_durations_for_place(
        self, *, subject_id: str, place_id: int
    ) -> list[int]:
        stmt = sa.select(PRESENCE_STAYS.c.started_ts, PRESENCE_STAYS.c.ended_ts).where(
            PRESENCE_STAYS.c.subject_id == subject_id,
            PRESENCE_STAYS.c.place_id == place_id,
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [r.ended_ts - r.started_ts for r in rows]

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

    async def aclose(self) -> None:
        await self._engine.dispose()
