"""The unit rollups and the stimuli they raise.

`UnitRollups.run` computes, for each unit, its co-locations, outings,
daybooks, ties and responses from the Record at a watermark and the presence
rollups of the same build below them — every row a pure function
(`nora.domain.rollup`) of those inputs — and writes them diffed. It is the
same code for an incremental run and a rebuild, and it computes every row of
every unit on every run, so the rows at a watermark are one answer however
the runs fell (`tests/integration/test_rollup_determinism.py`).

It then raises the Rollup's stimuli into the per-unit `stimulus` table the
wake evaluator reads:

- **co-location start** — a co-location still open on the Record's clock
  that began within `stimulus_horizon_seconds`;
- **arrival at an event's place** — a member's stay in progress, begun within
  the horizon, inside a place an open event names. Events are step 2b's:
  `EventPlaces` is the hook, and until a producer exists it names none;
- **deviation** — a newly closed stay the rhythm scores anomalous, or
  `deviation_silence_days` consecutive daybooks silent in every room and
  never at home, raised ONLY when every source it rests on is proved healthy
  for the span: the collector polled the person's circles
  throughout (`polls_healthy`) and, for silence, every platform of the
  unit's rooms was up throughout (`adapter_up`). Missing data from an outage
  is not absence of activity (R-27).

Each stimulus's `ref` names the row that raised it, and the table keeps one
per `(unit, ref)` (migration 0071), so a later run, or a rebuild recomputing
the same row, never raises it twice.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from nora.domain.mobility import Stay
from nora.domain.ports import GeocodeCacheStore, StimulusStore
from nora.domain.presence import haversine_distance_m
from nora.domain.rollup import (
    FOLLOW_NAMES,
    FOLLOW_WINDOW_SECONDS,
    Colocation,
    Daybook,
    Deviation,
    HealthRow,
    Outing,
    SocialFact,
    SocialInput,
    StaySpan,
    adapter_up,
    colocations,
    daybooks,
    local_date,
    local_day_bounds,
    outings,
    polls_healthy,
    responses,
    silent_runs,
    ties,
)
from nora.domain.stimuli import NewStimulus
from nora.infra.sql.rollups import RecordMark, SqlRollupStore, UnitRooms
from nora.infra.sql.social_stream import SqlSocialStreamStore

log = logging.getLogger("nora.analytics.units")

_DAY_MS = 86_400_000

StimulusOutcome = Literal["raised", "duplicate", "unhealthy", "capped"]


@dataclass(frozen=True)
class UnitConfig:
    colocation_radius_m: float = 150.0
    colocation_min_overlap_seconds: float = 600.0
    response_window_seconds: float = 3600.0
    #: An unquoted member message this soon after her send, mentioning no one
    #: else, answers it; later it must name her (`FOLLOW_NAMES`).
    follow_window_seconds: float = FOLLOW_WINDOW_SECONDS
    follow_names: tuple[str, ...] = FOLLOW_NAMES
    stimulus_horizon_seconds: float = 3600.0
    deviation_silence_days: int = 2
    #: D3 (DEC-245): at most this many deviation stimuli per person per local
    #: day. A session cannot read the rows behind a rollup yet (`rollup_read`),
    #: so an unbounded deviation feed would be items it cannot judge.
    deviation_stimuli_per_person_per_day: int = 1
    health_max_poll_gap_seconds: float = 900.0
    tz_name: str = "Asia/Hong_Kong"


@dataclass(frozen=True)
class EventPlace:
    """A place an OPEN event names (step 2b's event rows): the stimulus an
    arrival there raises cites `ref`."""

    ref: str
    lat: float
    lng: float
    radius_m: float


class EventPlaces(Protocol):
    async def open_event_places(self, *, unit: str, at_ms: int) -> Sequence[EventPlace]: ...


class NoEventPlaces:
    """The hook's only implementation until step 2b gives events a place and
    a lifecycle: no open event names a place, so no arrival is raised."""

    async def open_event_places(self, *, unit: str, at_ms: int) -> Sequence[EventPlace]:
        return ()


@dataclass(frozen=True)
class UnitRun:
    unit: str
    as_of_ms: int
    changes: Mapping[str, int]
    stimuli: tuple[tuple[str, StimulusOutcome], ...] = ()


@dataclass
class PersonScope:
    """One member: the subject its presence rows are keyed by, and the
    Life360 circles its polls are recorded under."""

    subject: str
    circles: tuple[str, ...] = ()
    former_ids: tuple[str, ...] = field(default_factory=tuple)


class UnitRollups:
    def __init__(
        self, *, store: SqlRollupStore, config: UnitConfig,
        units: Sequence[UnitRooms], people: Mapping[str, PersonScope],
        stimuli: StimulusStore | None = None, events: EventPlaces | None = None,
        social: SqlSocialStreamStore | None = None,
        metrics: Any = None, clock: Callable[[], float] = time.time,
        areas: GeocodeCacheStore | None = None,
    ) -> None:
        self._store = store
        #: Where an outing's areas are read (the geocode cache, READ-ONLY, as
        #: the place labels are); without it no outing has an area.
        self._areas = areas
        #: Without it no daybook reads social items: `items_posted` stays None.
        self._social = social
        self._cfg = config
        self._units = units
        self._people = people
        self._stimuli = stimuli
        self._events = events or NoEventPlaces()
        self._metrics = metrics
        # System time for the stimulus rows (when Nora wrote them), never an
        # input to what is computed.
        self._clock = clock
        #: every observed person id (current and retired) -> its subject.

    @property
    def _subject_of(self) -> dict[str, str]:
        return {pid: subject for subject, scope in self._people.items()
                for pid in (subject, *scope.former_ids)}

    async def run(
        self, *, build_id: int, mark: RecordMark, logical_now: Mapping[str, int],
        anomalous: Mapping[str, Sequence[Stay]] | None = None,
        raise_stimuli: bool = True,
    ) -> list[UnitRun]:
        out: list[UnitRun] = []
        for unit in self._units:
            out.append(await self._run_unit(
                unit, build_id=build_id, mark=mark, logical_now=logical_now,
                anomalous=anomalous or {}, raise_stimuli=raise_stimuli,
            ))
        return out

    async def _run_unit(
        self, unit: UnitRooms, *, build_id: int, mark: RecordMark,
        logical_now: Mapping[str, int], anomalous: Mapping[str, Sequence[Stay]],
        raise_stimuli: bool,
    ) -> UnitRun:
        members = tuple(m for m in unit.members if m in self._people)
        stays = await self._store.stay_spans(build_id=build_id, subjects=members)
        homes = await self._store.homes(build_id=build_id, subjects=members)
        messages, reactions, sends, evidence = await self._store.message_facts(
            unit=unit, mark=mark, subject_of=self._subject_of,
            follow_window_ms=int(self._cfg.follow_window_seconds * 1000),
            nora_names=self._cfg.follow_names,
        )
        # The unit's clock: the newest time its Record shows — its members'
        # folds (fixes and polls) and its rooms' messages — never the wall
        # clock and never later than the watermark.
        stamps = [logical_now.get(m) for m in members]
        stamps.extend(m.ts for m in messages)
        stamps.extend(s.ts for s in sends)
        known = [s for s in stamps if s is not None]
        as_of = min(max(known), mark.through_ms) if known else mark.through_ms
        posted = await self._posted(members, mark)
        found = colocations(
            unit.unit, stays, homes=homes, radius_m=self._cfg.colocation_radius_m,
            min_overlap_ms=int(self._cfg.colocation_min_overlap_seconds * 1000),
            as_of_ms=as_of,
        )
        outs = await self._placed(outings(found))
        books = daybooks(
            unit.unit, members, stays=stays, found=found, outings_found=outs,
            messages=messages, reactions=reactions, as_of_ms=as_of,
            tz_name=self._cfg.tz_name, social=posted,
        )
        tied = ties(
            unit.unit, members, messages=messages, reactions=reactions, found=found,
            as_of_ms=as_of, tz_name=self._cfg.tz_name,
        )
        window = int(self._cfg.response_window_seconds * 1000)
        platforms = sorted({p for p, _ in unit.rooms})
        earliest = min((s.ts for s in sends), default=as_of)
        adapter_rows = await self._store.health_rows(
            sources=platforms, start_ms=earliest, through_ms=mark.through_ms,
        ) if sends else []
        platform_up = {
            s.id: adapter_up(
                adapter_rows, platform=s.platform, start_ms=s.ts, end_ms=s.ts + window,
            )
            for s in sends
        }
        answered = responses(
            unit.unit, sends, evidence, window_ms=window, as_of_ms=as_of,
            platform_up=platform_up,
        )
        changes = await self._store.apply(
            build_id=build_id, unit=unit.unit, colocations=found, outings=outs,
            daybooks=books, ties=tied, responses=answered,
        )
        raised: list[tuple[str, StimulusOutcome]] = []
        if raise_stimuli and self._stimuli is not None:
            raised = await self._raise(
                self._stimuli, unit, members=members, as_of=as_of, mark=mark, found=found,
                stays=stays, books=books, anomalous=anomalous, platforms=platforms,
            )
        return UnitRun(unit=unit.unit, as_of_ms=as_of, changes=changes, stimuli=tuple(raised))

    async def _placed(self, outs: Sequence[Outing]) -> list[Outing]:
        """Each outing with the district and city of the nearest cached
        geocode within the co-location radius of its centre, the cache
        `nora.analytics` already labels places from (it never calls a
        geocoder). An outing at a saved place gets none: a saved place's
        existence is itself secret (R-35), and the memory index would make
        its area a hop away from it. Like a label, the area is the cache as
        this run reads it, an input outside the Record's events."""
        if self._areas is None:
            return list(outs)
        placed: list[Outing] = []
        for o in outs:
            area = None if o.saved_place_id is not None else await self._areas.area_within(
                lat=o.lat, lng=o.lng, radius_m=self._cfg.colocation_radius_m,
            )
            placed.append(o if area is None else dataclasses.replace(
                o, district=area.district, city=area.city,
            ))
        return placed

    async def _posted(self, members: Sequence[str], mark: RecordMark) -> SocialInput:
        """`members`' social items and when their accounts were first observed,
        under their subject (an item is recorded under any id the person has
        held)."""
        if self._social is None:
            return SocialInput()
        ids = [p for p, subject in self._subject_of.items() if subject in members]
        read = await self._social.posted(persons=ids, through_ms=mark.through_ms)
        observed: dict[str, int] = {}
        for person, since in read.observed_from.items():
            subject = self._subject_of[person]
            observed[subject] = min(since, observed.get(subject, since))
        return SocialInput(
            items=tuple(SocialFact(self._subject_of[i.person_id], i.ts) for i in read.items),
            observed_from=observed,
        )

    # -- stimuli --------------------------------------------------------------

    async def _raise(
        self, sink: StimulusStore, unit: UnitRooms, *, members: Sequence[str], as_of: int,
        mark: RecordMark, found: Sequence[Colocation], stays: Sequence[StaySpan],
        books: Sequence[Daybook], anomalous: Mapping[str, Sequence[Stay]],
        platforms: Sequence[str],
    ) -> list[tuple[str, StimulusOutcome]]:
        horizon = int(self._cfg.stimulus_horizon_seconds * 1000)
        out: list[tuple[str, StimulusOutcome]] = []
        for c in found:
            if c.open and as_of - c.valid_from_ms <= horizon:
                ref = f"colocation|{','.join(c.persons)}|{c.valid_from_ms}"
                out.append(("colocation_start", await self._append(
                    sink, unit.unit, "colocation_start", item="colocation_start",
                    ref=ref, at_ms=c.valid_from_ms,
                )))
        places = await self._events.open_event_places(unit=unit.unit, at_ms=as_of)
        if places:
            for s in stays:
                if not s.open or as_of - s.started_ts > horizon:
                    continue
                for place in places:
                    if haversine_distance_m(s.lat, s.lng, place.lat, place.lng) <= place.radius_m:
                        ref = f"arrival|{place.ref}|{s.person_id}|{s.started_ts}"
                        out.append(("arrival", await self._append(
                            sink, unit.unit, "arrival", item="arrival", ref=ref, at_ms=s.started_ts,
                            person_id=s.person_id,
                        )))
        for deviation in self._deviations(unit.unit, members, as_of, books, anomalous):
            healthy = await self._healthy(
                deviation, platforms=platforms, through_ms=mark.through_ms,
            )
            if not healthy:
                out.append(("deviation", "unhealthy"))
                self._count("deviation", "unhealthy")
                continue
            if await self._capped(sink, unit.unit, deviation):
                out.append(("deviation", "capped"))
                self._count("deviation", "capped")
                continue
            out.append(("deviation", await self._append(
                sink, unit.unit, "deviation", item=f"deviation_{deviation.kind}", ref=deviation.ref,
                at_ms=deviation.end_ms, person_id=deviation.person_id,
            )))
        return out

    async def _capped(self, sink: StimulusStore, unit: str, deviation: Deviation) -> bool:
        """D3: the person already has `deviation_stimuli_per_person_per_day`
        deviation stimuli on the local day this one falls on (its `at_ms`, the
        day the stimulus is raised for), raised by this run or an earlier one.
        The stimulus already on record for THIS deviation counts, so a deviation
        raised earlier reads as capped again on every later run (as it read as a
        duplicate)."""
        day = local_date(deviation.end_ms, self._cfg.tz_name)
        start, end = local_day_bounds(day, self._cfg.tz_name)
        raised = await sink.count_rollup(
            unit=unit, person_id=deviation.person_id, item_prefix="deviation_",
            from_ms=start, to_ms=end,
        )
        return raised >= self._cfg.deviation_stimuli_per_person_per_day

    def _deviations(
        self, unit: str, members: Sequence[str], as_of: int,
        books: Sequence[Daybook], anomalous: Mapping[str, Sequence[Stay]],
    ) -> list[Deviation]:
        horizon = int(self._cfg.stimulus_horizon_seconds * 1000)
        found: list[Deviation] = []
        for person in members:
            for stay in anomalous.get(person, ()):
                if as_of - stay.ended_ts <= horizon:
                    found.append(Deviation(
                        unit=unit, person_id=person, kind="anomaly",
                        start_ms=stay.started_ts, end_ms=stay.ended_ts,
                    ))
            today = local_date(as_of, self._cfg.tz_name)
            for first, last in silent_runs(
                books, person_id=person, days=self._cfg.deviation_silence_days,
                through_date=today,
            ):
                start, _ = local_day_bounds(first, self._cfg.tz_name)
                _, end = local_day_bounds(last, self._cfg.tz_name)
                # Only the run that ends with the day just completed: an
                # older run was either raised on its day or happened before
                # the Record could prove anything.
                if as_of - end <= _DAY_MS:
                    found.append(Deviation(
                        unit=unit, person_id=person, kind="silence",
                        start_ms=start, end_ms=end,
                    ))
        return found

    async def _healthy(
        self, deviation: Deviation, *, platforms: Sequence[str], through_ms: int,
    ) -> bool:
        """D3: every source the deviation rests on proved healthy for its
        span. Presence always (both kinds place a person); the unit's
        platforms too for silence, which also rests on «posted nothing»."""
        scope = self._people.get(deviation.person_id)
        if scope is None or not scope.circles:
            return False
        rows: list[HealthRow] = await self._store.health_rows(
            sources=["presence", *platforms] if deviation.kind == "silence" else ["presence"],
            start_ms=deviation.start_ms, through_ms=through_ms,
        )
        gap = int(self._cfg.health_max_poll_gap_seconds * 1000)
        if not any(
            polls_healthy(rows, scope=circle, start_ms=deviation.start_ms,
                          end_ms=deviation.end_ms, max_gap_ms=gap)
            for circle in scope.circles
        ):
            return False
        if deviation.kind == "silence":
            return all(
                adapter_up(rows, platform=p, start_ms=deviation.start_ms,
                           end_ms=deviation.end_ms)
                for p in platforms
            )
        return True

    async def _append(
        self, sink: StimulusStore, unit: str, kind: str, *, item: str, ref: str,
        at_ms: int, person_id: str | None = None,
    ) -> StimulusOutcome:
        stimulus_id = await sink.append(
            stimulus=NewStimulus(
                unit=unit, kind="rollup", item=item, ref=ref, at_ms=at_ms,
                person_id=person_id,
            ),
            recorded_at_ms=int(self._clock() * 1000),
        )
        outcome: StimulusOutcome = "raised" if stimulus_id is not None else "duplicate"
        self._count(kind, outcome)
        return outcome

    def _count(self, kind: str, outcome: StimulusOutcome) -> None:
        if self._metrics is not None:
            self._metrics.inc("nora_rollup_stimuli_total", kind=kind, outcome=outcome)
