"""Two operators' wire formats turned into one bounded answer. Pure; never raises.

This is `transit`'s `nora.agent.arithmetic`: no socket, no clock, no injected
collaborator, one public entry point (`describe`), and every failure expressed
as a returned value. `nora.infra.transit.client` does the I/O and hands the
payloads here.

WHAT THE TWO OPERATORS ACTUALLY SEND, read out of their published specifications
(KMB API Specifications v1.05, 23 Oct 2024; MTR Bus API Specification v1.13 and
Data Dictionary v1.19, both 1 Mar 2026) and checked against the live endpoints on
2026-08-06.

**KMB** (`GET /v1/transport/kmb/route-eta/{route}/{service_type}`) returns one
row per (stop, direction, arrival): `dir` `"O"`/`"I"`, `seq` the stop's position
along the route, `eta_seq` 1-3 for the next three buses at that stop, and `eta`
as a real RFC 3339 instant with a `+08:00` offset. **`eta` is nullable**, and the
spec's own sample shows why — an N-route out of season carries `eta: null` with
`rmk_en: "This route only operates on Sundays and Public Holidays"`. That remark
is the only place the reason exists, so it is kept, and it is the one field here
that is an operator-authored SENTENCE.

**MTR** (`POST .../mtr/bus/getSchedule`) sends no absolute time for a bus at all.
It sends `departureTimeInSecond` as a *string*, relative to a `routeStatusTime`
of `"2026/08/06 23:15"` — which is not RFC 3339, and is not even the
`"yyyy-MM-dd HH:mm"` its own data dictionary claims. Three further traps, all
measured:

* **`arrivalTimeInSecond` is `"108000"` at every origin stop.** Thirty hours is
  a sentinel, not an ETA; the dictionary says only "no arrival time text for
  first stops of non-circular route". A client that believed it would report a
  bus arriving tomorrow morning.
* **`departureTimeInSecond` goes negative** (`"-103"`) for a bus that has left.
* **`isScheduled` is `"1"` for a TIMETABLE entry and `"0"` for a bus the system
  can see.** 「仲有 9 分鐘」 read off a schedule and 「仲有 9 分鐘」 read off a
  tracked bus are different claims, and the group cannot tell them apart unless
  the difference reaches the model. KMB says the same thing in words —
  `rmk_en: "Scheduled Bus"` — so both fold into one boolean.

**`status` is not a success flag** and nothing here reads it. MTR's dictionary
defines `"1" = normal` and `"0" = error or alert`; the live endpoint returns
`"0"` for a route with 27 stops of good data AND for a route that does not
exist. It carries no information in either direction.

WHAT COMES BACK, and why it is departures from the START of a route. `transit`
takes a route number and nothing else — there is no stop argument, because
resolving 「我喺天恆」 to a stop id is the place resolver, which `design/14` §5.2
puts behind a scope boundary and `design/14` §6 schedules after this. Neither
operator's ETA response names its stops in a way a person could use either: KMB
gives `seq: 7` and MTR gives `busStopId: "K73-D070"`. So the one quantity that
is both well defined and sayable out loud is when the next buses leave the top
of each direction, and the note says so rather than letting her imply she knows
where anybody is standing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from nora.domain.prompt import escape

# Fixed offset, for the reason `nora.agent.tools` gives: Hong Kong has observed
# no DST since 1979, and a tz database lookup would differ between the container
# and a developer machine.
HKT = timezone(timedelta(hours=8), "HKT")

# ---- the ceilings, as constants (design/14 §4) -------------------------------
#
# A tool result is re-sent on EVERY subsequent model call in the turn, so its
# real cost is `tokens x calls remaining` against `total_tokens = 64000`. These
# are sized against the two shapes actually on the wire: 68X has 2 directions,
# K73 has 2, and a KMB trunk route with peak variants has at most 2 per service
# type. Four directions x three departures is roughly 700 characters of JSON —
# the same order as `lookup_person`, an order below `read_buffer(25)`.
MAX_DIRECTIONS = 4
MAX_DEPARTURES = 3
# One operator sentence, long enough for the longest `rmk_en` seen in KMB's own
# specification ("This route only operates on Sundays and Public Holidays", 54
# characters) with room to spare.
MAX_NOTE_CHARS = 120
# `dest_en` runs to 33 characters ("HUNG SHUI KIU (HUNG FUK ESTATE)") and MTR's
# longest CSV name to 82. Truncated rather than refused: a clipped place name is
# still the right direction.
MAX_TOWARDS_CHARS = 60

# Anything further out than this is a sentinel or a fault, not a bus. MTR's
# `108000` origin marker is thirty hours; the longest real headway on either
# network is well under two.
MAX_HORIZON_SECONDS = 3 * 3600


@dataclass(frozen=True)
class Departure:
    at: str                 # RFC 3339, always with the +08:00 offset
    in_minutes: int
    scheduled: bool         # from a timetable, not from a bus anyone can see


@dataclass(frozen=True)
class Direction:
    towards: str
    departures: tuple[Departure, ...]


@dataclass(frozen=True)
class Answer:
    """One operator's reply, already normalised. `reachable` is not `bool(directions)`."""

    operator: str
    reachable: bool
    as_of: str | None = None
    directions: tuple[Direction, ...] = ()
    remark: str = ""
    truncated: bool = False


# ---- reading each wire format ------------------------------------------------

def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _seconds(value: Any) -> int | None:
    """A count of seconds that the operator sent as a string. None if it is not one."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _instant(value: Any) -> datetime | None:
    """An RFC 3339 instant from KMB, or None.

    `fromisoformat` on 3.11+ reads the `+08:00` offset and a trailing `Z`. A row
    whose `eta` is null lands here as None and is dropped by the caller — with
    its `rmk_en` kept, because that string is the only statement of why.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=HKT)


def parse_mtr_clock(value: Any) -> datetime | None:
    """MTR's `routeStatusTime` — `"2026/08/06 23:15"` — as an instant.

    Both separators are accepted because the two sources disagree: the live
    endpoint sends slashes and MTR's own data dictionary documents
    `"yyyy-MM-dd HH:mm"`. Neither carries an offset, so HKT is attached — the
    endpoint serves one city.
    """
    text = _text(value).replace("/", "-")
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=HKT)
    except ValueError:
        return None


def _eta_seq(row: Mapping[str, Any]) -> int:
    """`eta_seq` is 1-3 for the next three buses at a stop. 99 sorts a row that
    has none of it last rather than crashing the sort."""
    value = row.get("eta_seq")
    return value if isinstance(value, int) else 99


def _departure(at: datetime, now: datetime, *, scheduled: bool) -> Departure | None:
    delta = (at - now).total_seconds()
    if delta < -30 or delta > MAX_HORIZON_SECONDS:
        # A bus thirty seconds in the past is «now»; one that left two minutes
        # ago is gone, and offering it is the tool being confidently wrong.
        return None
    return Departure(
        at=at.isoformat(timespec="seconds"),
        in_minutes=max(0, round(delta / 60)),
        scheduled=scheduled,
    )


def read_kmb(payload: Any) -> Answer:
    """A `RouteETA` envelope -> one `Direction` per `dir`, from the first stop.

    "First stop" is the LOWEST `seq` present in this response rather than a
    hardcoded 1: a direction whose terminus is reporting nothing still has a
    well-defined top, and 68X's `seq: 7` row at 23:31 must not be mistaken for
    a departure from the origin at 23:25.
    """
    if not isinstance(payload, dict):
        return Answer(operator="KMB", reachable=False)
    rows = [row for row in payload.get("data") or [] if isinstance(row, dict)]
    now = _instant(payload.get("generated_timestamp"))
    if now is None:
        now = _instant(next((r.get("data_timestamp") for r in rows), None))
    if now is None:
        return Answer(operator="KMB", reachable=True)

    by_direction: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_direction.setdefault(_text(row.get("dir")) or "?", []).append(row)

    directions: list[Direction] = []
    remarks: list[str] = []
    truncated = len(by_direction) > MAX_DIRECTIONS
    for rows_for_direction in list(by_direction.values())[:MAX_DIRECTIONS]:
        seqs = [r["seq"] for r in rows_for_direction if isinstance(r.get("seq"), int)]
        first_seq = min(seqs) if seqs else None
        at_origin = [r for r in rows_for_direction if r.get("seq") == first_seq]
        at_origin.sort(key=_eta_seq)
        departures: list[Departure] = []
        for row in at_origin:
            instant = _instant(row.get("eta"))
            if instant is None:
                # `eta: null` — the row exists only to carry its reason.
                remarks.append(_text(row.get("rmk_en")))
                continue
            # KMB says in words what MTR says in a flag. "Scheduled Bus" is the
            # timetable; an empty remark is a bus the system is tracking.
            departure = _departure(
                instant, now, scheduled=_text(row.get("rmk_en")).lower() == "scheduled bus",
            )
            if departure is not None:
                departures.append(departure)
        truncated = truncated or len(departures) > MAX_DEPARTURES
        if departures:
            directions.append(Direction(
                towards=_text(at_origin[0].get("dest_en"))[:MAX_TOWARDS_CHARS] or "?",
                departures=tuple(departures[:MAX_DEPARTURES]),
            ))
    return Answer(
        operator="KMB", reachable=True,
        as_of=now.isoformat(timespec="seconds"),
        directions=tuple(directions),
        remark=_pick_remark(remarks),
        truncated=truncated,
    )


def read_mtr(payload: Any, towards: Mapping[str, str]) -> Answer:
    """A `getSchedule` envelope -> one `Direction` per `lineRef`.

    `towards` comes from the route CSV (`nora.infra.transit.catalogue`): the
    response itself names no destination, only `lineRef: "K73_YLW"`.

    The origin of a direction is the FIRST `busStop` in document order carrying
    that `lineRef`, and its `departureTimeInSecond` is the only usable number
    there — its `arrivalTimeInSecond` is the `108000` sentinel.
    """
    if not isinstance(payload, dict):
        return Answer(operator="MTR", reachable=False)
    stops = [stop for stop in payload.get("busStop") or [] if isinstance(stop, dict)]
    now = parse_mtr_clock(payload.get("routeStatusTime"))
    if now is None:
        return Answer(operator="MTR", reachable=True)

    origins: dict[str, dict[str, Any]] = {}
    for stop in stops:
        for bus in stop.get("bus") or []:
            if not isinstance(bus, dict):
                continue
            origins.setdefault(_text(bus.get("lineRef")), stop)

    directions: list[Direction] = []
    truncated = len(origins) > MAX_DIRECTIONS
    for line_ref, stop in list(origins.items())[:MAX_DIRECTIONS]:
        if _text(stop.get("isSuspended")) == "1":
            continue
        departures: list[Departure] = []
        for bus in stop.get("bus") or []:
            if not isinstance(bus, dict) or _text(bus.get("lineRef")) != line_ref:
                continue
            seconds = _seconds(bus.get("departureTimeInSecond"))
            if seconds is None:
                continue
            departure = _departure(
                now + timedelta(seconds=seconds), now,
                scheduled=_text(bus.get("isScheduled")) != "0",
            )
            if departure is not None:
                departures.append(departure)
        departures.sort(key=lambda d: d.in_minutes)
        truncated = truncated or len(departures) > MAX_DEPARTURES
        if departures:
            directions.append(Direction(
                towards=(towards.get(line_ref) or line_ref)[:MAX_TOWARDS_CHARS],
                departures=tuple(departures[:MAX_DEPARTURES]),
            ))
    return Answer(
        operator="MTR", reachable=True,
        as_of=now.isoformat(timespec="seconds"),
        directions=tuple(directions),
        truncated=truncated,
    )


def _pick_remark(remarks: Iterable[str]) -> str:
    """The longest distinct remark, which is the one that says something.

    KMB repeats one `rmk_en` across every null row of a direction, and the
    informative one ("This route only operates on Sundays and Public Holidays")
    is always longer than the boilerplate.
    """
    candidates = sorted({r for r in remarks if r}, key=len, reverse=True)
    return candidates[0][:MAX_NOTE_CHARS] if candidates else ""


# ---- the one public entry point ---------------------------------------------

# The three sentences. They are written out here, side by side, because the
# defect this tool exists to avoid is exactly their collapse into one: 「而家冇
# 車」 said about a route that has never existed is confident, wrong, and reads
# identically to a true answer.
_NOTE_OK = (
    "These are departures from the START of each direction — a bus reaching a stop "
    "further along takes longer, and I do not know which stop anyone is at, so do "
    "not name one. `scheduled` true means this came off the timetable, not off a "
    "bus anybody can see; say so if you quote it."
)
_NOTE_NO_SUCH_ROUTE = (
    "Neither KMB nor MTR lists a route with that number. Say you cannot find that "
    "route and ask them to check it — do NOT say there are no buses, which is a "
    "different thing and would be a lie about a route that does not exist."
)
_NOTE_NO_SERVICE = (
    "That route is real, and right now nothing is showing on it — the last buses "
    "have most likely gone for the night. Say that. Do not say the route does not "
    "exist, and do not invent a time."
)
_NOTE_UNAVAILABLE = (
    "I could not reach the bus data just now, so I know nothing about this route "
    "either way. Say you cannot check — never that there are no buses, and never "
    "that the route does not exist."
)


def describe(
    route: str,
    facts_complete: bool,
    known_operators: Sequence[str],
    answers: Sequence[Answer],
) -> dict[str, Any]:
    """One bounded, model-facing answer. NEVER raises.

    `ok` / `nothing_found` / `unavailable`, and the ordering of the branches is
    the whole point (R-27):

    * a route no reachable catalogue lists, with a catalogue missing, is
      `unavailable` — there is nothing honest to say about it at all;
    * a route no catalogue lists, with every catalogue read, is `nothing_found`
      and the note says the ROUTE is unknown;
    * a known route whose operator could not be reached is `unavailable`;
    * a known route with nothing running is `nothing_found` and the note says
      the SERVICE is not running.

    The envelope is NOT wrapped `untrusted="true"` (design/14 §2.1): every field
    below is a number, a boolean, an RFC 3339 instant or a destination from a
    published route table — this is closer to `listGroups` than to a web page.
    `operator_note` is the exception the same paragraph names: it is an
    operator-authored sentence, so it is escaped and wrapped even though its
    envelope is typed.
    """
    # `route` is echoed only when it IS one; `nora.infra.transit.client` passes
    # an empty string for a value that never matched a route number, and this
    # drops the key rather than reflecting the model's own string back at it.
    echo: dict[str, Any] = {"route": route} if route else {}
    if not known_operators:
        if not facts_complete:
            return {"status": "unavailable", **echo, "known_route": None,
                    "note": _NOTE_UNAVAILABLE}
        return {"status": "nothing_found", **echo, "known_route": False,
                "note": _NOTE_NO_SUCH_ROUTE}

    answered = [a for a in answers if a.reachable]
    if not answered:
        return {"status": "unavailable", **echo, "known_route": True,
                "note": _NOTE_UNAVAILABLE}

    with_service = [a for a in answered if a.directions]
    remark = _pick_remark(a.remark for a in answered)
    if not with_service:
        out: dict[str, Any] = {
            "status": "nothing_found", **echo, "known_route": True,
            "operator": "/".join(a.operator for a in answered),
            "note": _NOTE_NO_SERVICE,
        }
        if remark:
            out["operator_note"] = _wrap(answered[0].operator, remark)
        return out

    stamps = sorted(a.as_of for a in with_service if a.as_of)
    directions: list[dict[str, Any]] = []
    for answer in with_service:
        for direction in answer.directions:
            rendered: dict[str, Any] = {
                "towards": direction.towards,
                "departures": [
                    {"at": d.at, "in_minutes": d.in_minutes, "scheduled": d.scheduled}
                    for d in direction.departures
                ],
            }
            # Named per direction ONLY when it is ambiguous. No route number is
            # published by both operators today (measured: 0 of 791 KMB against
            # 26 MTR), so paying for this field on every answer would be paying
            # for a case that has never occurred.
            if len(with_service) > 1:
                rendered["operator"] = answer.operator
            directions.append(rendered)

    result: dict[str, Any] = {
        "status": "ok", **echo, "known_route": True,
        "operator": "/".join(a.operator for a in with_service),
        "as_of": stamps[0] if stamps else None,
        "directions": directions[:MAX_DIRECTIONS],
        "truncated": any(a.truncated for a in with_service) or len(directions) > MAX_DIRECTIONS,
        "note": _NOTE_OK,
    }
    if remark:
        result["operator_note"] = _wrap(with_service[0].operator, remark)
    return result


def _wrap(operator: str, remark: str) -> str:
    """The one free-text field, labelled and escaped (design/14 §2.1).

    Escaped with the same helper R-13 uses on group text, because the wrapper is
    XML-shaped and a remark containing `</untrusted>` would otherwise close it.
    Nothing else in this module is wrapped, and that is deliberate rather than
    an omission: a destination name from a published route table is not a
    sentence an adversary writes.
    """
    return f'<untrusted source="{escape(operator)}">{escape(remark)}</untrusted>'
