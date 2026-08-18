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
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.mobility import LabelResult, RawFix, Stay
from nora.infra.sql.schema import (
    ANALYTICS_WATERMARK,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_FIXES,
    PRESENCE_STAYS,
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
        return [RawFix(id=r.id, ts=r.ts, lat=r.lat, lng=r.lng) for r in rows]

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
            )
            for r in rows
        ]

    async def existing_place(
        self, *, subject_id: str, kind: str
    ) -> tuple[int, LabelResult] | None:
        stmt = sa.select(PRESENCE_DERIVED_PLACES).where(
            PRESENCE_DERIVED_PLACES.c.subject_id == subject_id,
            PRESENCE_DERIVED_PLACES.c.kind == kind,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
        if row is None:
            return None
        return row.id, LabelResult(
            kind=row.kind, confidence=row.confidence,
            most_recent_support_ts=None,  # not persisted; recomputed each cycle
        )

    async def upsert_place(
        self, *, subject_id: str, circle_id: str, lat: float, lng: float,
        radius_m: float, result: LabelResult, now_ms: int,
    ) -> None:
        """Delete-then-insert for THIS subject's `kind`, in one transaction
        — the same shape `PresenceStore.replace_places` uses, and for the
        same reason: a handful of rarely-changing rows per subject, so the
        simplest write that cannot leave a stale row behind wins. A
        downgrade to `unlabeled` deletes the row entirely rather than
        storing a row with kind='unlabeled' — `existing_place` for a kind
        that no longer holds correctly returns None (Step 1's
        `test_a_stale_place_can_be_downgraded_to_unlabeled_in_place`)."""
        async with self._engine.begin() as conn:
            # kind is only ever 'home' or 'work' as a STORED row — an
            # 'unlabeled' result deletes whichever of the two it was.
            await conn.execute(
                sa.delete(PRESENCE_DERIVED_PLACES).where(
                    PRESENCE_DERIVED_PLACES.c.subject_id == subject_id,
                    PRESENCE_DERIVED_PLACES.c.kind.in_(["home", "work"])
                    if result.kind == "unlabeled"
                    else PRESENCE_DERIVED_PLACES.c.kind == result.kind,
                )
            )
            if result.kind != "unlabeled":
                await conn.execute(
                    sa.insert(PRESENCE_DERIVED_PLACES),
                    {
                        "subject_id": subject_id, "circle_id": circle_id,
                        "lat": lat, "lng": lng, "radius_m": radius_m,
                        "kind": result.kind, "confidence": result.confidence,
                        "label": None, "saved_place_id": None,
                        "first_seen_ts": now_ms, "last_seen_ts": now_ms,
                    },
                )

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
