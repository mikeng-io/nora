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
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncEngine

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
    stay_from_json,
)
from nora.infra.sql.aggregate_writes import AggregateWrites
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.item_registration import SqlItemRegistration
from nora.infra.sql.mobility_writes import MobilityWrites
from nora.infra.sql.schema import (
    PRESENCE_CORRIDORS,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_FIXES,
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
        lat=r.lat, lng=r.lng, radius_m=r.radius_m, kind=r.source_kind,
        confidence=r.place_confidence, label=r.label,
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





class SqlMobilityStore(MobilityWrites):
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        metrics: Any = None,
        build_id: int | None = None,
        registration: SqlItemRegistration | None = None,
    ) -> None:
        self._engine = engine
        self._metrics = metrics
        self._build_id = build_id
        self._aggregates = AggregateWrites(SqlCognitiveGraph(engine), registration)

    def scoped(self, build_id: int) -> SqlMobilityStore:
        """Bind a writer to one build."""
        return SqlMobilityStore(
            self._engine,
            metrics=self._metrics,
            build_id=build_id,
            registration=self._aggregates.registration,
        )

    def unbound(self) -> SqlMobilityStore:
        """Resolve the current build on each read."""
        return SqlMobilityStore(
            self._engine,
            metrics=self._metrics,
            registration=self._aggregates.registration,
        )

    @property
    def build_id(self) -> int | None:
        return self._build_id

    @staticmethod
    def _in_build(build: int, table: sa.Table) -> Any:
        scope = table.alias("scope_copy")
        canonical = (
            sa.select(sa.func.min(scope.c.unit))
            .where(
                scope.c.build_id == build,
                scope.c.subject_id == table.c.subject_id,
                scope.c.state == "live",
            )
            .correlate(table)
            .scalar_subquery()
        )
        return sa.and_(
            table.c.build_id == build,
            table.c.state == "live",
            table.c.unit.is_not_distinct_from(canonical),
        )

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
            )
        agg_stmt = (
            sa.select(
                PRESENCE_STAYS.c.place_id,
                sa.func.count().label("stay_count"),
                sa.func.max(PRESENCE_STAYS.c.ended_ts).label("last_seen_ts"),
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
            eligible = agg_stmt.subquery()
            places_stmt = (places_stmt.join(eligible,
                eligible.c.place_id == PRESENCE_DERIVED_PLACES.c.id).order_by(None)
                .order_by(eligible.c.last_seen_ts.desc(), PRESENCE_DERIVED_PLACES.c.id.asc()))
        async with self._engine.connect() as conn:
            places = (await conn.execute(places_stmt)).all()
            aggs = {r.place_id: r for r in (await conn.execute(agg_stmt)).all()}

        out: list[dict[str, Any]] = []
        for place in sorted(places, key=lambda p: p.id):
            agg = aggs.get(place.id)
            out.append(
                {
                    "id": place.id,
                    "kind": place.source_kind,
                    "label": place.label,
                    "saved": place.saved_place_id is not None,
                    "lat": place.lat,
                    "lng": place.lng,
                    "radius_m": place.radius_m,
                    "confidence": place.place_confidence,
                    "first_seen_ts": place.first_seen_ts,
                    "last_seen_ts": (agg.last_seen_ts if agg is not None and until_ms is not None
                                     else place.last_seen_ts),
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
        window_ms = min(max(window_ms, 0), 180 * 86400000)
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
                PRESENCE_DERIVED_PLACES.c.source_kind.label("place_kind"),
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
        places = PRESENCE_PLACES.c
        stmt = (
            sa.select(PRESENCE_PLACES).ext(distinct_on(places.circle_id, places.place_id))
            .where(places.state == "live")
            .order_by(places.circle_id, places.place_id, places.updated_ms.desc())
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
            samples = (sa.select(
                PRESENCE_TRIPS.c.corridor_id,
                sa.func.count().label("sample_count"),
                sa.func.min(PRESENCE_TRIPS.c.started_ts).label("first_seen_ts"),
                sa.func.max(PRESENCE_TRIPS.c.ended_ts).label("last_seen_ts"),
            ).where(
                PRESENCE_TRIPS.c.subject_id == subject_id,
                self._in_build(build, PRESENCE_TRIPS),
                PRESENCE_TRIPS.c.started_ts <= until_ms,
                PRESENCE_TRIPS.c.ended_ts <= until_ms,
            ).group_by(PRESENCE_TRIPS.c.corridor_id).subquery())
            stmt = (sa.select(PRESENCE_CORRIDORS.c.id, PRESENCE_CORRIDORS.c.subject_id,
                PRESENCE_CORRIDORS.c.sequence, samples.c.sample_count,
                samples.c.first_seen_ts, samples.c.last_seen_ts)
                .join(samples, samples.c.corridor_id == PRESENCE_CORRIDORS.c.id)
                .where(PRESENCE_CORRIDORS.c.subject_id == subject_id,
                       self._in_build(build, PRESENCE_CORRIDORS),
                       PRESENCE_CORRIDORS.c.first_seen_ts <= until_ms)
                .order_by(PRESENCE_CORRIDORS.c.id.asc()))
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
        fix = PRESENCE_FIXES.c
        fix_recorded = fix.recorded_at
        first_fix = PRESENCE_FIXES.alias("original_fix").c
        copied_fix = sa.exists().where(
            first_fix.circle_id == fix.circle_id, first_fix.person_id == fix.person_id,
            first_fix.ts == fix.ts, first_fix.id < fix.id,
        )
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
                fix_recorded <= through_ms, ~copied_fix,
                _after(fix_recorded, FIX_RANK, PRESENCE_FIXES.c.id, after),
            )
            .order_by(fix_recorded.asc(), PRESENCE_FIXES.c.id.asc())
            .limit(limit)
        )
        poll = SOURCE_HEALTH.c
        poll_recorded = _ms(poll.recorded_at)
        first_poll = SOURCE_HEALTH.alias("original_poll").c
        copied_poll = sa.exists().where(
            first_poll.source == poll.source, first_poll.scope == poll.scope,
            first_poll.at_ms == poll.at_ms, first_poll.recorded_at == poll.recorded_at,
            first_poll.event == poll.event, first_poll.outcome == poll.outcome,
            first_poll.unit < poll.unit,
        )
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
                poll_recorded <= through_ms, ~copied_poll,
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
