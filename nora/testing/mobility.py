"""In-memory MobilityStore. Passes tests/contract/test_mobility_store_contract.py."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from nora.domain.mobility import LabelResult, RawFix, Stay


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
        self._places: dict[
            tuple[str, str], tuple[int, float, float, float, LabelResult, str | None]
        ] = {}
        self._watermarks: dict[str, tuple[int, int]] = {}
        self._next_place_id = 1
        self._occupancy: dict[tuple[str, int, int], int] = {}  # (subject, place, how) -> dwell_ms
        self._rhythm: dict[tuple[str, int], dict[str, float | int]] = {}

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
