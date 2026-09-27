"""In-memory MobilityStore. Passes tests/contract/test_mobility_store_contract.py."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from nora.domain.mobility import (
    DerivedPlace,
    RawFix,
    Stay,
    TaggedPlace,
    Trip,
    best_corridor_match,
)


@dataclass
class _SeededFix:
    id: int
    person_id: str
    ts: int
    lat: float
    lng: float
    accuracy_m: float
    wire_state: str
    recorded_at: int
    address: str | None = None
    is_driving: bool | None = None


class FakeMobilityStore:
    def __init__(self) -> None:
        self._fixes: list[_SeededFix] = []
        self._stays: list[Stay] = []
        self._trips: list[Trip] = []
        self._places: dict[int, DerivedPlace] = {}
        self._tagged: list[TaggedPlace] = []
        self._watermarks: dict[str, tuple[int, int]] = {}
        self._next_place_id = 1
        self._occupancy: dict[tuple[str, int, int], int] = {}  # (subject, place, how) -> dwell_ms
        self._rhythm: dict[tuple[str, int], dict[str, float | int]] = {}
        self._corridors: dict[int, dict[str, Any]] = {}
        self._next_corridor_id = 1
        self._corridor_speed: dict[tuple[str, int, int], dict[str, float | int]] = {}

    async def watermark(self, *, subject_id: str) -> tuple[int, int]:
        return self._watermarks.get(subject_id, (0, 0))

    async def fixes_since(
        self, *, person_ids: list[str], after_fix_id: int, grace_ms: int,
        now_ms: int, limit: int,
    ) -> list[RawFix]:
        cutoff = now_ms - grace_ms
        matches = [
            f for f in self._fixes
            if f.person_id in person_ids
            and f.id > after_fix_id
            and f.accuracy_m <= 100.0
            and f.wire_state == "live"
            and f.recorded_at < cutoff
        ]
        matches.sort(key=lambda f: f.id)
        return [
            RawFix(
                id=f.id, ts=f.ts, lat=f.lat, lng=f.lng, address=f.address,
                is_driving=f.is_driving,
            )
            for f in matches[:limit]
        ]

    async def write_stays(self, stays: list[Stay]) -> None:
        # Behavioral twin of `SqlMobilityStore.write_stays` — including the
        # data it CANNOT persist: `presence_stays` has no address column, so
        # the real store drops `Stay.address` on the roundtrip, and re-reads
        # (`stays_for_subject`) come back with it as `None` (issue #10: the
        # rollup re-attaches this cycle's addresses from its own segmentation
        # output instead of trusting the roundtrip). The fake must drop it
        # the same way or the place-label-fallback tests would pass on the
        # in-memory path (reading cluster members' addresses) while the real
        # store exercises re-attachment — decorative tests, exactly the
        # fail-mode CLAUDE.md's fake-parity and negative-control rules name.
        self._stays.extend(dataclasses.replace(s, address=None) for s in stays)

    async def stays_for_subject(self, *, subject_id: str) -> list[Stay]:
        return sorted(
            (s for s in self._stays if s.subject_id == subject_id),
            key=lambda s: s.started_ts,
        )

    async def write_trips(self, trips: list[Trip]) -> None:
        self._trips.extend(trips)

    async def trips_for_subject(self, *, subject_id: str) -> list[Trip]:
        # `corridor` survives the round trip in BOTH stores as of migration
        # 0020 (`presence_trips.sequence`) — see
        # `SqlMobilityStore.trips_for_subject`'s docstring for why the old
        # "the SQL side returns () on purpose" divergence is gone.
        return sorted(
            (t for t in self._trips if t.subject_id == subject_id),
            key=lambda t: t.started_ts,
        )

    async def unclustered_named_trips_for_subject(
        self, *, subject_id: str, limit: int
    ) -> list[Trip]:
        # Behavioral twin of `SqlMobilityStore
        # .unclustered_named_trips_for_subject` — see that docstring for
        # why `corridor_id IS NULL` is the watermark, why the empty-
        # sequence exclusion lives in the query rather than in the caller,
        # and why the cap drops the OLDEST trips. `if t.corridor` is the
        # in-memory spelling of `sequence IS NOT NULL AND
        # jsonb_array_length(sequence) > 0`: this store cannot represent a
        # NULL sequence at all, so an empty tuple covers both cases.
        return sorted(
            (
                t for t in self._trips
                if t.subject_id == subject_id
                and t.corridor_id is None
                and t.corridor
            ),
            key=lambda t: t.started_ts,
            reverse=True,
        )[:limit]

    async def set_trip_corridor_ids(
        self, *, subject_id: str, started_ts_in: list[int], corridor_id: int
    ) -> None:
        targets = set(started_ts_in)
        for i, t in enumerate(self._trips):
            if (
                t.subject_id == subject_id
                and t.started_ts in targets
                and t.corridor_id is None
            ):
                self._trips[i] = dataclasses.replace(t, corridor_id=corridor_id)

    async def existing_corridor(
        self, *, subject_id: str, sequence: tuple[str, ...], max_edit_distance: int,
    ) -> tuple[int, tuple[str, ...]] | None:
        # The DECISION is `nora.domain.mobility.best_corridor_match`, the
        # SAME function `SqlMobilityStore.existing_corridor` calls: a
        # distance tie breaks on the LOWEST corridor id, by rule, in one
        # place. Each store used to carry its own copy of this loop and the
        # two copies resolved ties differently — dict insertion order here,
        # Postgres heap order there (final whole-branch review, Important
        # #3).
        return best_corridor_match(
            (
                (cid, row["sequence"]) for cid, row in self._corridors.items()
                if row["subject_id"] == subject_id
            ),
            sequence=sequence, max_edit_distance=max_edit_distance,
        )

    async def upsert_corridor(
        self, *, subject_id: str, sequence: tuple[str, ...], sample_count: int,
        now_ms: int, existing_id: int | None,
    ) -> int:
        if existing_id is not None:
            self._corridors[existing_id]["sample_count"] = sample_count
            self._corridors[existing_id]["last_seen_ts"] = now_ms
            return existing_id
        cid = self._next_corridor_id
        self._next_corridor_id += 1
        self._corridors[cid] = {
            "subject_id": subject_id, "sequence": sequence,
            "sample_count": sample_count, "first_seen_ts": now_ms,
            "last_seen_ts": now_ms,
        }
        return cid

    async def trip_speeds_for_corridor(
        self, *, subject_id: str, corridor_id: int,
    ) -> list[tuple[int, float]]:
        return [
            (t.started_ts, t.avg_speed_kmh) for t in self._trips
            if t.subject_id == subject_id and t.corridor_id == corridor_id
        ]

    async def upsert_corridor_speed(
        self, *, subject_id: str, corridor_id: int, hour_of_week: int,
        p50_kmh: float, p90_kmh: float, sample_count: int, now_ms: int,
    ) -> None:
        self._corridor_speed[(subject_id, corridor_id, hour_of_week)] = {
            "p50_kmh": p50_kmh, "p90_kmh": p90_kmh, "sample_count": sample_count,
        }

    async def places_for_subject(self, *, subject_id: str) -> list[DerivedPlace]:
        return [p for pid, p in sorted(self._places.items()) if p.subject_id == subject_id]

    async def save_place(
        self, *, subject_id: str, circle_id: str, place_id: int | None,
        lat: float, lng: float, radius_m: float,
        kind: str, confidence: float, label: str | None,
        saved_place_id: str | None, first_seen_ts: int, last_seen_ts: int,
    ) -> int:
        # Behavioral twin of `SqlMobilityStore.save_place`: an id given is an
        # UPDATE that keeps it; `unlabeled` is stored, never a deletion.
        if place_id is None:
            place_id = self._next_place_id
            self._next_place_id += 1
        elif (
            place_id not in self._places
            or self._places[place_id].subject_id != subject_id
        ):
            return place_id  # the SQL UPDATE is scoped by subject: no row, no write
        else:
            circle_id = self._places[place_id].circle_id
        self._places[place_id] = DerivedPlace(
            id=place_id, subject_id=subject_id, circle_id=circle_id,
            lat=lat, lng=lng, radius_m=radius_m,
            kind=kind,  # type: ignore[arg-type]
            confidence=confidence, label=label, saved_place_id=saved_place_id,
            first_seen_ts=first_seen_ts, last_seen_ts=last_seen_ts,
        )
        return place_id

    async def delete_places(self, *, subject_id: str, place_ids: list[int]) -> None:
        doomed = set(place_ids)
        for pid in doomed:
            if pid in self._places and self._places[pid].subject_id == subject_id:
                del self._places[pid]
        self._occupancy = {
            k: v for k, v in self._occupancy.items()
            if not (k[0] == subject_id and k[1] in doomed)
        }
        self._rhythm = {
            k: v for k, v in self._rhythm.items()
            if not (k[0] == subject_id and k[1] in doomed)
        }

    async def relink_stays(
        self, *, subject_id: str, links: dict[int, int | None],
    ) -> None:
        for i, s in enumerate(self._stays):
            if s.subject_id == subject_id and s.started_ts in links:
                self._stays[i] = dataclasses.replace(s, place_id=links[s.started_ts])

    async def tagged_places(self) -> list[TaggedPlace]:
        return sorted(self._tagged, key=lambda t: (t.circle_id, t.place_id))

    async def replace_occupancy(
        self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int,
    ) -> None:
        self._occupancy = {
            k: v for k, v in self._occupancy.items()
            if not (k[0] == subject_id and k[1] == place_id)
        }
        for how, dwell_ms in bins.items():
            self._occupancy[(subject_id, place_id, how)] = dwell_ms

    async def _seed_tagged_place_for_test(self, tag: TaggedPlace) -> None:
        self._tagged.append(tag)

    async def upsert_rhythm(
        self, *, subject_id: str, place_id: int, mean_arrival_minute: float,
        mean_departure_minute: float, duration_median_ms: int,
        duration_mad_ms: int, sample_count: int, now_ms: int,
    ) -> None:
        self._rhythm[(subject_id, place_id)] = {
            "mean_arrival_minute": mean_arrival_minute,
            "mean_departure_minute": mean_departure_minute,
            "duration_median_ms": duration_median_ms,
            "duration_mad_ms": duration_mad_ms,
            "sample_count": sample_count,
        }

    async def advance_fix_watermark(self, *, subject_id: str, fix_id: int) -> None:
        _, cluster = self._watermarks.get(subject_id, (0, 0))
        self._watermarks[subject_id] = (fix_id, cluster)

    async def advance_cluster_watermark(self, *, subject_id: str, fix_id: int) -> None:
        fix, _ = self._watermarks.get(subject_id, (0, 0))
        self._watermarks[subject_id] = (fix, fix_id)

    async def _seed_fix_for_test(self, **kw: Any) -> None:
        self._fixes.append(_SeededFix(**{k: v for k, v in kw.items() if k != "circle_id"}))

    async def aclose(self) -> None:
        return None
