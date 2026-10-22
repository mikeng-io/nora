"""`MobilityStore` over SQLAlchemy Core — the presence Rollup's read/write seam.
A SECOND store in the SAME `nora` database as `PresenceStore`.

**Builds.** Every presence Rollup row carries the build that computed it
(`build_id`, migration 0071). An instance is either BOUND to one build
(`scoped(build_id)`: the analytics process writes and reads its own build
through it) or UNBOUND (`build_id=None`: every read resolves the CURRENT build
through `rollup_build`'s one `current` row, the pointer). Every reader the
agent, the portal and the tools use is an unbound instance, so a rebuild that
switches the pointer moves all of them at once, and a superseded build is
never read again but never deleted. Writing through an unbound instance is a
programming error and raises.

This store does not classify or schedule — that is `nora.domain.mobility`
(pure) and `nora.analytics.rollup` (the fold) above it. It reads the Record
in the fold's canonical order, writes rows, and nothing else.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.mobility import (
    DerivedPlace,
    RawFix,
    Stay,
    TaggedPlace,
    Trip,
    hour_of_week_bins,
)
from nora.domain.rollup import (
    FIX_RANK,
    POLL_RANK,
    Event,
    EventPosition,
    FixEvent,
    FoldState,
    NoRollupBuildError,
    PollEvent,
    StoredCorridor,
    episode_key,
    lineage_json,
    presence_lineage,
    stay_from_json,
    stay_to_json,
)
from nora.infra.sql import memory_index
from nora.infra.sql.schema import (
    PRESENCE_CORRIDORS,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_FIXES,
    PRESENCE_OCCUPANCY,
    PRESENCE_PLACE_RHYTHM,
    PRESENCE_PLACES,
    PRESENCE_STAYS,
    PRESENCE_TRIPS,
    ROLLUP_BUILD,
    ROLLUP_FOLD_STATE,
    SOURCE_HEALTH,
)

# The web consumer is deliberately bounded independently of the rollup's
# batch sizes.  These are the same operational limits as the portal's history
# endpoint: a 90-day dashboard window and at most 5,000 derived rows.
_MAX_ANALYTICS_WINDOW_MS = 90 * 24 * 60 * 60 * 1000
_MAX_ANALYTICS_POINTS = 5_000
_MAX_ANALYTICS_PLACES = 100
_ANALYTICS_TZ_NAME = "Asia/Hong_Kong"

#: A poll row is written within `HEALTH_WRITE_TIMEOUT_SECONDS` (5 s) of the
#: cycle it records (`nora.presence.collector`), so its `at_ms` is never this
#: far behind its `recorded_at`. Only an index hint: the poll read narrows on
#: `(source, at_ms)` with it, and the canonical order is still `recorded_at`.
_POLL_WRITE_LAG_BOUND_MS = 10 * 60 * 1000


def current_build_id() -> sa.ScalarSelect[Any]:
    """The pointer: the one `current` build's id (NULL when there is none,
    and then every unbound read answers empty)."""
    return (
        sa.select(ROLLUP_BUILD.c.id)
        .where(ROLLUP_BUILD.c.status == "current")
        .scalar_subquery()
    )


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


def _place_from_row(r: Any) -> DerivedPlace:
    return DerivedPlace(
        id=r.id, subject_id=r.subject_id, circle_id=r.circle_id,
        lat=r.lat, lng=r.lng, radius_m=r.radius_m, kind=r.kind,
        confidence=r.confidence, label=r.label,
        saved_place_id=r.saved_place_id,
        first_seen_ts=r.first_seen_ts, last_seen_ts=r.last_seen_ts,
    )


def _state_from_row(r: Any) -> FoldState:
    return FoldState(
        position=EventPosition(r.pos_recorded_ms, r.pos_rank, r.pos_id),
        logical_now_ms=r.logical_now_ms, frontier_ts=r.frontier_ts,
        window_ids=tuple(r.window_fix_ids or ()), tags_sha256=r.tags_sha256,
        open_stay=stay_from_json(r.open_stay), late_fixes=r.late_fixes,
    )


def _ms(column: Any) -> Any:
    """A timestamptz as epoch milliseconds (floor), the fold's position unit."""
    return sa.cast(sa.func.floor(sa.extract("epoch", column) * 1000), sa.BigInteger)


def _after(recorded: Any, rank: int, row_id: Any, after: EventPosition) -> Any:
    """`(recorded, rank, id) > after` for a source whose rank is constant."""
    later = recorded > after.recorded_ms
    same = recorded == after.recorded_ms
    if rank > after.rank:
        return sa.or_(later, same)
    if rank < after.rank:
        return later
    return sa.or_(later, sa.and_(same, row_id > after.id))


def _lineage(subject_id: str) -> list[list[str | None]]:
    return lineage_json(presence_lineage(subject_id))


class SqlMobilityStore:
    def __init__(
        self, engine: AsyncEngine, *, metrics: Any = None, build_id: int | None = None,
    ) -> None:
        """`metrics` is INJECTED, never imported — see `PresenceStore`'s
        docstring: `nora.infra` sits below `nora.obs` in the import-linter
        layers, so importing `nora.obs.metrics` here would break that
        boundary. `None` means this store does not count. `build_id` binds
        the instance to one build; None reads the current one."""
        self._engine = engine
        self._metrics = metrics
        self._build_id = build_id

    def scoped(self, build_id: int) -> SqlMobilityStore:
        """The same store bound to one build, for the fold that writes it."""
        return SqlMobilityStore(self._engine, metrics=self._metrics, build_id=build_id)

    def unbound(self) -> SqlMobilityStore:
        """The readers' view: every read resolves the current build."""
        return SqlMobilityStore(self._engine, metrics=self._metrics)

    @property
    def build_id(self) -> int | None:
        return self._build_id

    @staticmethod
    def _in_build(build: int, table: sa.Table) -> Any:
        return table.c.build_id == build

    async def _read_build(self) -> int:
        """The build a read reads: the bound one, else the current one. With
        no current build (a fresh database before the first rebuild switches
        one in) there is NOTHING derived to read, which is not «nothing
        found»: it raises `NoRollupBuildError`, and every reader above this
        store already answers an exception as R-27 `unavailable`."""
        if self._build_id is not None:
            return self._build_id
        async with self._engine.connect() as conn:
            build = (await conn.execute(sa.select(current_build_id()))).scalar_one_or_none()
        if build is None:
            raise NoRollupBuildError("no Rollup build is current yet")
        return int(build)

    def _bound(self) -> int:
        if self._build_id is None:
            raise RuntimeError(
                "a write needs a build: use SqlMobilityStore.scoped(build_id)"
            )
        return self._build_id

    # -- the readers the agent, the tools and the portal use -----------------

    async def places_with_aggregates(
        self, *, subject_id: str, until_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read derived places and their stay aggregates for one subject.

        Aggregates follow `presence_stays.place_id` — the link the rollup
        wrote — not a second containment test, which would count a stay near
        two places at both and a stay at a place whose row had moved.
        SELECT-only; the web consumer never re-runs derivation.
        """
        # The cap keeps the most RECENTLY seen places: places are unbounded
        # per subject (DEC-190), and an id-ascending cut would drop the newest.
        build = await self._read_build()
        places_stmt = (
            sa.select(PRESENCE_DERIVED_PLACES)
            .where(
                PRESENCE_DERIVED_PLACES.c.subject_id == subject_id,
                self._in_build(build, PRESENCE_DERIVED_PLACES),
            )
            .order_by(
                PRESENCE_DERIVED_PLACES.c.last_seen_ts.desc(),
                PRESENCE_DERIVED_PLACES.c.id.asc(),
            )
            .limit(_MAX_ANALYTICS_PLACES)
        )
        if until_ms is not None:
            places_stmt = places_stmt.where(
                PRESENCE_DERIVED_PLACES.c.first_seen_ts <= until_ms,
                PRESENCE_DERIVED_PLACES.c.last_seen_ts <= until_ms,
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
                self._in_build(build, PRESENCE_STAYS),
                PRESENCE_STAYS.c.place_id.is_not(None),
            )
            .group_by(PRESENCE_STAYS.c.place_id)
        )
        if until_ms is not None:
            agg_stmt = agg_stmt.where(PRESENCE_STAYS.c.started_ts <= until_ms,
                                      PRESENCE_STAYS.c.ended_ts <= until_ms)
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

    async def rhythm_bins(self, *, subject_id: str, window_ms: int,
                          until_ms: int | None = None) -> list[dict[str, int]]:
        """Stays inside a trailing window, dwell SPLIT across the local
        hours-of-week each one covers.

        Crediting a stay's WHOLE dwell to the hour it started in would put a
        10-hour overnight stay beginning Monday 22:00 all on «Monday 22:00»
        and none on the small hours. `hour_of_week_bins` is the same split
        Tier 3's occupancy uses, clipped to the window; `stay_count` counts the stays
        that touch a bin."""
        window_ms = min(max(window_ms, 0), _MAX_ANALYTICS_WINDOW_MS)
        if window_ms == 0:
            return []
        now_ms = int(time.time() * 1000) if until_ms is None else until_ms
        cutoff_ms = now_ms - window_ms
        build = await self._read_build()
        stmt = (
            sa.select(PRESENCE_STAYS.c.started_ts, PRESENCE_STAYS.c.ended_ts)
            .where(
                PRESENCE_STAYS.c.subject_id == subject_id,
                self._in_build(build, PRESENCE_STAYS),
                PRESENCE_STAYS.c.ended_ts >= cutoff_ms,
            )
            .order_by(PRESENCE_STAYS.c.started_ts.desc())
            .limit(_MAX_ANALYTICS_POINTS)
        )
        if until_ms is not None:
            stmt = stmt.where(PRESENCE_STAYS.c.started_ts <= until_ms,
                              PRESENCE_STAYS.c.ended_ts <= until_ms)
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

        The `day` consumer's read (DEC-155):
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
        build = await self._read_build()
        stmt = (
            sa.select(
                PRESENCE_STAYS.c.started_ts, PRESENCE_STAYS.c.ended_ts,
                PRESENCE_STAYS.c.lat, PRESENCE_STAYS.c.lng,
                PRESENCE_DERIVED_PLACES.c.kind.label("place_kind"),
                PRESENCE_DERIVED_PLACES.c.label.label("place_label"),
                PRESENCE_DERIVED_PLACES.c.saved_place_id.label("place_saved_id"),
            )
            .select_from(
                PRESENCE_STAYS.outerjoin(
                    PRESENCE_DERIVED_PLACES,
                    PRESENCE_STAYS.c.place_id == PRESENCE_DERIVED_PLACES.c.id,
                )
            )
            .where(
                PRESENCE_STAYS.c.subject_id == subject_id,
                self._in_build(build, PRESENCE_STAYS),
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
                # Whether the label is a household name (Life360 saved) or
                # only the geocoded street of the spot.
                "place_saved": row.place_saved_id is not None,
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
        build = await self._read_build()
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
                self._in_build(build, PRESENCE_TRIPS),
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

    async def stays_for_subject(self, *, subject_id: str) -> list[Stay]:
        build = await self._read_build()
        stmt = (
            sa.select(PRESENCE_STAYS)
            .where(
                PRESENCE_STAYS.c.subject_id == subject_id,
                self._in_build(build, PRESENCE_STAYS),
            )
            .order_by(PRESENCE_STAYS.c.started_ts.asc(), PRESENCE_STAYS.c.id.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            Stay(
                subject_id=r.subject_id, person_id=r.person_id, circle_id=r.circle_id,
                started_ts=r.started_ts, ended_ts=r.ended_ts,
                lat=r.lat, lng=r.lng, radius_m=r.radius_m, fix_count=r.fix_count,
                address=r.address, place_id=r.place_id,
            )
            for r in rows
        ]

    async def trips_for_subject(self, *, subject_id: str) -> list[Trip]:
        """Every trip for the subject in the build, oldest first, WITH its
        road-name sequence."""
        build = await self._read_build()
        stmt = (
            sa.select(PRESENCE_TRIPS)
            .where(
                PRESENCE_TRIPS.c.subject_id == subject_id,
                self._in_build(build, PRESENCE_TRIPS),
            )
            .order_by(PRESENCE_TRIPS.c.started_ts.asc(), PRESENCE_TRIPS.c.id.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_trip_from_row(r) for r in rows]

    async def places_for_subject(self, *, subject_id: str) -> list[DerivedPlace]:
        """Every place for the subject in the build, id-ascending — any
        number of them, two homes included (DEC-190)."""
        build = await self._read_build()
        stmt = (
            sa.select(PRESENCE_DERIVED_PLACES)
            .where(
                PRESENCE_DERIVED_PLACES.c.subject_id == subject_id,
                self._in_build(build, PRESENCE_DERIVED_PLACES),
            )
            .order_by(PRESENCE_DERIVED_PLACES.c.id.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_place_from_row(r) for r in rows]

    async def place_by_id(self, *, place_id: int) -> DerivedPlace | None:
        """One derived place by id, whoever's and whichever build's it is:
        `route` resolves a place a presence read named this turn through it,
        and the CALLER decides, by the place's
        `subject_id`, whether this room may be told it. Not build-scoped on
        purpose: ids are unique across builds, and a place named by a read
        just before a switch still resolves to the row it named."""
        stmt = sa.select(PRESENCE_DERIVED_PLACES).where(
            PRESENCE_DERIVED_PLACES.c.id == place_id,
        )
        async with self._engine.connect() as conn:
            r = (await conn.execute(stmt)).first()
        return None if r is None else _place_from_row(r)

    async def tagged_places(self) -> list[TaggedPlace]:
        """Every Life360 saved place, EVERY circle — duplicate tags merge
        across circles, so the read cannot be circle-scoped."""
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

    async def corridors_for_subject(self, *, subject_id: str,
                                    until_ms: int | None = None) -> list[StoredCorridor]:
        build = await self._read_build()
        stmt = (
            sa.select(PRESENCE_CORRIDORS)
            .where(
                PRESENCE_CORRIDORS.c.subject_id == subject_id,
                self._in_build(build, PRESENCE_CORRIDORS),
            )
            .order_by(PRESENCE_CORRIDORS.c.id.asc())
        )
        if until_ms is not None:
            stmt = stmt.where(PRESENCE_CORRIDORS.c.first_seen_ts <= until_ms,
                              PRESENCE_CORRIDORS.c.last_seen_ts <= until_ms)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            StoredCorridor(
                id=r.id, subject_id=r.subject_id, sequence=tuple(r.sequence),
                sample_count=r.sample_count, first_seen_ts=r.first_seen_ts,
                last_seen_ts=r.last_seen_ts,
            )
            for r in rows
        ]

    # -- the fold -----------------------------------------------------------

    async def fold_state(self, *, subject_id: str) -> FoldState | None:
        stmt = sa.select(ROLLUP_FOLD_STATE).where(
            ROLLUP_FOLD_STATE.c.build_id == self._bound(),
            ROLLUP_FOLD_STATE.c.subject_id == subject_id,
        )
        async with self._engine.connect() as conn:
            r = (await conn.execute(stmt)).first()
        return None if r is None else _state_from_row(r)

    async def events_since(
        self, *, person_ids: Sequence[str], circle_ids: Sequence[str],
        after: EventPosition, through_ms: int, limit: int,
    ) -> list[Event]:
        """This person's Record events strictly after `after` and recorded at
        or before `through_ms`, in the canonical order, at most `limit`:
        their admitted fixes (accuracy ≤ 100 m, `live`) and the presence
        collector's SUCCESSFUL polls of their circles. Each source is read in
        order and cut at `limit`, then the two are merged and cut again, so
        the result is exactly the first `limit` events of the merged
        stream."""
        fix_recorded = PRESENCE_FIXES.c.recorded_at
        fixes_stmt = (
            sa.select(
                PRESENCE_FIXES.c.id, PRESENCE_FIXES.c.ts,
                PRESENCE_FIXES.c.lat, PRESENCE_FIXES.c.lng,
                PRESENCE_FIXES.c.address, PRESENCE_FIXES.c.is_driving,
                fix_recorded.label("recorded_ms"),
            )
            .where(
                PRESENCE_FIXES.c.person_id.in_(list(person_ids)),
                PRESENCE_FIXES.c.accuracy_m <= 100.0,
                PRESENCE_FIXES.c.wire_state == "live",
                fix_recorded <= through_ms,
                _after(fix_recorded, FIX_RANK, PRESENCE_FIXES.c.id, after),
            )
            .order_by(fix_recorded.asc(), PRESENCE_FIXES.c.id.asc())
            .limit(limit)
        )
        poll_recorded = _ms(SOURCE_HEALTH.c.recorded_at)
        polls_stmt = (
            sa.select(
                SOURCE_HEALTH.c.id, SOURCE_HEALTH.c.at_ms,
                poll_recorded.label("recorded_ms"),
            )
            .where(
                SOURCE_HEALTH.c.source == "presence",
                SOURCE_HEALTH.c.event == "poll",
                SOURCE_HEALTH.c.outcome == "ok",
                SOURCE_HEALTH.c.scope.in_(list(circle_ids)),
                SOURCE_HEALTH.c.at_ms >= after.recorded_ms - _POLL_WRITE_LAG_BOUND_MS,
                poll_recorded <= through_ms,
                _after(poll_recorded, POLL_RANK, SOURCE_HEALTH.c.id, after),
            )
            .order_by(poll_recorded.asc(), SOURCE_HEALTH.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            fixes = (await conn.execute(fixes_stmt)).all()
            polls = (await conn.execute(polls_stmt)).all()
        events: list[Event] = [
            FixEvent(
                position=EventPosition(int(r.recorded_ms), FIX_RANK, int(r.id)),
                fix=RawFix(
                    id=r.id, ts=r.ts, lat=r.lat, lng=r.lng, address=r.address,
                    is_driving=bool(r.is_driving) if r.is_driving is not None else None,
                ),
            )
            for r in fixes
        ]
        events.extend(
            PollEvent(
                position=EventPosition(int(r.recorded_ms), POLL_RANK, int(r.id)),
                at_ms=int(r.at_ms),
            )
            for r in polls
        )
        events.sort(key=lambda e: e.position)
        return events[:limit]

    async def fixes_by_id(self, *, ids: Sequence[int]) -> list[RawFix]:
        """The fixes a fold holds undecided, in the order it holds them. A
        fix no longer in the table (retention removed it) is simply absent."""
        if not ids:
            return []
        stmt = sa.select(
            PRESENCE_FIXES.c.id, PRESENCE_FIXES.c.ts, PRESENCE_FIXES.c.lat,
            PRESENCE_FIXES.c.lng, PRESENCE_FIXES.c.address, PRESENCE_FIXES.c.is_driving,
        ).where(PRESENCE_FIXES.c.id.in_(list(ids)))
        async with self._engine.connect() as conn:
            rows = {r.id: r for r in (await conn.execute(stmt)).all()}
        return [
            RawFix(
                id=r.id, ts=r.ts, lat=r.lat, lng=r.lng, address=r.address,
                is_driving=bool(r.is_driving) if r.is_driving is not None else None,
            )
            for i in ids if (r := rows.get(i)) is not None
        ]

    async def commit_fold(
        self, *, subject_id: str, stays: list[Stay], trips: list[Trip],
        state: FoldState,
    ) -> None:
        """The fold's output and where it now stands, in ONE transaction: a
        run that dies before this commits nothing, and the next one folds the
        same events again from the same state, so no row is written twice."""
        build = self._bound()
        lineage = _lineage(subject_id)
        async with self._engine.begin() as conn:
            if stays:
                await conn.execute(sa.insert(PRESENCE_STAYS), [
                    {
                        "subject_id": s.subject_id, "person_id": s.person_id,
                        "circle_id": s.circle_id, "started_ts": s.started_ts,
                        "ended_ts": s.ended_ts, "lat": s.lat, "lng": s.lng,
                        "radius_m": s.radius_m, "fix_count": s.fix_count,
                        "address": s.address, "build_id": build, "lineage": lineage,
                    }
                    for s in stays
                ])
            if trips:
                await conn.execute(sa.insert(PRESENCE_TRIPS), [
                    {
                        "subject_id": t.subject_id, "person_id": t.person_id,
                        "circle_id": t.circle_id, "started_ts": t.started_ts,
                        "ended_ts": t.ended_ts, "distance_m": t.distance_m,
                        "avg_speed_kmh": t.avg_speed_kmh, "fix_count": t.fix_count,
                        # `list(...)`: JSONB takes a JSON array, and a Python
                        # tuple is not one.
                        "sequence": list(t.corridor),
                        "corridor_id": t.corridor_id,
                        "build_id": build, "lineage": lineage,
                    }
                    for t in trips
                ])
            await self._save_state(conn, build=build, subject_id=subject_id, state=state)
            # The memory index's nodes, when this is the current build (a
            # rebuild's folds write a build still `building`, which has none).
            for kind, spans in (("stay", stays), ("trip", trips)):
                await memory_index.project_episodes(
                    conn, build, kind,
                    [episode_key(x.subject_id, x.started_ts, x.ended_ts) for x in spans],
                    metrics=self._metrics,
                )

    async def _save_state(
        self, conn: AsyncConnection, *, build: int, subject_id: str, state: FoldState,
    ) -> None:
        values = {
            "pos_recorded_ms": state.position.recorded_ms,
            "pos_rank": state.position.rank, "pos_id": state.position.id,
            "logical_now_ms": state.logical_now_ms, "frontier_ts": state.frontier_ts,
            "window_fix_ids": list(state.window_ids),
            "tags_sha256": state.tags_sha256,
            "open_stay": stay_to_json(state.open_stay),
            "late_fixes": state.late_fixes,
        }
        stmt = pg_insert(ROLLUP_FOLD_STATE).values(
            build_id=build, subject_id=subject_id, **values,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ROLLUP_FOLD_STATE.c.build_id, ROLLUP_FOLD_STATE.c.subject_id],
            set_=values,
        )
        await conn.execute(stmt)

    async def mark_derived(self, *, subject_id: str, tags_sha256: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(ROLLUP_FOLD_STATE)
                .where(
                    ROLLUP_FOLD_STATE.c.build_id == self._bound(),
                    ROLLUP_FOLD_STATE.c.subject_id == subject_id,
                )
                .values(tags_sha256=tags_sha256)
            )

    async def save_place(
        self, *, subject_id: str, circle_id: str, place_id: int | None,
        lat: float, lng: float, radius_m: float,
        kind: str, confidence: float, label: str | None,
        saved_place_id: str | None, first_seen_ts: int, last_seen_ts: int,
    ) -> int:
        """UPDATE in place when `place_id` is given — the id is what
        `presence_stays.place_id`, `presence_occupancy` and
        `presence_place_rhythm` key off, so it is never churned — else INSERT
        into the bound build. `kind='unlabeled'` is a stored value, never a
        deletion (DEC-190 rule 4): the place is still where the person goes."""
        build = self._bound()
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
                        PRESENCE_DERIVED_PLACES.c.build_id == build,
                    )
                    .values(**values)
                )
                return place_id
            result = await conn.execute(
                sa.insert(PRESENCE_DERIVED_PLACES)
                .values(
                    subject_id=subject_id, circle_id=circle_id, build_id=build,
                    lineage=_lineage(subject_id), **values,
                )
                .returning(PRESENCE_DERIVED_PLACES.c.id)
            )
            return int(result.scalar_one())

    async def delete_places(self, *, subject_id: str, place_ids: list[int]) -> None:
        """Drop places no stay resolves to any more, WITH their occupancy and
        rhythm rows — a Tier 3 row keyed on a vanished place is a ghost no
        consumer can resolve. The caller unlinks the stays first. Deleted
        rows stay in `row_history` (the trigger), so the build as of an
        earlier T still holds them."""
        if not place_ids:
            return
        build = self._bound()
        async with self._engine.begin() as conn:
            for table in (PRESENCE_OCCUPANCY, PRESENCE_PLACE_RHYTHM):
                await conn.execute(
                    sa.delete(table).where(
                        table.c.subject_id == subject_id,
                        table.c.build_id == build,
                        table.c.place_id.in_(place_ids),
                    )
                )
            await conn.execute(
                sa.delete(PRESENCE_DERIVED_PLACES).where(
                    PRESENCE_DERIVED_PLACES.c.subject_id == subject_id,
                    PRESENCE_DERIVED_PLACES.c.build_id == build,
                    PRESENCE_DERIVED_PLACES.c.id.in_(place_ids),
                )
            )

    async def relink_stays(
        self, *, subject_id: str, links: dict[int, int | None],
    ) -> None:
        """Set each stay's `place_id` (keyed by `started_ts`) to EXACTLY the
        given value, overwriting what is there. It is not fill-NULL-only: an
        additive write would leave stays linked to rows that had moved away, and
        a link must follow the place picture the reconcile computed, not
        whichever writer got there first."""
        if not links:
            return
        build = self._bound()
        by_place: dict[int | None, list[int]] = {}
        for started_ts, place_id in links.items():
            by_place.setdefault(place_id, []).append(started_ts)
        async with self._engine.begin() as conn:
            for place_id, started in by_place.items():
                await conn.execute(
                    sa.update(PRESENCE_STAYS)
                    .where(
                        PRESENCE_STAYS.c.subject_id == subject_id,
                        PRESENCE_STAYS.c.build_id == build,
                        PRESENCE_STAYS.c.started_ts.in_(started),
                    )
                    .values(place_id=place_id)
                )

    async def replace_occupancy(
        self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int,
    ) -> None:
        """Occupancy for one place, recomputed WHOLESALE from its linked
        stays: a full replace is idempotent and follows the links.
        `now_ms` is written as `updated_ts`, the Record time the bins are as
        of."""
        build = self._bound()
        scope = (
            PRESENCE_OCCUPANCY.c.subject_id == subject_id,
            PRESENCE_OCCUPANCY.c.build_id == build,
            PRESENCE_OCCUPANCY.c.place_id == place_id,
        )
        async with self._engine.begin() as conn:
            stored = {
                r.hour_of_week: (r.dwell_ms, r.updated_ts)
                for r in (await conn.execute(
                    sa.select(
                        PRESENCE_OCCUPANCY.c.hour_of_week, PRESENCE_OCCUPANCY.c.dwell_ms,
                        PRESENCE_OCCUPANCY.c.updated_ts,
                    ).where(*scope)
                )).all()
            }
            # DIFFED, so an unchanged place writes nothing (and no version in
            # `row_history`): the reconcile rebuilds every kept place.
            gone = sorted(set(stored) - set(bins))
            if gone:
                await conn.execute(
                    sa.delete(PRESENCE_OCCUPANCY).where(
                        *scope, PRESENCE_OCCUPANCY.c.hour_of_week.in_(gone),
                    )
                )
            for how, dwell in sorted(bins.items()):
                if how not in stored:
                    await conn.execute(sa.insert(PRESENCE_OCCUPANCY).values(
                        subject_id=subject_id, place_id=place_id, hour_of_week=how,
                        dwell_ms=dwell, updated_ts=now_ms, build_id=build,
                        lineage=_lineage(subject_id),
                    ))
                elif stored[how] != (dwell, now_ms):
                    await conn.execute(
                        sa.update(PRESENCE_OCCUPANCY)
                        .where(*scope, PRESENCE_OCCUPANCY.c.hour_of_week == how)
                        .values(dwell_ms=dwell, updated_ts=now_ms)
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
            sample_count=sample_count, updated_ts=now_ms, build_id=self._bound(),
            lineage=_lineage(subject_id),
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

    async def save_corridor(
        self, *, subject_id: str, corridor_id: int | None,
        sequence: tuple[str, ...], sample_count: int, first_seen_ts: int,
        last_seen_ts: int,
    ) -> int:
        """UPDATE in place when `corridor_id` is given (the id trips link to),
        else INSERT into the bound build. `first_seen_ts`/`last_seen_ts` are
        the corridor's trips' starts, Record time."""
        build = self._bound()
        values = {
            "sequence": list(sequence), "sample_count": sample_count,
            "first_seen_ts": first_seen_ts, "last_seen_ts": last_seen_ts,
        }
        async with self._engine.begin() as conn:
            if corridor_id is not None:
                await conn.execute(
                    sa.update(PRESENCE_CORRIDORS).where(
                        PRESENCE_CORRIDORS.c.id == corridor_id,
                        PRESENCE_CORRIDORS.c.build_id == build,
                    ).values(**values)
                )
                return corridor_id
            result = await conn.execute(
                sa.insert(PRESENCE_CORRIDORS).values(
                    subject_id=subject_id, build_id=build,
                    lineage=_lineage(subject_id), **values,
                ).returning(PRESENCE_CORRIDORS.c.id)
            )
            return int(result.scalar_one())

    async def delete_corridors(self, *, subject_id: str, corridor_ids: list[int]) -> None:
        if not corridor_ids:
            return
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(PRESENCE_CORRIDORS).where(
                    PRESENCE_CORRIDORS.c.subject_id == subject_id,
                    PRESENCE_CORRIDORS.c.build_id == self._bound(),
                    PRESENCE_CORRIDORS.c.id.in_(corridor_ids),
                )
            )

    async def relink_trips(
        self, *, subject_id: str, links: dict[int, int | None],
    ) -> None:
        """Set each trip's `corridor_id` (keyed by `started_ts`) to exactly
        the given value, the corridor twin of `relink_stays`."""
        if not links:
            return
        build = self._bound()
        by_corridor: dict[int | None, list[int]] = {}
        for started_ts, corridor_id in links.items():
            by_corridor.setdefault(corridor_id, []).append(started_ts)
        async with self._engine.begin() as conn:
            for corridor_id, started in by_corridor.items():
                await conn.execute(
                    sa.update(PRESENCE_TRIPS)
                    .where(
                        PRESENCE_TRIPS.c.subject_id == subject_id,
                        PRESENCE_TRIPS.c.build_id == build,
                        PRESENCE_TRIPS.c.started_ts.in_(started),
                    )
                    .values(corridor_id=corridor_id)
                )

    async def _seed_fix_for_test(self, **kw: Any) -> None:
        """Contract-suite seeding ONLY (tests/contract/…) — this store does
        not write presence_fixes in production; `PresenceStore.record_fix`
        does. Exists so the shared contract suite can seed identical rows
        against BOTH implementations without importing PresenceStore."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(PRESENCE_FIXES), {"circle_id": "c1", **kw})

    async def _seed_poll_for_test(
        self, *, at_ms: int, recorded_ms: int | None = None, scope: str = "c1",
        outcome: str = "ok",
    ) -> None:
        """Contract-suite seeding ONLY — `source_health` is written by the
        collector (`SqlSourceHealthLog`) in production. `recorded_ms`
        defaults to `at_ms`, as a poll row is written right after its cycle."""
        recorded = at_ms if recorded_ms is None else recorded_ms
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(SOURCE_HEALTH), {
                "source": "presence", "scope": scope, "event": "poll",
                "outcome": outcome, "at_ms": at_ms,
                "recorded_at": datetime.fromtimestamp(recorded // 1000, tz=UTC)
                + timedelta(milliseconds=recorded % 1000),
            })

    async def _seed_build_for_test(self, *, current: bool = False) -> int:
        """Contract-suite seeding ONLY — builds are made by
        `nora.infra.sql.rollup_builds` in production. A current build
        supersedes the one that was."""
        async with self._engine.begin() as conn:
            if current:
                await conn.execute(
                    sa.update(ROLLUP_BUILD).where(ROLLUP_BUILD.c.status == "current")
                    .values(status="superseded")
                )
            result = await conn.execute(
                sa.insert(ROLLUP_BUILD).values(
                    code_version="test", params_sha256="",
                    status="current" if current else "building",
                ).returning(ROLLUP_BUILD.c.id)
            )
            return int(result.scalar_one())

    async def _seed_trips_for_test(self, trips: list[Trip]) -> None:
        """Contract-suite seeding ONLY — trips in the bound build as a fold
        would have written them."""
        await self.commit_fold(
            subject_id=trips[0].subject_id if trips else "", stays=[], trips=trips,
            state=FoldState(),
        )

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
