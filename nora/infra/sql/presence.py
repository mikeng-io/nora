"""`PresenceStore` over SQLAlchemy Core — the presence record.

A SECOND store in the SAME `nora` database as `SqlRecordStore`, over the same
kind of `AsyncEngine`. It holds the change-recorder's write, two reads, and the
retention prune. No semantics live here — classification (`wire_state`),
disclosure (R-21) and the daemon loop are all above it.

**The write is a change-recorder, not a sampler.** `record_fix`
inserts a row ONLY when the incoming `ts` strictly exceeds the member's last
stored `ts`. Polling buys zero freshness — a stationary phone returns the
identical fix for many minutes — so recording every poll would be pure
duplication. The `since`-style dedup (read the member's latest `ts`, compare,
insert-or-skip, in one transaction) is what makes the table a time-series of
DISTINCT fixes: two reads of an unmoved member produce one row.

**Prune is a periodic sweep off the write path**, the identical
shape `SqlRecordStore.prune_all` uses and for the identical reason: a retention
chore must never contend with a collection write. It is time-windowed (older
than `retention_days`), not count-windowed like messages — presence is bounded
by age, not by per-conversation depth.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.presence import PresenceFix, SavedPlace, WireState
from nora.infra.sql.item_registration import SqlItemRegistration, epoch_ms
from nora.infra.sql.item_retention import tombstone_items
from nora.infra.sql.schema import PRESENCE_FIXES, PRESENCE_PLACES

log = logging.getLogger("nora.presence.store")

DEFAULT_RETENTION_DAYS = 180
_MS_PER_DAY = 86_400_000


class PresenceStore:
    """The read/write seam over `presence_fixes`.

    Built over the SAME engine as `SqlRecordStore` in production (one `nora`
    database, one pool), but takes its own `AsyncEngine` so a test — or a
    separate collector process — can own its lifecycle independently.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        metrics: Any = None,
        registration: SqlItemRegistration | None = None,
        aggregate_graph: Any = None,
    ) -> None:
        """`metrics` is INJECTED, never imported, for the reason
        `SqlRecordStore` documents: `nora.infra` sits below `nora.obs` in the
        import-linter layers, so importing `nora.obs.metrics` here breaks
        contract. `None` means this store does not count — correct for unit
        tests that assert only on rows."""
        self._engine = engine
        self._retention_days = retention_days
        self._metrics = metrics
        self._registration = registration
        self._aggregate_graph = aggregate_graph

    async def recorded_circles(self, person_ids: Sequence[str]) -> dict[str, tuple[str, ...]]:
        """Observed Record circles, not current provider membership or collection authority."""
        stmt = sa.select(PRESENCE_FIXES.c.person_id, PRESENCE_FIXES.c.circle_id).where(
            PRESENCE_FIXES.c.person_id.in_(person_ids)).distinct().order_by(
                PRESENCE_FIXES.c.person_id, PRESENCE_FIXES.c.circle_id)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        out: dict[str, list[str]] = {}
        for person, circle in rows:
            out.setdefault(person, []).append(circle)
        return {person: tuple(circles) for person, circles in out.items()}

    async def record_fix(self, *, fix: PresenceFix) -> bool:
        """Insert `fix` iff its `ts` advances past the member's last stored `ts`.

        Returns True when a row was written, False when the fix was a duplicate
        of (or older than) the last one — which is the ordinary steady state for
        a still or backgrounded phone. The read and the conditional insert run
        in ONE transaction so a concurrent writer cannot slip a newer fix
        between them; today there is one collector process, so this is belt for
        braces, but it keeps the change-recorder invariant true by construction
        rather than by deployment assumption.
        """
        latest = sa.select(sa.func.max(PRESENCE_FIXES.c.ts)).where(
            PRESENCE_FIXES.c.circle_id == fix.circle_id,
            PRESENCE_FIXES.c.person_id == fix.person_id,
        )
        insert = sa.insert(PRESENCE_FIXES).values(
            circle_id=fix.circle_id,
            person_id=fix.person_id,
            ts=fix.ts,
            lat=fix.lat,
            lng=fix.lng,
            accuracy_m=fix.accuracy_m,
            is_driving=None if fix.is_driving is None else int(fix.is_driving),
            battery=fix.battery,
            address=fix.address,
            speed_kmh=fix.speed_kmh,
            wire_state=fix.wire_state.value,
            recorded_at=fix.recorded_at,
        )
        registration = self._registration
        units = (
            await registration.scope.presence_units(fix.person_id, fix.circle_id)
            if registration else ()
        )
        written = False
        async with self._engine.begin() as conn:
            for unit in units or (None,):
                last_ts = (await conn.execute(
                    latest.where(PRESENCE_FIXES.c.unit.is_not_distinct_from(unit)),
                )).scalar_one_or_none()
                if last_ts is not None and fix.ts <= last_ts:
                    continue
                values = await registration.planes(
                    conn, PRESENCE_FIXES, unit=unit, occurred_at=epoch_ms(fix.ts),
                    persons=(fix.person_id,),
                ) if registration else {}
                await conn.execute(insert.values(**values))
                written = True
        return written

    async def latest_fix(
        self, *, circle_id: str, person_id: str
    ) -> PresenceFix | None:
        """The member's most recent stored fix, or None if there is none.

        The read `whereis` makes: one row off the `(circle_id, person_id, ts
        DESC)` index. `None` is «nothing recorded», which `whereis` renders as
        `unavailable` — never null-island."""
        stmt = (
            _select_fix()
            .where(
                PRESENCE_FIXES.c.circle_id == circle_id,
                PRESENCE_FIXES.c.person_id == person_id,
            )
            .order_by(PRESENCE_FIXES.c.ts.desc(), PRESENCE_FIXES.c.id.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
        return _row_to_fix(row) if row is not None else None

    async def window(
        self, *, circle_id: str, person_id: str, since_ts: int, until_ts: int
    ) -> list[PresenceFix]:
        """One member's fixes in `[since_ts, until_ts]`, OLDEST FIRST.

        The read derivation makes (stay-points, "before"): a member's fixes over
        a time window, clustered app-side. Oldest-first
        because a movement history has to be read chronologically, the same
        ordering `RecordStore.unattributed` is careful about.
        """
        stmt = (
            _select_fix()
            .where(
                PRESENCE_FIXES.c.circle_id == circle_id,
                PRESENCE_FIXES.c.person_id == person_id,
                PRESENCE_FIXES.c.ts >= since_ts,
                PRESENCE_FIXES.c.ts <= until_ts,
            )
            .ext(distinct_on(PRESENCE_FIXES.c.ts))
            .order_by(PRESENCE_FIXES.c.ts.asc(), PRESENCE_FIXES.c.id.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_fix(r) for r in rows]

    async def replace_places(
        self, *, circle_id: str, places: list[SavedPlace], partial: bool = False,
        inputs: Sequence[Mapping[str, Any]] = (),
        observed_persons: Sequence[str] | None = None,
    ) -> None:
        """Apply one circle's saved-place snapshot in each observing unit."""
        scope = self._registration.scope if self._registration else None
        placements = await scope.circle_placements(
            circle_id, observed_persons=observed_persons,
        ) if scope else {None: ()}
        by_id = {place.place_id: place for place in places}
        async with self._engine.begin() as conn:
            for unit, persons in (placements or {None: ()}).items():
                await self._write_place_heads(
                    conn, circle_id, unit, by_id, partial, inputs, persons,
                )
            if not partial and placements is not None:
                table = PRESENCE_PLACES
                prior_units = (
                    await conn.scalars(sa.select(table.c.unit).where(
                        table.c.circle_id == circle_id, table.c.state == "live",
                    ).distinct())
                ).all()
                for unit in prior_units:
                    if unit not in placements:
                        await self._write_place_heads(conn, circle_id, unit, {}, False, (), ())

    async def _write_place_heads(
        self, conn: AsyncConnection, circle_id: str, unit: str | None,
        by_id: dict[str, SavedPlace], partial: bool, inputs: Sequence[Mapping[str, Any]],
        persons: tuple[str, ...],
    ) -> None:
        table = PRESENCE_PLACES
        scope = (table.c.circle_id == circle_id, table.c.unit.is_not_distinct_from(unit))
        if not partial:
            gone = (await conn.execute(sa.select(table).where(
                *scope, table.c.state == "live", table.c.place_id.not_in(by_id),
            ).with_for_update())).mappings().all()
            for prior in gone:
                if prior["graph_id"] is not None and self._aggregate_graph is not None:
                    payload = json.dumps(dict(prior), default=str, sort_keys=True)
                    await self._aggregate_graph.tombstone_in(
                        conn, table, row_id=prior["graph_id"], unit=unit,
                        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
                    )
                else:
                    await conn.execute(sa.delete(table).where(
                        *scope, table.c.place_id == prior["place_id"],
                    ))
        for place in by_id.values():
            await self._write_place_head(conn, circle_id, unit, place, inputs, persons)

    async def _write_place_head(
        self, conn: AsyncConnection, circle_id: str, unit: str | None, place: SavedPlace,
        inputs: Sequence[Mapping[str, Any]], persons: tuple[str, ...],
    ) -> None:
        planes: dict[str, Any] = {}
        registration = self._registration
        if registration is not None and self._aggregate_graph is not None:
            at = epoch_ms(place.updated_ms)
            planes = await self._aggregate_graph.aggregate_planes(
                conn, PRESENCE_PLACES, unit=unit, key=(circle_id, place.place_id, unit),
                occurred_at=at, asserted_at=at, minimum="internal",
                persons=persons,
                inputs=(source for source in inputs if source.get("unit") == unit),
                revisions=registration.revisions(),
            )
        values = {
            "circle_id": circle_id, "place_id": place.place_id,
            "name": place.name, "lat": place.lat, "lng": place.lng,
            "radius_m": place.radius_m, "address": place.address,
            "updated_ms": place.updated_ms, **planes,
        }
        await conn.execute(pg_insert(PRESENCE_PLACES).values(**values).on_conflict_do_update(
            constraint="uq_presence_places_unit",
            set_={k: v for k, v in values.items() if k not in {"circle_id", "place_id", "unit"}},
        ))

    async def places_for(self, *, circle_id: str) -> list[SavedPlace]:
        """One circle's saved places, name-ordered for deterministic reads."""
        stmt = (
            sa.select(PRESENCE_PLACES).ext(distinct_on(PRESENCE_PLACES.c.place_id))
            .where(PRESENCE_PLACES.c.circle_id == circle_id, PRESENCE_PLACES.c.state == "live")
            .order_by(PRESENCE_PLACES.c.place_id, PRESENCE_PLACES.c.updated_ms.desc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            SavedPlace(
                circle_id=r.circle_id,
                place_id=r.place_id,
                name=r.name,
                lat=r.lat,
                lng=r.lng,
                radius_m=r.radius_m,
                address=r.address,
                updated_ms=r.updated_ms,
            )
            for r in sorted(rows, key=lambda row: (row.name, row.place_id))
        ]

    async def prune(self, *, now_ms: int) -> int:
        """Delete every fix older than the retention window. Returns the count.

        Time-windowed, not count-windowed: `now_ms - retention_days` in
        milliseconds, computed here so the cutoff is never a hard-coded constant.
        Called by the collector's periodic sweep, never on the write path.
        """
        cutoff = now_ms - self._retention_days * _MS_PER_DAY
        async with self._engine.begin() as conn:
            rows = await tombstone_items(conn, PRESENCE_FIXES, PRESENCE_FIXES.c.ts < cutoff)
        return len(rows)

    async def aclose(self) -> None:
        await self._engine.dispose()


def _select_fix() -> sa.Select[Any]:
    return sa.select(
        PRESENCE_FIXES.c.circle_id,
        PRESENCE_FIXES.c.person_id,
        PRESENCE_FIXES.c.ts,
        PRESENCE_FIXES.c.lat,
        PRESENCE_FIXES.c.lng,
        PRESENCE_FIXES.c.accuracy_m,
        PRESENCE_FIXES.c.is_driving,
        PRESENCE_FIXES.c.battery,
        PRESENCE_FIXES.c.address,
        PRESENCE_FIXES.c.speed_kmh,
        PRESENCE_FIXES.c.wire_state,
        PRESENCE_FIXES.c.recorded_at,
    )


def _row_to_fix(row: Any) -> PresenceFix:
    return PresenceFix(
        circle_id=row.circle_id,
        person_id=row.person_id,
        ts=row.ts,
        lat=row.lat,
        lng=row.lng,
        wire_state=WireState(row.wire_state),
        recorded_at=row.recorded_at,
        accuracy_m=row.accuracy_m,
        is_driving=None if row.is_driving is None else bool(row.is_driving),
        battery=row.battery,
        address=row.address,
        speed_kmh=row.speed_kmh,
    )
