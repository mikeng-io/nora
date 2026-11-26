"""Persist and retire the aggregate rows produced by mobility folds."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.mobility import Stay, Trip
from nora.domain.rollup import FoldState, lineage_json, presence_lineage, stay_to_json
from nora.infra.sql.aggregate_writes import AggregateWrites
from nora.infra.sql.cognitive_graph_store import RowData
from nora.infra.sql.schema import (
    PRESENCE_CORRIDORS,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_OCCUPANCY,
    PRESENCE_PLACE_RHYTHM,
    PRESENCE_STAYS,
    PRESENCE_TRIPS,
    ROLLUP_FOLD_STATE,
)


def _lineage(subject_id: str) -> list[list[str | None]]:
    return lineage_json(presence_lineage(subject_id))


class MobilityWrites:
    """Write the outputs of a mobility fold in its bound build."""

    _engine: AsyncEngine
    _metrics: Any
    _build_id: int | None
    _aggregates: AggregateWrites

    def _bound(self) -> int:
        if self._build_id is None:
            raise RuntimeError(
                "a write needs a build: use SqlMobilityStore.scoped(build_id)"
            )
        return self._build_id


    async def commit_fold(
        self,
        *,
        subject_id: str,
        stays: list[Stay],
        trips: list[Trip],
        state: FoldState,
    ) -> None:
        """The fold's output and where it now stands, in ONE transaction: a
        run that dies before this commits nothing, and the next one folds the
        same events again from the same state, so no row is written twice."""
        build = self._bound()
        lineage = _lineage(subject_id)
        async with self._engine.begin() as conn:
            for stay in stays:
                await self._aggregates.store_presence(
                    conn,
                    PRESENCE_STAYS,
                    {
                        "subject_id": stay.subject_id,
                        "person_id": stay.person_id,
                        "circle_id": stay.circle_id,
                        "started_ts": stay.started_ts,
                        "ended_ts": stay.ended_ts,
                        "lat": stay.lat,
                        "lng": stay.lng,
                        "radius_m": stay.radius_m,
                        "fix_count": stay.fix_count,
                        "address": stay.address,
                        "build_id": build,
                        "build_lineage": lineage,
                    },
                )
            for trip in trips:
                await self._aggregates.store_presence(
                    conn,
                    PRESENCE_TRIPS,
                    {
                        "subject_id": trip.subject_id,
                        "person_id": trip.person_id,
                        "circle_id": trip.circle_id,
                        "started_ts": trip.started_ts,
                        "ended_ts": trip.ended_ts,
                        "distance_m": trip.distance_m,
                        "avg_speed_kmh": trip.avg_speed_kmh,
                        "fix_count": trip.fix_count,
                        "sequence": list(trip.corridor),
                        "corridor_id": trip.corridor_id,
                        "build_id": build,
                        "build_lineage": lineage,
                    },
                )
            await self._save_state(conn, build=build, subject_id=subject_id, state=state)

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
        self,
        *,
        subject_id: str,
        circle_id: str,
        place_id: int | None,
        lat: float,
        lng: float,
        radius_m: float,
        kind: str,
        confidence: float,
        label: str | None,
        saved_place_id: str | None,
        first_seen_ts: int,
        last_seen_ts: int,
    ) -> int:
        """UPDATE in place when `place_id` is given — the id is what
        `presence_stays.place_id`, `presence_occupancy` and
        `presence_place_rhythm` key off, so it is never churned — else INSERT
        into the bound build. `kind='unlabeled'` is a stored value, never a
        deletion under the keep-where-they-go rule: the place is still where the person goes."""
        build = self._bound()
        values = {
            "lat": lat,
            "lng": lng,
            "radius_m": radius_m,
            "source_kind": kind,
            "place_confidence": confidence,
            "label": label,
            "saved_place_id": saved_place_id,
            "first_seen_ts": first_seen_ts,
            "last_seen_ts": last_seen_ts,
        }
        return await self._save_identity(
            PRESENCE_DERIVED_PLACES,
            place_id,
            {
                **values,
                "subject_id": subject_id,
                "circle_id": circle_id,
                "build_id": build,
                "build_lineage": _lineage(subject_id),
            },
        )

    async def _save_identity(
        self,
        table: sa.Table,
        row_id: int | None,
        values: dict[str, Any],
    ) -> int:
        async with self._engine.begin() as conn:
            prior = None
            if row_id is not None:
                prior = (
                    (
                        await conn.execute(
                            sa.select(table)
                            .where(
                                table.c.id == row_id,
                                table.c.build_id == self._bound(),
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
            picked = await self._aggregates.store_presence(conn, table, values, old=prior)
            if picked is None:
                raise AssertionError("presence aggregate identity was not written")
            return picked

    async def delete_places(self, *, subject_id: str, place_ids: list[int]) -> None:
        if not place_ids:
            return
        build = self._bound()
        async with self._engine.begin() as conn:
            ids = {
                row["id"]
                for place_id in place_ids
                for row in await self._aggregates.span_copies(
                    conn, PRESENCE_DERIVED_PLACES, place_id
                )
            }
            for table in (PRESENCE_OCCUPANCY, PRESENCE_PLACE_RHYTHM):
                await self._aggregates.discard_rows(
                    conn,
                    table,
                    table.c.subject_id == subject_id,
                    table.c.build_id == build,
                    table.c.place_id.in_(ids),
                )
            await self._aggregates.discard_rows(
                conn,
                PRESENCE_DERIVED_PLACES,
                PRESENCE_DERIVED_PLACES.c.id.in_(ids),
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
            stays = PRESENCE_STAYS.c
            for place_id, started in by_place.items():
                target = sa.update(PRESENCE_STAYS).where(
                    stays.subject_id == subject_id, stays.build_id == build,
                    stays.state == "live", stays.started_ts.in_(started),
                )
                copies = await self._aggregates.span_copies(
                    conn, PRESENCE_DERIVED_PLACES, place_id,
                ) if place_id is not None else []
                if not copies:
                    await conn.execute(target.values(place_id=place_id))
                for place in copies:
                    await conn.execute(target.where(
                        stays.unit.is_not_distinct_from(place["unit"]),
                    ).values(place_id=place["id"]))

    async def replace_occupancy(
        self,
        *,
        subject_id: str,
        place_id: int,
        bins: dict[int, int],
        now_ms: int,
    ) -> None:
        """Replace the covered buckets of a place in each observing unit."""
        build = self._bound()
        async with self._engine.begin() as conn:
            copies = await self._aggregates.span_copies(conn, PRESENCE_DERIVED_PLACES, place_id)
            for place in copies or [{"id": place_id, "unit": None}]:
                table = PRESENCE_OCCUPANCY
                where = (
                    table.c.subject_id == subject_id,
                    table.c.build_id == build,
                    table.c.place_id == place["id"],
                )
                await self._aggregates.discard_rows(
                    conn,
                    table,
                    *where,
                    table.c.hour_of_week.not_in(bins),
                )
                for hour, dwell in sorted(bins.items()):
                    await self._bucket(
                        conn,
                        table,
                        place,
                        {
                            "subject_id": subject_id,
                            "place_id": place["id"],
                            "hour_of_week": hour,
                            "dwell_ms": dwell,
                            "updated_ts": now_ms,
                            "build_id": build,
                            "build_lineage": _lineage(subject_id),
                        },
                        "uq_occupancy_bin",
                    )

    async def upsert_rhythm(
        self,
        *,
        subject_id: str,
        place_id: int,
        mean_arrival_minute: float,
        mean_departure_minute: float,
        duration_median_ms: int,
        duration_mad_ms: int,
        sample_count: int,
        now_ms: int,
    ) -> None:
        async with self._engine.begin() as conn:
            copies = await self._aggregates.span_copies(conn, PRESENCE_DERIVED_PLACES, place_id)
            for place in copies or [{"id": place_id, "unit": None}]:
                await self._bucket(
                    conn,
                    PRESENCE_PLACE_RHYTHM,
                    place,
                    {
                        "subject_id": subject_id,
                        "place_id": place["id"],
                        "mean_arrival_minute": mean_arrival_minute,
                        "mean_departure_minute": mean_departure_minute,
                        "duration_median_ms": duration_median_ms,
                        "duration_mad_ms": duration_mad_ms,
                        "sample_count": sample_count,
                        "updated_ts": now_ms,
                        "build_id": self._bound(),
                        "build_lineage": _lineage(subject_id),
                    },
                    "presence_place_rhythm_pkey",
                )

    async def _bucket(
        self,
        conn: AsyncConnection,
        table: sa.Table,
        place: RowData,
        values: dict[str, Any],
        conflict: str,
    ) -> None:
        reg = self._aggregates.registration
        if reg is not None:
            values.update(
                await self._aggregates.presence_planes(
                    conn,
                    table,
                    values,
                    unit=place["unit"],
                )
            )
        if table is PRESENCE_OCCUPANCY:
            stmt = pg_insert(PRESENCE_OCCUPANCY).values(values)
        else:
            stmt = pg_insert(PRESENCE_PLACE_RHYTHM).values(values)
        update = {k: stmt.excluded[k] for k in values if k != "recorded_at"}
        if "asserted_at" in update:
            update["asserted_at"] = table.c.recorded_at
        await conn.execute(stmt.on_conflict_do_update(constraint=conflict, set_=update))

    async def save_corridor(
        self,
        *,
        subject_id: str,
        corridor_id: int | None,
        sequence: tuple[str, ...],
        sample_count: int,
        first_seen_ts: int,
        last_seen_ts: int,
    ) -> int:
        """UPDATE in place when `corridor_id` is given (the id trips link to),
        else INSERT into the bound build. `first_seen_ts`/`last_seen_ts` are
        the corridor's trips' starts, Record time."""
        build = self._bound()
        values = {
            "sequence": list(sequence),
            "sample_count": sample_count,
            "first_seen_ts": first_seen_ts,
            "last_seen_ts": last_seen_ts,
        }
        return await self._save_identity(
            PRESENCE_CORRIDORS,
            corridor_id,
            {
                **values,
                "subject_id": subject_id,
                "build_id": build,
                "build_lineage": _lineage(subject_id),
            },
        )

    async def delete_corridors(self, *, subject_id: str, corridor_ids: list[int]) -> None:
        if not corridor_ids:
            return
        async with self._engine.begin() as conn:
            table = PRESENCE_CORRIDORS
            selected = sa.select(table.c.sequence).where(table.c.id.in_(corridor_ids))
            await self._aggregates.discard_rows(
                conn,
                table,
                table.c.subject_id == subject_id,
                table.c.build_id == self._bound(),
                table.c.sequence.in_(selected),
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
            trips = PRESENCE_TRIPS.c
            for corridor_id, started in by_corridor.items():
                target = sa.update(PRESENCE_TRIPS).where(
                    trips.subject_id == subject_id, trips.build_id == build,
                    trips.state == "live", trips.started_ts.in_(started),
                )
                copies = await self._aggregates.span_copies(
                    conn, PRESENCE_CORRIDORS, corridor_id,
                ) if corridor_id is not None else []
                if not copies:
                    await conn.execute(target.values(corridor_id=corridor_id))
                for corridor in copies:
                    await conn.execute(target.where(
                        trips.unit.is_not_distinct_from(corridor["unit"]),
                    ).values(corridor_id=corridor["id"]))
