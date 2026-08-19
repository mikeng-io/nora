"""In-memory MobilityStore. Passes tests/contract/test_mobility_store_contract.py."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from nora.domain.mobility import LabelResult, RawFix, Stay, Trip, edit_distance


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


class FakeMobilityStore:
    def __init__(self) -> None:
        self._fixes: list[_SeededFix] = []
        self._stays: list[Stay] = []
        self._trips: list[Trip] = []
        self._places: dict[
            tuple[str, str], tuple[int, float, float, float, LabelResult, str | None]
        ] = {}
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
            RawFix(id=f.id, ts=f.ts, lat=f.lat, lng=f.lng, address=f.address)
            for f in matches[:limit]
        ]

    async def write_stays(self, stays: list[Stay]) -> None:
        self._stays.extend(stays)

    async def stays_for_subject(self, *, subject_id: str) -> list[Stay]:
        return sorted(
            (s for s in self._stays if s.subject_id == subject_id),
            key=lambda s: s.started_ts,
        )

    async def set_place_ids(
        self, *, subject_id: str, started_ts_in: list[int], place_id: int
    ) -> None:
        targets = set(started_ts_in)
        for i, s in enumerate(self._stays):
            if (
                s.subject_id == subject_id
                and s.started_ts in targets
                and s.place_id is None
            ):
                self._stays[i] = dataclasses.replace(s, place_id=place_id)

    async def write_trips(self, trips: list[Trip]) -> None:
        self._trips.extend(trips)

    async def trips_for_subject(self, *, subject_id: str) -> list[Trip]:
        # Unlike SqlMobilityStore, this naturally preserves `corridor` — it
        # never throws anything away. That is fine and expected: the two
        # implementations are behavioral twins on every METHOD's contract,
        # not on every unobserved field, the same relationship
        # `existing_place`'s `most_recent_support_ts` already has between
        # the two stores.
        return sorted(
            (t for t in self._trips if t.subject_id == subject_id),
            key=lambda t: t.started_ts,
        )

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
        best: tuple[int, int, tuple[str, ...]] | None = None
        for cid, row in self._corridors.items():
            if row["subject_id"] != subject_id:
                continue
            dist = edit_distance(sequence, row["sequence"])
            if dist <= max_edit_distance and (best is None or dist < best[0]):
                best = (dist, cid, row["sequence"])
        return None if best is None else (best[1], best[2])

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

    async def existing_place(
        self, *, subject_id: str, kind: str
    ) -> tuple[int, float, float, float, LabelResult, str | None] | None:
        return self._places.get((subject_id, kind))

    async def upsert_place(
        self, *, subject_id: str, circle_id: str, kind: str, lat: float,
        lng: float, radius_m: float, result: LabelResult, now_ms: int,
        label: str | None = None,
    ) -> None:
        # `kind == result.kind` (steady-state reconfirm — Speed 3 re-labels
        # the SAME physical place every cycle) UPDATEs in place, preserving
        # `place_id` — see SqlMobilityStore.upsert_place's docstring
        # (behavioral twin, per this project's contract-suite convention;
        # Task 10 review, Critical #1: churning the id every cycle orphaned
        # stay/occupancy/rhythm links keyed off it).
        if kind == result.kind and result.kind != "unlabeled":
            existing = self._places.get((subject_id, result.kind))
            if existing is not None:
                place_id = existing[0]
                self._places[(subject_id, result.kind)] = (
                    place_id, lat, lng, radius_m, result, label,
                )
                return
        # `kind` is the slot being re-evaluated, independent of
        # `result.kind` — see SqlMobilityStore.upsert_place's docstring.
        # The delete clears EITHER `kind` OR `result.kind` (when it is a
        # real kind and differs from `kind`) — never just one, never both
        # `home`/`work` unconditionally. Without also clearing `result.kind`,
        # a recheck of `home` that resolves straight to `work` would leave
        # a stale row behind under an already-existing, unrelated `work`
        # place from a different cluster.
        delete_kinds = {kind}
        if result.kind != "unlabeled":
            delete_kinds.add(result.kind)
        for delete_kind in delete_kinds:
            self._places.pop((subject_id, delete_kind), None)
        if result.kind == "unlabeled":
            return
        place_id = self._next_place_id
        self._next_place_id += 1
        self._places[(subject_id, result.kind)] = (
            place_id, lat, lng, radius_m, result, label,
        )

    async def record_occupancy(
        self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int
    ) -> None:
        for how, dwell_ms in bins.items():
            key = (subject_id, place_id, how)
            self._occupancy[key] = self._occupancy.get(key, 0) + dwell_ms

    async def stay_durations_for_place(
        self, *, subject_id: str, place_id: int
    ) -> list[int]:
        return [
            s.ended_ts - s.started_ts for s in self._stays
            if s.subject_id == subject_id and s.place_id == place_id
        ]

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
