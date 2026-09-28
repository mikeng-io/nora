"""In-memory MobilityStore. Passes tests/contract/test_mobility_store_contract.py.

The behavioural twin of `nora.infra.sql.mobility.SqlMobilityStore`, builds
included: every row carries the build that wrote it, an instance is bound to
one build (`scoped`) or reads the current one, and the Record events come back
in the same canonical order (`EventPosition`) with the same filters.

A fresh `FakeMobilityStore()` is bound to build 1, which is current — what a
unit test of the fold wants without a build manager in the way.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from nora.domain.mobility import (
    DerivedPlace,
    RawFix,
    Stay,
    TaggedPlace,
    Trip,
)
from nora.domain.rollup import (
    FIX_RANK,
    POLL_RANK,
    Event,
    EventPosition,
    FixEvent,
    FoldState,
    PollEvent,
    StoredCorridor,
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


@dataclass
class _SeededPoll:
    id: int
    scope: str
    outcome: str
    at_ms: int
    recorded_ms: int


@dataclass
class _Tables:
    """The rows, shared by every view of one fake database."""

    fixes: list[_SeededFix] = field(default_factory=list)
    polls: list[_SeededPoll] = field(default_factory=list)
    stays: list[tuple[int, Stay]] = field(default_factory=list)
    trips: list[tuple[int, Trip]] = field(default_factory=list)
    places: dict[int, tuple[int, DerivedPlace]] = field(default_factory=dict)
    tagged: list[TaggedPlace] = field(default_factory=list)
    folds: dict[tuple[int, str], FoldState] = field(default_factory=dict)
    occupancy: dict[tuple[int, str, int, int], int] = field(default_factory=dict)
    rhythm: dict[tuple[str, int], tuple[int, dict[str, float | int]]] = field(
        default_factory=dict,
    )
    corridors: dict[int, tuple[int, StoredCorridor]] = field(default_factory=dict)
    next_place_id: int = 1
    next_corridor_id: int = 1
    next_poll_id: int = 1
    next_build: int = 2
    current_build: int | None = 1


def _after(position: EventPosition, after: EventPosition) -> bool:
    return position > after


class FakeMobilityStore:
    def __init__(self, *, build_id: int | None = 1, _tables: _Tables | None = None) -> None:
        self._t = _tables if _tables is not None else _Tables()
        self._build_id = build_id

    def scoped(self, build_id: int) -> FakeMobilityStore:
        return FakeMobilityStore(build_id=build_id, _tables=self._t)

    def unbound(self) -> FakeMobilityStore:
        """The readers' view: every read resolves the current build."""
        return FakeMobilityStore(build_id=None, _tables=self._t)

    def set_current_build(self, build_id: int | None) -> None:
        self._t.current_build = build_id

    @property
    def build_id(self) -> int | None:
        return self._build_id

    def _reading(self) -> int | None:
        return self._build_id if self._build_id is not None else self._t.current_build

    def _bound(self) -> int:
        if self._build_id is None:
            raise RuntimeError("a write needs a build: use FakeMobilityStore.scoped(build_id)")
        return self._build_id

    # -- readers --------------------------------------------------------------

    async def stays_for_subject(self, *, subject_id: str) -> list[Stay]:
        build = self._reading()
        return sorted(
            (s for b, s in self._t.stays if b == build and s.subject_id == subject_id),
            key=lambda s: s.started_ts,
        )

    async def trips_for_subject(self, *, subject_id: str) -> list[Trip]:
        build = self._reading()
        return sorted(
            (t for b, t in self._t.trips if b == build and t.subject_id == subject_id),
            key=lambda t: t.started_ts,
        )

    async def places_for_subject(self, *, subject_id: str) -> list[DerivedPlace]:
        build = self._reading()
        return [
            p for _pid, (b, p) in sorted(self._t.places.items())
            if b == build and p.subject_id == subject_id
        ]

    async def place_by_id(self, *, place_id: int) -> DerivedPlace | None:
        row = self._t.places.get(place_id)
        return None if row is None else row[1]

    async def tagged_places(self) -> list[TaggedPlace]:
        return sorted(self._t.tagged, key=lambda t: (t.circle_id, t.place_id))

    async def corridors_for_subject(self, *, subject_id: str) -> list[StoredCorridor]:
        build = self._reading()
        return [
            c for _cid, (b, c) in sorted(self._t.corridors.items())
            if b == build and c.subject_id == subject_id
        ]

    def occupancy_for(self, *, subject_id: str, place_id: int) -> dict[int, int]:
        """Test read-back of one place's bins in the bound (or current) build."""
        build = self._reading()
        return {
            how: dwell for (b, s, p, how), dwell in self._t.occupancy.items()
            if b == build and s == subject_id and p == place_id
        }

    def rhythm_for(self, *, subject_id: str, place_id: int) -> dict[str, float | int] | None:
        row = self._t.rhythm.get((subject_id, place_id))
        return None if row is None or row[0] != self._reading() else row[1]

    # -- the fold ---------------------------------------------------------------

    async def fold_state(self, *, subject_id: str) -> FoldState | None:
        return self._t.folds.get((self._bound(), subject_id))

    async def events_since(
        self, *, person_ids: Sequence[str], circle_ids: Sequence[str],
        after: EventPosition, through_ms: int, limit: int,
    ) -> list[Event]:
        events: list[Event] = [
            FixEvent(
                position=EventPosition(f.recorded_at, FIX_RANK, f.id),
                fix=RawFix(
                    id=f.id, ts=f.ts, lat=f.lat, lng=f.lng, address=f.address,
                    is_driving=f.is_driving,
                ),
            )
            for f in self._t.fixes
            if f.person_id in person_ids and f.accuracy_m <= 100.0
            and f.wire_state == "live" and f.recorded_at <= through_ms
            and _after(EventPosition(f.recorded_at, FIX_RANK, f.id), after)
        ]
        events.extend(
            PollEvent(position=EventPosition(p.recorded_ms, POLL_RANK, p.id), at_ms=p.at_ms)
            for p in self._t.polls
            if p.scope in circle_ids and p.outcome == "ok"
            and p.recorded_ms <= through_ms
            and _after(EventPosition(p.recorded_ms, POLL_RANK, p.id), after)
        )
        events.sort(key=lambda e: e.position)
        return events[:limit]

    async def fixes_by_id(self, *, ids: Sequence[int]) -> list[RawFix]:
        by_id = {f.id: f for f in self._t.fixes}
        return [
            RawFix(
                id=f.id, ts=f.ts, lat=f.lat, lng=f.lng, address=f.address,
                is_driving=f.is_driving,
            )
            for i in ids if (f := by_id.get(i)) is not None
        ]

    async def commit_fold(
        self, *, subject_id: str, stays: list[Stay], trips: list[Trip],
        state: FoldState,
    ) -> None:
        build = self._bound()
        # `place_id`/`corridor_id` start unset, as the SQL insert leaves them.
        self._t.stays.extend((build, dataclasses.replace(s, place_id=None)) for s in stays)
        self._t.trips.extend((build, dataclasses.replace(t, corridor_id=None)) for t in trips)
        self._t.folds[(build, subject_id)] = state

    async def mark_derived(self, *, subject_id: str, tags_sha256: str) -> None:
        key = (self._bound(), subject_id)
        if key in self._t.folds:
            self._t.folds[key] = dataclasses.replace(self._t.folds[key], tags_sha256=tags_sha256)

    # -- the derived rows ---------------------------------------------------------

    async def save_place(
        self, *, subject_id: str, circle_id: str, place_id: int | None,
        lat: float, lng: float, radius_m: float,
        kind: str, confidence: float, label: str | None,
        saved_place_id: str | None, first_seen_ts: int, last_seen_ts: int,
    ) -> int:
        # Behavioral twin of `SqlMobilityStore.save_place`: an id given is an
        # UPDATE that keeps it; `unlabeled` is stored, never a deletion.
        build = self._bound()
        if place_id is None:
            place_id = self._t.next_place_id
            self._t.next_place_id += 1
        else:
            row = self._t.places.get(place_id)
            if row is None or row[0] != build or row[1].subject_id != subject_id:
                return place_id  # the SQL UPDATE is scoped: no row, no write
            circle_id = row[1].circle_id
        self._t.places[place_id] = (build, DerivedPlace(
            id=place_id, subject_id=subject_id, circle_id=circle_id,
            lat=lat, lng=lng, radius_m=radius_m,
            kind=kind,  # type: ignore[arg-type]
            confidence=confidence, label=label, saved_place_id=saved_place_id,
            first_seen_ts=first_seen_ts, last_seen_ts=last_seen_ts,
        ))
        return place_id

    async def delete_places(self, *, subject_id: str, place_ids: list[int]) -> None:
        build = self._bound()
        doomed = set(place_ids)
        for pid in doomed:
            row = self._t.places.get(pid)
            if row is not None and row[0] == build and row[1].subject_id == subject_id:
                del self._t.places[pid]
        self._t.occupancy = {
            k: v for k, v in self._t.occupancy.items()
            if not (k[0] == build and k[1] == subject_id and k[2] in doomed)
        }
        self._t.rhythm = {
            k: v for k, v in self._t.rhythm.items()
            if not (v[0] == build and k[0] == subject_id and k[1] in doomed)
        }

    async def relink_stays(
        self, *, subject_id: str, links: dict[int, int | None],
    ) -> None:
        build = self._bound()
        for i, (b, s) in enumerate(self._t.stays):
            if b == build and s.subject_id == subject_id and s.started_ts in links:
                self._t.stays[i] = (b, dataclasses.replace(s, place_id=links[s.started_ts]))

    async def replace_occupancy(
        self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int,
    ) -> None:
        build = self._bound()
        self._t.occupancy = {
            k: v for k, v in self._t.occupancy.items()
            if not (k[0] == build and k[1] == subject_id and k[2] == place_id)
        }
        for how, dwell_ms in bins.items():
            self._t.occupancy[(build, subject_id, place_id, how)] = dwell_ms

    async def upsert_rhythm(
        self, *, subject_id: str, place_id: int, mean_arrival_minute: float,
        mean_departure_minute: float, duration_median_ms: int,
        duration_mad_ms: int, sample_count: int, now_ms: int,
    ) -> None:
        self._t.rhythm[(subject_id, place_id)] = (self._bound(), {
            "mean_arrival_minute": mean_arrival_minute,
            "mean_departure_minute": mean_departure_minute,
            "duration_median_ms": duration_median_ms,
            "duration_mad_ms": duration_mad_ms,
            "sample_count": sample_count,
        })

    async def save_corridor(
        self, *, subject_id: str, corridor_id: int | None,
        sequence: tuple[str, ...], sample_count: int, first_seen_ts: int,
        last_seen_ts: int,
    ) -> int:
        build = self._bound()
        if corridor_id is None:
            corridor_id = self._t.next_corridor_id
            self._t.next_corridor_id += 1
        elif corridor_id not in self._t.corridors or self._t.corridors[corridor_id][0] != build:
            return corridor_id
        self._t.corridors[corridor_id] = (build, StoredCorridor(
            id=corridor_id, subject_id=subject_id, sequence=tuple(sequence),
            sample_count=sample_count, first_seen_ts=first_seen_ts,
            last_seen_ts=last_seen_ts,
        ))
        return corridor_id

    async def delete_corridors(self, *, subject_id: str, corridor_ids: list[int]) -> None:
        build = self._bound()
        for cid in corridor_ids:
            row = self._t.corridors.get(cid)
            if row is not None and row[0] == build and row[1].subject_id == subject_id:
                del self._t.corridors[cid]

    async def relink_trips(
        self, *, subject_id: str, links: dict[int, int | None],
    ) -> None:
        build = self._bound()
        for i, (b, t) in enumerate(self._t.trips):
            if b == build and t.subject_id == subject_id and t.started_ts in links:
                self._t.trips[i] = (b, dataclasses.replace(t, corridor_id=links[t.started_ts]))

    # -- seeding ------------------------------------------------------------------

    async def _seed_fix_for_test(self, **kw: Any) -> None:
        self._t.fixes.append(_SeededFix(**{k: v for k, v in kw.items() if k != "circle_id"}))

    async def _seed_poll_for_test(
        self, *, at_ms: int, recorded_ms: int | None = None, scope: str = "c1",
        outcome: str = "ok",
    ) -> None:
        self._t.polls.append(_SeededPoll(
            id=self._t.next_poll_id, scope=scope, outcome=outcome, at_ms=at_ms,
            recorded_ms=at_ms if recorded_ms is None else recorded_ms,
        ))
        self._t.next_poll_id += 1

    async def _seed_tagged_place_for_test(self, tag: TaggedPlace) -> None:
        self._t.tagged.append(tag)

    async def _seed_build_for_test(self, *, current: bool = False) -> int:
        build = self._t.next_build
        self._t.next_build += 1
        if current:
            self._t.current_build = build
        return build

    async def _seed_trips_for_test(self, trips: list[Trip]) -> None:
        """Trips in the bound build as a fold would have written them — for
        a test of what trips add up to (corridors) without a fix stream."""
        build = self._bound()
        self._t.trips.extend((build, dataclasses.replace(t, corridor_id=None)) for t in trips)

    async def aclose(self) -> None:
        return None
