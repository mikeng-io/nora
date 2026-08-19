"""In-memory MobilityStore. Passes tests/contract/test_mobility_store_contract.py."""

from __future__ import annotations

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


class FakeMobilityStore:
    def __init__(self) -> None:
        self._fixes: list[_SeededFix] = []
        self._stays: list[Stay] = []
        self._places: dict[
            tuple[str, str], tuple[int, float, float, float, LabelResult, str | None]
        ] = {}
        self._watermarks: dict[str, tuple[int, int]] = {}
        self._next_place_id = 1

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
            RawFix(id=f.id, ts=f.ts, lat=f.lat, lng=f.lng) for f in matches[:limit]
        ]

    async def write_stays(self, stays: list[Stay]) -> None:
        self._stays.extend(stays)

    async def stays_for_subject(self, *, subject_id: str) -> list[Stay]:
        return sorted(
            (s for s in self._stays if s.subject_id == subject_id),
            key=lambda s: s.started_ts,
        )

    async def existing_place(
        self, *, subject_id: str, kind: str
    ) -> tuple[int, float, float, float, LabelResult, str | None] | None:
        return self._places.get((subject_id, kind))

    async def upsert_place(
        self, *, subject_id: str, circle_id: str, kind: str, lat: float,
        lng: float, radius_m: float, result: LabelResult, now_ms: int,
        label: str | None = None,
    ) -> None:
        # `kind` is the slot being re-evaluated, independent of
        # `result.kind` — see SqlMobilityStore.upsert_place's docstring
        # (behavioral twin, per this project's contract-suite convention).
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
