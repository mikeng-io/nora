"""The Rollup's own types: the presence fold's events and resume state, the
unit rollups (co-location, outing, daybook, tie, response), their lineage,
citation by overlap, and the healthy-source proof a deviation needs.

Pure: no store, no clock. Every function here is a function of its arguments,
which is what lets the analytics process's incremental run and a rebuild at
the same watermark compute the same rows.

**The presence fold** (`nora.analytics.rollup.MobilityRollup`) consumes the
Record as ONE ordered stream of events per person: that person's fixes and
the presence collector's recorded polls of their circles, in `EventPosition`
order. A fix is evidence of where the person was and of the time; a poll that
succeeded is evidence of the time only. The fold's clock (`logical_now_ms`)
is the newest time that evidence shows, never the wall clock, so a stay
closes when the Record shows it ended (a later fix, or a poll proving time
passed with no fix), not because a clock passed. `FoldState` is where
the fold stands after the last event it consumed, stored per build and
person, so the next run continues from exactly there.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from itertools import pairwise
from typing import Any, Literal, NamedTuple
from zoneinfo import ZoneInfo

from nora.domain.mobility import RawFix, Stay, TaggedPlace
from nora.domain.presence import haversine_distance_m

#: Hong Kong: the unit's local day and ISO week («local HK date»).
LOCAL_TZ = "Asia/Hong_Kong"


class EventPosition(NamedTuple):
    """Where one Record event sits in the fold's canonical order:
    `(recorded_ms, rank, id)`. `recorded_ms` is when Nora wrote the row
    (system time), `rank` breaks a tie between sources (a fix before the poll
    that fetched it), `id` is the row's own id. Every run reads events
    strictly after its position and at or before its watermark, in this
    order, so how the Record is cut into runs cannot change what the fold
    sees."""

    recorded_ms: int
    rank: int
    id: int


#: A fix sorts before a poll recorded at the same instant (the collector
#: writes a cycle's fixes, then its poll row).
FIX_RANK = 0
POLL_RANK = 1

#: Before every event, whatever its time (a synthetic Record may start before
#: the epoch; a real one never does).
NEVER = -(2**62)


def start_position() -> EventPosition:
    """Where a fold that has read nothing stands: before every event."""
    return EventPosition(NEVER, 0, 0)


@dataclass(frozen=True)
class FixEvent:
    position: EventPosition
    fix: RawFix


@dataclass(frozen=True)
class PollEvent:
    """A presence collector poll that SUCCEEDED (`source_health` outcome
    `ok`): evidence that the Record's clock reached `at_ms`. A failed poll is
    no evidence of time passing for anyone (R-27): the fold never reads one."""

    position: EventPosition
    at_ms: int


Event = FixEvent | PollEvent


@dataclass(frozen=True)
class FoldState:
    """Where the presence fold stands for one person in one build.

    `window_ids` are the fixes consumed but not yet finalized (an open stay,
    an undecided trip), in consumption order; the next run re-reads them by
    id. `frontier_ts` is the newest time the finalized output reaches: a fix
    that arrives later with an older `ts` is dropped as late and counted in
    `late_fixes`, never spliced into a stay already written. `open_stay` is
    the trailing stay in progress once it has dwelled `min_dwell`, what
    co-location and arrival read «now» from. `tags_sha256` is the saved-place
    set the last place plan read."""

    position: EventPosition = field(default_factory=start_position)
    logical_now_ms: int = NEVER
    frontier_ts: int = NEVER
    window_ids: tuple[int, ...] = ()
    tags_sha256: str | None = None
    open_stay: Stay | None = None
    late_fixes: int = 0


class NoRollupBuildError(RuntimeError):
    """No Rollup build is current (a fresh database, before the analytics
    process's first rebuild switches one in). A reader must answer R-27
    `unavailable` — «I couldn't check» — never an empty result, which would
    say «there is nothing» about data that was never derived."""


@dataclass(frozen=True)
class StoredCorridor:
    """One `presence_corridors` row of a build: a named route, what its trips
    add up to."""

    id: int
    subject_id: str
    sequence: tuple[str, ...]
    sample_count: int
    first_seen_ts: int
    last_seen_ts: int


def stay_to_json(stay: Stay | None) -> dict[str, Any] | None:
    if stay is None:
        return None
    return {
        "subject_id": stay.subject_id, "person_id": stay.person_id,
        "circle_id": stay.circle_id, "started_ts": stay.started_ts,
        "ended_ts": stay.ended_ts, "lat": stay.lat, "lng": stay.lng,
        "radius_m": stay.radius_m, "fix_count": stay.fix_count,
        "address": stay.address,
    }


def stay_from_json(data: Mapping[str, Any] | None) -> Stay | None:
    if not data:
        return None
    return Stay(
        subject_id=data["subject_id"], person_id=data["person_id"],
        circle_id=data["circle_id"], started_ts=int(data["started_ts"]),
        ended_ts=int(data["ended_ts"]), lat=float(data["lat"]),
        lng=float(data["lng"]), radius_m=float(data["radius_m"]),
        fix_count=int(data["fix_count"]), address=data.get("address"),
    )


def tags_sha256(tags: Iterable[TaggedPlace]) -> str:
    """A stable digest of the saved places a place plan reads: when it
    changes, the plan is recomputed."""
    ordered = sorted(
        (t.circle_id, t.place_id, t.name, t.lat, t.lng, t.radius_m) for t in tags
    )
    return hashlib.sha256(json.dumps(ordered).encode()).hexdigest()


# --- lineage ------------------------------------------------------------

LineageKind = Literal["presence", "message", "face", "social", "play", "perception"]


class LineageEntry(NamedTuple):
    """One `(kind, subject, origin room)` a row rests on. `message` entries
    carry their room as `origin` (`platform:conversation_id`) and no
    subject; a `presence` entry names the person placed and no room."""

    kind: str
    subject: str | None
    origin: str | None


def presence_lineage(*persons: str) -> tuple[LineageEntry, ...]:
    """Placing a person is presence, whatever row does it."""
    return tuple(LineageEntry("presence", p, None) for p in sorted(set(persons)))


def message_lineage(*rooms: str) -> tuple[LineageEntry, ...]:
    return tuple(LineageEntry("message", None, r) for r in sorted(set(rooms)))


def lineage_json(entries: Iterable[LineageEntry]) -> list[list[str | None]]:
    """The stored form: a sorted, de-duplicated JSON array of triples."""
    return [list(e) for e in sorted(set(entries), key=lambda e: tuple(x or "" for x in e))]


def room_key(platform: str, conversation_id: str) -> str:
    return f"{platform}:{conversation_id}"


# --- local time -----------------------------------------------------------


def local_date(ts_ms: int, tz_name: str = LOCAL_TZ) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo(tz_name)).date().isoformat()


def local_day_bounds(day: str, tz_name: str = LOCAL_TZ) -> tuple[int, int]:
    """`[start, end)` of a local date, epoch ms."""
    tz = ZoneInfo(tz_name)
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def days_touched(start_ms: int, end_ms: int, tz_name: str = LOCAL_TZ) -> list[str]:
    """Every local date an interval touches, oldest first."""
    first = date.fromisoformat(local_date(start_ms, tz_name))
    last = date.fromisoformat(local_date(max(start_ms, end_ms), tz_name))
    out: list[str] = []
    d = first
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def iso_week(ts_ms: int, tz_name: str = LOCAL_TZ) -> str:
    y, w, _ = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo(tz_name)).isocalendar()
    return f"{y}-W{w:02d}"


def iso_week_bounds(week: str, tz_name: str = LOCAL_TZ) -> tuple[int, int]:
    year, w = week.split("-W")
    monday = date.fromisocalendar(int(year), int(w), 1)
    tz = ZoneInfo(tz_name)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=tz)
    return int(start.timestamp() * 1000), int((start + timedelta(days=7)).timestamp() * 1000)


def overlap_ms(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def utc_ms(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp() * 1000)


# --- citation by overlap ----------------------------------------------------


@dataclass(frozen=True)
class Citation:
    """What a row above the Rollup cites: `(kind, subject, interval)`, never a
    row id or an exact start, because a new build recomputes rows and their
    boundaries. `subject` is the person for a person-scoped rollup, else the
    unit-qualified subject the kind names (`unit|a,b` for a tie, `unit|person`
    for a daybook, `unit|persons` for a co-location or outing,
    `unit|message_id` for a response)."""

    kind: str
    subject: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class Resolution:
    """A citation resolved against one build: the rows overlapping its
    interval, or `unsupported` when none do."""

    citation: Citation
    row_ids: tuple[int, ...]

    @property
    def unsupported(self) -> bool:
        return not self.row_ids


def resolve_by_overlap(
    citation: Citation, rows: Sequence[tuple[int, int, int]],
) -> Resolution:
    """`rows` are `(id, valid_from_ms, valid_to_ms)` of the cited kind and
    subject in one build. A row resolves when its interval overlaps the
    citation's; a zero-length interval (an instant) overlaps a row that
    contains it."""
    hits = [
        rid for rid, start, end in rows
        if (start <= citation.end_ms and end >= citation.start_ms)
    ]
    return Resolution(citation=citation, row_ids=tuple(sorted(hits)))


#: The offset a cited interval is printed in: Hong Kong's, fixed.
_CITED_TZ = timezone(timedelta(hours=8), "HKT")


def iso(ts_ms: int) -> str:
    """RFC 3339 in Hong Kong time, to the second: how `rollup_read` shows a
    row's interval and so how a note or memo cites it (DEC-156: epoch
    milliseconds are unreadable to a model)."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=_CITED_TZ).isoformat(timespec="seconds")


def episode_key(subject: str, start_ms: int, end_ms: int) -> str:
    """An outing's, stay's or trip's memory-index key: its citation
    `(subject, interval)` exactly as `rollup(kind|subject|from/to)` writes it
    (`nora.domain.note_body.rollup_key`), without the kind, which the node
    carries. Never a row id, so a rebuild that recomputes the same happening
    keeps the key, and what cites it still reaches it."""
    return f"{subject}|{iso(start_ms)}/{iso(end_ms)}"


# --- the unit rollups -------------------------------------------------------


@dataclass(frozen=True)
class StaySpan:
    """A stay as the unit rollups read it: closed, or the stay in progress
    (`open`), with the place it is linked to in the same build."""

    person_id: str
    started_ts: int
    ended_ts: int
    lat: float
    lng: float
    radius_m: float
    open: bool = False
    place_kind: str | None = None
    place_label: str | None = None
    saved_place_id: str | None = None
    place_id: int | None = None


@dataclass(frozen=True)
class Home:
    """A member's home place in the current build (a place labelled `home`)."""

    person_id: str
    lat: float
    lng: float
    radius_m: float


@dataclass(frozen=True)
class Colocation:
    unit: str
    persons: tuple[str, ...]
    valid_from_ms: int
    valid_to_ms: int
    lat: float
    lng: float
    radius_m: float
    place_label: str | None
    saved_place_id: str | None
    at_home_of: tuple[str, ...]
    homes_unknown: tuple[str, ...]
    stay_count: int
    open: bool
    as_of_ms: int

    @property
    def lineage(self) -> tuple[LineageEntry, ...]:
        return presence_lineage(*self.persons)

    @property
    def is_outing(self) -> bool:
        """Two or more members away from EVERY member's home. A member with no
        home place yet cannot be proved away from it, so the answer is no,
        never a guess (fail-closed labelling)."""
        return (
            len(self.persons) >= 2 and not self.at_home_of and not self.homes_unknown
        )


@dataclass(frozen=True)
class Outing:
    unit: str
    persons: tuple[str, ...]
    valid_from_ms: int
    valid_to_ms: int
    lat: float
    lng: float
    place_label: str | None
    saved_place_id: str | None
    open: bool
    as_of_ms: int

    @property
    def lineage(self) -> tuple[LineageEntry, ...]:
        return presence_lineage(*self.persons)


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    return haversine_distance_m(lat1, lng1, lat2, lng2)


def colocations(
    unit: str, stays: Sequence[StaySpan], *, homes: Mapping[str, Sequence[Home]],
    radius_m: float, min_overlap_ms: int, as_of_ms: int,
) -> list[Colocation]:
    """Co-locations of `unit`'s members: two stays of DIFFERENT
    persons whose centres are within `radius_m` and whose intervals overlap
    by at least `min_overlap_ms` are together for that overlap; stays joined
    that way, transitively, are one episode. Its persons are every person in
    it, its interval the union of the pairwise overlaps, its place the
    overlap-weighted centre, `open` when a stay in progress reaches the end.

    `homes` is each member's home places; a member absent from it has no
    known home (`homes_unknown`). Deterministic in the order of `stays`: they
    are sorted here first."""
    ordered = sorted(stays, key=lambda s: (s.started_ts, s.person_id, s.ended_ts, s.lat, s.lng))
    n = len(ordered)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    edges: list[tuple[int, int, int, int]] = []
    # Sweep by start: only stays that started before `a` ends can overlap it.
    for i, a in enumerate(ordered):
        for j in range(i + 1, n):
            b = ordered[j]
            if b.started_ts >= a.ended_ts:
                break
            if a.person_id == b.person_id:
                continue
            lo, hi = max(a.started_ts, b.started_ts), min(a.ended_ts, b.ended_ts)
            if hi - lo < min_overlap_ms:
                continue
            if _haversine_m(a.lat, a.lng, b.lat, b.lng) > radius_m:
                continue
            edges.append((i, j, lo, hi))
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)
    groups: dict[int, list[tuple[int, int, int, int]]] = {}
    for e in edges:
        groups.setdefault(find(e[0]), []).append(e)
    out: list[Colocation] = []
    for root in sorted(groups):
        group_edges = groups[root]
        members = sorted({i for e in group_edges for i in e[:2]})
        spans = [ordered[i] for i in members]
        start = min(e[2] for e in group_edges)
        end = max(e[3] for e in group_edges)
        weights = [max(1, e[3] - e[2]) for e in group_edges]
        total = sum(weights) * 2
        pairs = list(zip(group_edges, weights, strict=True))
        lat = sum((ordered[e[0]].lat + ordered[e[1]].lat) * w for e, w in pairs) / total
        lng = sum((ordered[e[0]].lng + ordered[e[1]].lng) * w for e, w in pairs) / total
        radius = max(_haversine_m(lat, lng, s.lat, s.lng) for s in spans)
        persons = tuple(sorted({s.person_id for s in spans}))
        at_home: list[str] = []
        unknown: list[str] = []
        for p in persons:
            places = homes.get(p, ())
            if not places:
                unknown.append(p)
                continue
            if any(
                _haversine_m(lat, lng, h.lat, h.lng) <= h.radius_m + radius_m
                for h in places
            ):
                at_home.append(p)
        saved = sorted({s.saved_place_id for s in spans if s.saved_place_id})
        labels = sorted({s.place_label for s in spans if s.place_label})
        out.append(Colocation(
            unit=unit, persons=persons, valid_from_ms=start, valid_to_ms=end,
            lat=lat, lng=lng, radius_m=radius,
            place_label=labels[0] if labels else None,
            saved_place_id=saved[0] if saved else None,
            at_home_of=tuple(at_home), homes_unknown=tuple(unknown),
            stay_count=len(spans),
            open=any(s.open and s.ended_ts >= end for s in spans),
            # The row's OWN newest evidence, never the unit's clock: a clock
            # stamp would rewrite every unit rollup row on every run.
            as_of_ms=max(s.ended_ts for s in spans),
        ))
    return out


def outings(found: Sequence[Colocation]) -> list[Outing]:
    """The co-locations that are outings (`Colocation.is_outing`)."""
    return [
        Outing(
            unit=c.unit, persons=c.persons, valid_from_ms=c.valid_from_ms,
            valid_to_ms=c.valid_to_ms, lat=c.lat, lng=c.lng,
            place_label=c.place_label, saved_place_id=c.saved_place_id,
            open=c.open, as_of_ms=c.as_of_ms,
        )
        for c in found if c.is_outing
    ]


@dataclass(frozen=True)
class MessageFact:
    """One person-authored message in a unit room, as the unit rollups read
    it. `quoted_person` is the author of the message it replies to (resolved
    in the same room), `mentioned` the persons its mentions resolve to."""

    id: int
    person_id: str
    room: str
    ts: int
    quoted_person: str | None = None
    mentioned: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReactionFact:
    """One reaction by a person to a message whose author is a person."""

    id: int
    person_id: str
    target_person: str
    room: str
    ts: int


@dataclass(frozen=True)
class Daybook:
    unit: str
    person_id: str
    local_date: str
    stays: tuple[dict[str, Any], ...]
    stay_ms: int
    home_ms: int
    colocation_ms: int
    with_persons: tuple[str, ...]
    outings: int
    rooms: Mapping[str, int]
    messages: int
    reactions: int
    valid_from_ms: int
    valid_to_ms: int
    as_of_ms: int
    items_posted: int | None = None

    @property
    def lineage(self) -> tuple[LineageEntry, ...]:
        entries = list(message_lineage(*self.rooms))
        if self.stays or self.colocation_ms:
            entries.extend(presence_lineage(self.person_id, *self.with_persons))
        return tuple(entries)

    @property
    def silent(self) -> bool:
        """Nothing posted in any room and never at home: the day a silence
        deviation counts."""
        return self.messages == 0 and self.reactions == 0 and self.home_ms == 0


def daybooks(
    unit: str, members: Sequence[str], *, stays: Sequence[StaySpan],
    found: Sequence[Colocation], outings_found: Sequence[Outing],
    messages: Sequence[MessageFact], reactions: Sequence[ReactionFact],
    as_of_ms: int, tz_name: str = LOCAL_TZ,
) -> list[Daybook]:
    """One row per member per local date on which the Record or the rollups
    below hold anything for them, up to the date `as_of_ms` falls on (a day
    still running is a row too; it is recomputed as the day fills). No upward
    input: a daybook counts outings, never events attended."""
    member_set = set(members)
    per: dict[tuple[str, str], dict[str, Any]] = {}

    def slot(person: str, day: str) -> dict[str, Any]:
        return per.setdefault((person, day), {
            "stays": [], "stay_ms": 0, "home_ms": 0, "coloc_ms": 0,
            "with": set(), "outings": 0, "rooms": {}, "messages": 0, "reactions": 0,
            "newest": None,
        })

    def seen(row: dict[str, Any], ts: int) -> None:
        row["newest"] = ts if row["newest"] is None else max(row["newest"], ts)

    for s in stays:
        if s.person_id not in member_set:
            continue
        for day in days_touched(s.started_ts, s.ended_ts, tz_name):
            d0, d1 = local_day_bounds(day, tz_name)
            piece = overlap_ms(s.started_ts, s.ended_ts, d0, d1)
            if piece <= 0 and not (d0 <= s.started_ts < d1):
                continue
            row = slot(s.person_id, day)
            row["stays"].append({
                "from": max(s.started_ts, d0), "to": min(s.ended_ts, d1),
                "place_kind": s.place_kind, "place_label": s.place_label,
                "open": s.open,
            })
            row["stay_ms"] += piece
            seen(row, min(s.ended_ts, d1))
            if s.place_kind == "home":
                row["home_ms"] += piece
    for c in found:
        for p in c.persons:
            if p not in member_set:
                continue
            for day in days_touched(c.valid_from_ms, c.valid_to_ms, tz_name):
                d0, d1 = local_day_bounds(day, tz_name)
                piece = overlap_ms(c.valid_from_ms, c.valid_to_ms, d0, d1)
                if piece <= 0:
                    continue
                row = slot(p, day)
                row["coloc_ms"] += piece
                seen(row, min(c.as_of_ms, d1))
                row["with"].update(q for q in c.persons if q != p)
    for o in outings_found:
        for p in o.persons:
            if p in member_set:
                row = slot(p, local_date(o.valid_from_ms, tz_name))
                row["outings"] += 1
                seen(row, o.as_of_ms)
    for m in messages:
        if m.person_id not in member_set:
            continue
        row = slot(m.person_id, local_date(m.ts, tz_name))
        row["messages"] += 1
        row["rooms"][m.room] = row["rooms"].get(m.room, 0) + 1
        seen(row, m.ts)
    for r in reactions:
        if r.person_id not in member_set:
            continue
        row = slot(r.person_id, local_date(r.ts, tz_name))
        row["reactions"] += 1
        row["rooms"].setdefault(r.room, 0)
        seen(row, r.ts)
    last_day = local_date(as_of_ms, tz_name)
    out: list[Daybook] = []
    for (person, day), row in sorted(per.items()):
        if day > last_day:
            continue
        d0, d1 = local_day_bounds(day, tz_name)
        out.append(Daybook(
            unit=unit, person_id=person, local_date=day,
            stays=tuple(sorted(row["stays"], key=lambda x: (x["from"], x["to"]))),
            stay_ms=row["stay_ms"], home_ms=row["home_ms"],
            colocation_ms=row["coloc_ms"], with_persons=tuple(sorted(row["with"])),
            outings=row["outings"], rooms=dict(sorted(row["rooms"].items())),
            messages=row["messages"], reactions=row["reactions"],
            valid_from_ms=d0, valid_to_ms=d1,
            as_of_ms=row["newest"] if row["newest"] is not None else d0,
        ))
    return out


@dataclass(frozen=True)
class Tie:
    unit: str
    person_a: str
    person_b: str
    iso_week: str
    replies_ab: int
    replies_ba: int
    mentions_ab: int
    mentions_ba: int
    reactions_ab: int
    reactions_ba: int
    colocation_ms: int
    rooms: tuple[str, ...]
    valid_from_ms: int
    valid_to_ms: int
    as_of_ms: int

    @property
    def lineage(self) -> tuple[LineageEntry, ...]:
        entries = list(message_lineage(*self.rooms))
        if self.colocation_ms:
            entries.extend(presence_lineage(self.person_a, self.person_b))
        return tuple(entries)


def ties(
    unit: str, members: Sequence[str], *, messages: Sequence[MessageFact],
    reactions: Sequence[ReactionFact], found: Sequence[Colocation],
    as_of_ms: int, tz_name: str = LOCAL_TZ,
) -> list[Tie]:
    """One row per pair of members per ISO week in which they interacted:
    replies, mentions and reactions each way, and minutes co-located
    (co-tags and play are not counted)."""
    member_set = set(members)
    acc: dict[tuple[str, str, str], dict[str, Any]] = {}

    def bump(src: str, dst: str, ts: int, what: str, room: str | None) -> None:
        if src == dst or src not in member_set or dst not in member_set:
            return
        a, b = sorted((src, dst))
        row = acc.setdefault((a, b, iso_week(ts, tz_name)), {
            "replies_ab": 0, "replies_ba": 0, "mentions_ab": 0, "mentions_ba": 0,
            "reactions_ab": 0, "reactions_ba": 0, "coloc": 0, "rooms": set(), "newest": ts,
        })
        row["newest"] = max(row["newest"], ts)
        row[f"{what}_{'ab' if src == a else 'ba'}"] += 1
        if room is not None:
            row["rooms"].add(room)

    for m in messages:
        if m.quoted_person is not None:
            bump(m.person_id, m.quoted_person, m.ts, "replies", m.room)
        for p in m.mentioned:
            bump(m.person_id, p, m.ts, "mentions", m.room)
    for r in reactions:
        bump(r.person_id, r.target_person, r.ts, "reactions", r.room)
    for c in found:
        people = [p for p in c.persons if p in member_set]
        for i, a in enumerate(people):
            for b in people[i + 1:]:
                cursor = c.valid_from_ms
                while cursor < c.valid_to_ms:
                    week = iso_week(cursor, tz_name)
                    w0, w1 = iso_week_bounds(week, tz_name)
                    piece = overlap_ms(c.valid_from_ms, c.valid_to_ms, w0, w1)
                    row = acc.setdefault((a, b, week), {
                        "replies_ab": 0, "replies_ba": 0, "mentions_ab": 0,
                        "mentions_ba": 0, "reactions_ab": 0, "reactions_ba": 0,
                        "coloc": 0, "rooms": set(), "newest": w0,
                    })
                    row["coloc"] += piece
                    row["newest"] = max(row["newest"], min(c.as_of_ms, w1))
                    cursor = w1
    out: list[Tie] = []
    for (a, b, week), row in sorted(acc.items()):
        w0, w1 = iso_week_bounds(week, tz_name)
        if w0 > as_of_ms:
            continue
        out.append(Tie(
            unit=unit, person_a=a, person_b=b, iso_week=week,
            replies_ab=row["replies_ab"], replies_ba=row["replies_ba"],
            mentions_ab=row["mentions_ab"], mentions_ba=row["mentions_ba"],
            reactions_ab=row["reactions_ab"], reactions_ba=row["reactions_ba"],
            colocation_ms=row["coloc"], rooms=tuple(sorted(row["rooms"])),
            valid_from_ms=w0, valid_to_ms=w1, as_of_ms=row["newest"],
        ))
    return out


# --- response ---------------------------------------------------------------

ResponseOutcome = Literal["open", "responded", "silent", "unanswered", "unknown"]


@dataclass(frozen=True)
class SendFact:
    """One thing Nora sent in a unit room. `named` are the persons its
    structured mentions resolve to; `intent` is the brief's intent, read from
    the send's raw_json (None: «not recorded», never «tell»)."""

    id: int
    room: str
    platform: str
    conversation_id: str
    ts: int
    named: tuple[str, ...] = ()
    intent: str | None = None
    #: The turn that delivered it (raw_json `tag_id`); None is «not recorded»,
    #: and a send with no turn is its own turn (`unquoted_follows`).
    turn: str | None = None


@dataclass(frozen=True)
class ResponseEvidence:
    """One person's response to a send: a reply quoting it, a reaction on
    it, an edit of a reply to it, or `follow`: a message that quoted nothing
    and is read as answering it (`unquoted_follows`)."""

    send_id: int
    person_id: str
    ts: int
    kind: Literal["reply", "reaction", "edit", "follow"]


#: How long after her send an unquoted message answers it without naming her.
FOLLOW_WINDOW_SECONDS = 120.0

#: The words that name her in a message body, matched whole-word and
#: case-insensitively (`nora.tag.is_tagged`'s rules): after the follow window
#: a message answers her only when it names her.
FOLLOW_NAMES: tuple[str, ...] = ("nora",)


@dataclass(frozen=True)
class FollowCandidate:
    """One member's message as `unquoted_follows` reads it. It quotes none of
    her sends (a quote is a `reply`, counted once). `names_nora` is the
    message naming her: a structured mention that resolves to her own account,
    or one of `FOLLOW_NAMES` in its body. `mentions_other` is a structured
    mention that does not resolve to her, or a quote of someone else's
    message (a reply to that person)."""

    id: int
    person_id: str
    room: str
    ts: int
    names_nora: bool
    mentions_other: bool


def unquoted_follows(
    sends: Sequence[SendFact], candidates: Sequence[FollowCandidate], *,
    follow_window_ms: int,
) -> list[ResponseEvidence]:
    """The `follow` evidence: member messages that answered one of her sends
    without quoting it.

    A message answers her when it names her, or when it comes within
    `follow_window_ms` of her latest send in its room and mentions someone
    else not at all. It counts for ONE send: the last send of the turn that
    delivered her latest send before it (a multi-part turn is one thing said;
    a send with no turn is its own turn). Whether the answer is late is
    `responses()`'s call, by its own window. A message between the parts of
    one turn is stamped at the turn's last part, so the window opens at it."""
    by_room: dict[str, list[SendFact]] = {}
    for s in sorted(sends, key=lambda x: (x.ts, x.id)):
        by_room.setdefault(s.room, []).append(s)
    last_of_turn: dict[tuple[str, str], SendFact] = {}
    for room, room_sends in by_room.items():
        for s in room_sends:
            last_of_turn[(room, s.turn or f"send:{s.id}")] = s
    out: list[ResponseEvidence] = []
    for c in sorted(candidates, key=lambda x: x.id):
        room_sends = by_room.get(c.room, [])
        i = bisect_left([s.ts for s in room_sends], c.ts)
        if i == 0:
            continue  # nothing of hers before it in this room
        latest = room_sends[i - 1]
        if not (
            c.names_nora
            or (c.ts - latest.ts <= follow_window_ms and not c.mentions_other)
        ):
            continue
        target = last_of_turn[(c.room, latest.turn or f"send:{latest.id}")]
        out.append(ResponseEvidence(
            send_id=target.id, person_id=c.person_id,
            ts=max(c.ts, target.ts), kind="follow",
        ))
    return out


@dataclass(frozen=True)
class Response:
    unit: str
    message_id: int
    platform: str
    conversation_id: str
    sent_ts: int
    replies: int
    reactions: int
    edits: int
    responders: tuple[str, ...]
    named_persons: tuple[str, ...]
    intent: str | None
    outcome: ResponseOutcome
    valid_from_ms: int
    valid_to_ms: int
    as_of_ms: int
    #: Messages that quoted nothing and are read as answering it
    #: (`unquoted_follows`): counted in `responders`, never in `replies`.
    follows: int = 0

    @property
    def lineage(self) -> tuple[LineageEntry, ...]:
        return message_lineage(room_key(self.platform, self.conversation_id))


def responses(
    unit: str, sends: Sequence[SendFact], evidence: Sequence[ResponseEvidence], *,
    window_ms: int, as_of_ms: int,
    platform_up: Mapping[int, bool],
) -> list[Response]:
    """How people responded to each send, within `window_ms` of it.

    - `open` while the window runs past `as_of_ms`, the newest Record time
      (the window closes when the Record shows the time passed, never the
      wall clock);
    - `responded` when any reply, reaction, edit or unquoted `follow` landed
      in the window;
    - with nothing in a closed window: `silent` when the send expected an
      answer (its brief's intent is `ask`, or it named a person) AND its
      room's platform was proved up for the window (`platform_up[send id]`),
      `unknown` when it expected one and that was not proved (an outage is
      not silence, R-27), `unanswered` when it expected none."""
    by_send: dict[int, list[ResponseEvidence]] = {}
    for e in evidence:
        by_send.setdefault(e.send_id, []).append(e)
    out: list[Response] = []
    for s in sorted(sends, key=lambda x: x.id):
        end = s.ts + window_ms
        inside = [e for e in by_send.get(s.id, ()) if s.ts <= e.ts <= end]
        replies = sum(1 for e in inside if e.kind == "reply")
        reacts = sum(1 for e in inside if e.kind == "reaction")
        edits = sum(1 for e in inside if e.kind == "edit")
        follows = sum(1 for e in inside if e.kind == "follow")
        expects = s.intent == "ask" or bool(s.named)
        outcome: ResponseOutcome
        if inside:
            outcome = "responded"
        elif end > as_of_ms:
            outcome = "open"
        elif not expects:
            outcome = "unanswered"
        elif platform_up.get(s.id, False):
            outcome = "silent"
        else:
            outcome = "unknown"
        out.append(Response(
            unit=unit, message_id=s.id, platform=s.platform,
            conversation_id=s.conversation_id, sent_ts=s.ts,
            replies=replies, reactions=reacts, edits=edits, follows=follows,
            responders=tuple(sorted({e.person_id for e in inside})),
            named_persons=tuple(sorted(set(s.named))), intent=s.intent,
            outcome=outcome, valid_from_ms=s.ts, valid_to_ms=end,
            # Its newest answer, else the send itself: not the clock (the
            # outcome is what moves when the window closes).
            as_of_ms=max((e.ts for e in inside), default=s.ts),
        ))
    return out


# --- healthy sources -------------------------------------------------------


@dataclass(frozen=True)
class HealthRow:
    """One `source_health` row as the proof reads it."""

    source: str
    scope: str
    event: str
    outcome: str
    at_ms: int


def polls_healthy(
    rows: Sequence[HealthRow], *, scope: str, start_ms: int, end_ms: int,
    max_gap_ms: int,
) -> bool:
    """The presence collector is proved healthy for `[start, end]` in one
    circle when it polled that circle throughout — no two consecutive polls,
    nor the span's edges and the nearest poll, further apart than
    `max_gap_ms` — and every one of those polls succeeded. No rows is not
    proof (R-27): it is exactly what an outage looks like."""
    polls = sorted(
        (r for r in rows if r.source == "presence" and r.event == "poll" and r.scope == scope),
        key=lambda r: r.at_ms,
    )
    inside = [r for r in polls if start_ms <= r.at_ms <= end_ms]
    if not inside or any(r.outcome != "ok" for r in inside):
        return False
    before = [r for r in polls if r.at_ms < start_ms]
    after = [r for r in polls if r.at_ms > end_ms]
    edge_start = before[-1].at_ms if before and before[-1].outcome == "ok" else inside[0].at_ms
    edge_end = after[0].at_ms if after and after[0].outcome == "ok" else inside[-1].at_ms
    points = [edge_start, *(r.at_ms for r in inside), edge_end]
    if points[0] - start_ms > max_gap_ms or end_ms - points[-1] > max_gap_ms:
        return False
    return all(b - a <= max_gap_ms for a, b in pairwise(points))


def adapter_up(
    rows: Sequence[HealthRow], *, platform: str, start_ms: int, end_ms: int,
) -> bool:
    """A platform adapter is proved up for `[start, end]` when its newest
    recorded transition at or before `start` is `up` and none inside the span
    leaves `up` (`nora.app.source_health`: a steady state writes nothing, so
    the state holds until the next transition). No transition before the
    span is not proof."""
    mine = sorted(
        (r for r in rows if r.source == platform and r.event == "transition"),
        key=lambda r: r.at_ms,
    )
    before = [r for r in mine if r.at_ms <= start_ms]
    if not before or before[-1].outcome != "up":
        return False
    return all(r.outcome == "up" for r in mine if start_ms < r.at_ms <= end_ms)


@dataclass(frozen=True)
class Deviation:
    """A person's pattern broke: `anomaly` (a stay scored against the
    rhythm) or `silence` (N consecutive silent daybook days). A stimulus, not
    a rule."""

    unit: str
    person_id: str
    kind: Literal["anomaly", "silence"]
    start_ms: int
    end_ms: int
    detail: str = ""

    @property
    def ref(self) -> str:
        return f"deviation|{self.kind}|{self.person_id}|{self.start_ms}"


def silent_runs(
    books: Sequence[Daybook], *, person_id: str, days: int, through_date: str,
) -> list[tuple[str, str]]:
    """Runs of at least `days` consecutive COMPLETED local dates (before
    `through_date`, the date still running) on which `person_id` was silent:
    no daybook row at all counts as silent (nothing held for them that day).
    Returns `(first, last)` dates of each run's first `days`."""
    mine = {b.local_date: b for b in books if b.person_id == person_id}
    if not mine:
        return []
    first = date.fromisoformat(min(mine))
    last = date.fromisoformat(through_date) - timedelta(days=1)
    runs: list[tuple[str, str]] = []
    streak: list[date] = []
    d = first
    while d <= last:
        book = mine.get(d.isoformat())
        if book is None or book.silent:
            streak.append(d)
            if len(streak) == days:
                runs.append((streak[0].isoformat(), streak[-1].isoformat()))
        else:
            streak = []
        d += timedelta(days=1)
    return runs


__all__ = [
    "FIX_RANK",
    "FOLLOW_NAMES",
    "FOLLOW_WINDOW_SECONDS",
    "LOCAL_TZ",
    "NEVER",
    "POLL_RANK",
    "Citation",
    "Colocation",
    "Daybook",
    "Deviation",
    "Event",
    "EventPosition",
    "FixEvent",
    "FoldState",
    "FollowCandidate",
    "HealthRow",
    "Home",
    "LineageEntry",
    "MessageFact",
    "NoRollupBuildError",
    "Outing",
    "PollEvent",
    "ReactionFact",
    "Resolution",
    "Response",
    "ResponseEvidence",
    "SendFact",
    "StaySpan",
    "StoredCorridor",
    "Tie",
    "adapter_up",
    "colocations",
    "daybooks",
    "days_touched",
    "episode_key",
    "iso",
    "iso_week",
    "iso_week_bounds",
    "lineage_json",
    "local_date",
    "local_day_bounds",
    "message_lineage",
    "outings",
    "overlap_ms",
    "polls_healthy",
    "presence_lineage",
    "resolve_by_overlap",
    "responses",
    "room_key",
    "silent_runs",
    "start_position",
    "stay_from_json",
    "stay_to_json",
    "tags_sha256",
    "ties",
    "unquoted_follows",
    "utc_ms",
]
