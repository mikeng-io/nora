"""The unit rollups and the stimuli they raise.

`UnitRollups.run` computes, for each unit, its co-locations, outings,
daybooks, ties and responses from the Record at a watermark and the presence
rollups of the same build below them — every row a pure function
(`nora.domain.rollup`) of those inputs — and writes them diffed. It is the
same code for an incremental run and a rebuild, and it computes every row of
every unit on every run, so the rows at a watermark are one answer however
the runs fell (`tests/integration/test_rollup_determinism.py`).

It then records, for each person silent in every room for
`deviation_silence_days` consecutive daybooks, a graph `absence` stimulus,
raised ONLY when every source it rests on is proved healthy for the span: the
collector polled the person's circles throughout (`polls_healthy`) and every
platform of the unit's rooms was up throughout (`adapter_up`). Missing data
from an outage is not absence of activity (R-27).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from nora.access import IdentityAccess
from nora.domain.ports import GeocodeCacheStore
from nora.domain.rollup import (
    FOLLOW_NAMES,
    FOLLOW_WINDOW_SECONDS,
    NEVER,
    Daybook,
    Deviation,
    HealthRow,
    Outing,
    SocialFact,
    SocialInput,
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
from nora.infra.sql.projection_time import ProjectionInputs
from nora.infra.sql.rollups import RecordMark, SqlRollupStore, UnitRooms
from nora.infra.sql.social_stream import SqlSocialStreamStore

log = logging.getLogger("nora.analytics.units")

_DAY_MS = 86_400_000

@dataclass(frozen=True)
class UnitConfig:
    colocation_radius_m: float = 150.0
    colocation_min_overlap_seconds: float = 600.0
    response_window_seconds: float = 3600.0
    #: An unquoted member message this soon after her send, mentioning no one
    #: else, answers it; later it must name her (`FOLLOW_NAMES`).
    follow_window_seconds: float = FOLLOW_WINDOW_SECONDS
    follow_names: tuple[str, ...] = FOLLOW_NAMES
    deviation_silence_days: int = 2
    health_max_poll_gap_seconds: float = 900.0
    tz_name: str = "Asia/Hong_Kong"


@dataclass(frozen=True)
class UnitRun:
    unit: str
    as_of_ms: int
    changes: Mapping[str, int]


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
        social: SqlSocialStreamStore | None = None,
        areas: GeocodeCacheStore | None = None, access: IdentityAccess | None = None,
    ) -> None:
        self._store = store
        #: Where an outing's areas are read (the geocode cache, READ-ONLY, as
        #: the place labels are); without it no outing has an area.
        self._areas = areas
        #: Without it no daybook reads social items: `items_posted` stays None.
        self._social = social
        self._access = access
        self._cfg = config
        self._units = units
        self._people = people
        #: every observed person id (current and retired) -> its subject.

    @property
    def _subject_of(self) -> dict[str, str]:
        return {pid: subject for subject, scope in self._people.items()
                for pid in (subject, *scope.former_ids)}

    async def projection_time(self) -> int | None:
        return await self._store.read_projection_frontier(tuple(
            ProjectionInputs(unit.unit, unit.rooms, tuple(
                (circle, (person, *scope.former_ids))
                for person in unit.members if (scope := self._people.get(person)) is not None
                for circle in scope.circles
            ))
            for unit in self._units
        ))

    async def run(
        self, *, build_id: int, mark: RecordMark, logical_now: Mapping[str, int],
        raise_stimuli: bool = True,
    ) -> list[UnitRun]:
        out: list[UnitRun] = []
        for unit in self._units:
            out.append(await self._run_unit(
                unit, build_id=build_id, mark=mark, logical_now=logical_now,
                raise_stimuli=raise_stimuli,
            ))
        return out

    async def _run_unit(
        self, unit: UnitRooms, *, build_id: int, mark: RecordMark,
        logical_now: Mapping[str, int], raise_stimuli: bool,
    ) -> UnitRun:
        members = tuple(m for m in unit.members if m in self._people)
        stays = await self._store.stay_spans(build_id=build_id, subjects=members, unit=unit.unit)
        homes = await self._store.homes(build_id=build_id, subjects=members, unit=unit.unit)
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
        known = [s for s in stamps if s is not None and s != NEVER]
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
        if raise_stimuli:
            await self._absences(
                unit, members=members, as_of=as_of, mark=mark, books=books,
                platforms=platforms,
            )
        return UnitRun(unit=unit.unit, as_of_ms=as_of, changes=changes)

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
        allowed = {p for p in members if self._access is None
                   or not await self._access.social_off(p)}
        ids = [p for p, subject in self._subject_of.items() if subject in allowed]
        read = await self._social.posted(persons=ids, through_ms=mark.through_ms)
        allowed = {p for p in allowed if self._access is None
                   or not await self._access.social_off(p)}
        observed: dict[str, int] = {}
        for person, since in read.observed_from.items():
            if self._subject_of[person] not in allowed:
                continue
            subject = self._subject_of[person]
            observed[subject] = min(since, observed.get(subject, since))
        return SocialInput(
            items=tuple(SocialFact(self._subject_of[i.person_id], i.ts) for i in read.items
                        if self._subject_of[i.person_id] in allowed),
            observed_from=observed,
        )

    # -- absences -------------------------------------------------------------

    async def _absences(
        self, unit: UnitRooms, *, members: Sequence[str], as_of: int, mark: RecordMark,
        books: Sequence[Daybook], platforms: Sequence[str],
    ) -> None:
        for deviation in self._deviations(unit.unit, members, as_of, books):
            if await self._healthy(deviation, platforms=platforms, through_ms=mark.through_ms):
                await self._store.append_absence(deviation)

    def _deviations(
        self, unit: str, members: Sequence[str], as_of: int,
        books: Sequence[Daybook],
    ) -> list[Deviation]:
        found: list[Deviation] = []
        for person in members:
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
        """Every source the silence rests on proved healthy for its span: the
        person's presence polls and the unit's platforms («posted nothing»)."""
        scope = self._people.get(deviation.person_id)
        if scope is None or not scope.circles:
            return False
        rows: list[HealthRow] = await self._store.health_rows(
            sources=["presence", *platforms] if deviation.kind == "silence" else ["presence"],
            start_ms=deviation.start_ms, through_ms=through_ms,
            graph_unit=deviation.unit,
        )
        gap = int(self._cfg.health_max_poll_gap_seconds * 1000)
        if not all(
            polls_healthy(rows, scope=circle, start_ms=deviation.start_ms,
                          end_ms=deviation.end_ms, max_gap_ms=gap, require_preceding=True)
            for circle in scope.circles
        ):
            return False
        if deviation.kind == "silence":
            return all(
                adapter_up(rows, platform=p, start_ms=deviation.start_ms,
                           end_ms=deviation.end_ms, require_preceding=True)
                for p in platforms
            )
        return True
