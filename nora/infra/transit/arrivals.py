"""Two operators' wire formats turned into one bounded answer. Pure; never raises.

This is `transit`'s `nora.agent.arithmetic`: no socket, no clock, no injected
collaborator, one public entry point (`describe`), and every failure expressed
as a returned value. `nora.infra.transit.client` does the I/O and hands the
payloads here.

WHAT THE TWO OPERATORS ACTUALLY SEND, read out of their published specifications
(KMB API Specifications v1.05; MTR Bus API Specification v1.13 and Data
Dictionary v1.19) and checked against the live endpoints.

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
`"yyyy-MM-dd HH:mm"` its own data dictionary claims. Three further traps:

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

WHAT COMES BACK. Without a place named, departures from the START of each
direction — neither ETA response names its stops in a way a person could use
(KMB gives `seq: 7`, MTR gives `busStopId: "K73-D070"`), so the top of the
route is the one quantity that is well defined without knowing where anybody
stands, and the note says so. WITH a place named, `nora.infra.transit.places`
resolves it to the operator's own stop ids (that module is the scope boundary:
places only, never people) and the departures are AT
that stop: KMB via the per-stop ETA endpoint, MTR by joining `busStopId`
against the stops CSV's `STATION_ID` — where the `arrivalTimeInSecond` that is
a `108000` sentinel at an origin is REAL at every later stop, and is the
number a person waiting there needs (`_stop_seconds` below).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from nora.domain.blocks import wrap_outside_text

from .places import PlaceResolution

# Fixed offset, for the reason `nora.agent.tools` gives: Hong Kong has observed
# no DST since 1979, and a tz database lookup would differ between the container
# and a developer machine.
HKT = timezone(timedelta(hours=8), "HKT")

# ---- the ceilings, as constants ----------------------------------------------
#
# A tool result is re-sent on EVERY subsequent model call in the turn, so its
# real cost is `tokens x calls remaining` against `total_tokens = 64000`. These
# are sized against the two shapes actually on the wire: 68X has 2 directions,
# K73 has 2, and a KMB trunk route with peak variants has at most 2 per service
# type. Four directions x three departures is roughly 700 characters of JSON —
# the same order as `lookup_person`, an order below `load_transcript(25)`.
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


def _stop_seconds(bus: Mapping[str, Any]) -> int | None:
    """How many seconds until this bus is AT a named stop.

    The ARRIVAL, when it is real: at every stop past the origin
    `arrivalTimeInSecond` is the number a person standing there needs, and at
    a terminus it is the only number there is (`departureTimeInSecond` reads
    `"0"` for a bus that ends its run). At an origin the arrival is the
    thirty-hour `108000` sentinel, which the horizon check screens out, and
    the departure is the answer — exactly as it is in origin mode.
    """
    arrival = _seconds(bus.get("arrivalTimeInSecond"))
    if arrival is not None and arrival <= MAX_HORIZON_SECONDS:
        return arrival
    return _seconds(bus.get("departureTimeInSecond"))


def read_mtr(
    payload: Any,
    towards: Mapping[str, str],
    at_stops: frozenset[str] | None = None,
) -> Answer:
    """A `getSchedule` envelope -> one `Direction` per `lineRef`.

    `towards` comes from the route CSV (`nora.infra.transit.catalogue`): the
    response itself names no destination, only `lineRef: "K73_YLW"`.

    In origin mode (`at_stops=None`) the origin of a direction is the FIRST
    `busStop` in document order carrying that `lineRef`, and its
    `departureTimeInSecond` is the only usable number there — its
    `arrivalTimeInSecond` is the `108000` sentinel. With `at_stops` given (the
    place resolver's `STATION_ID`s, which are the same strings as
    `busStopId` — the join the resolver stands on), the stop read per
    direction is the NAMED one instead, and `_stop_seconds` picks arrival
    over departure. A resolved stop absent from the envelope contributes no
    direction — the response only carries stops with something to report —
    and that absence
    lands as `nothing_found` at the stop, never as an invented time.
    """
    if not isinstance(payload, dict):
        return Answer(operator="MTR", reachable=False)
    stops = [stop for stop in payload.get("busStop") or [] if isinstance(stop, dict)]
    now = parse_mtr_clock(payload.get("routeStatusTime"))
    if now is None:
        return Answer(operator="MTR", reachable=True)

    origins: dict[str, dict[str, Any]] = {}
    for stop in stops:
        if at_stops is not None and _text(stop.get("busStopId")) not in at_stops:
            continue
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
            seconds = (
                _stop_seconds(bus) if at_stops is not None
                else _seconds(bus.get("departureTimeInSecond"))
            )
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

# The sentences. They are written out here, side by side, because the defect
# this tool exists to avoid is exactly their collapse into one: 「而家冇車」
# said about a route that has never existed is confident, wrong, and reads
# identically to a true answer — and with a place in play the same collapse has
# a second axis, which the `_NEAR_*` suffixes keep apart (R-27b).
_NOTE_OK_LIVE = "These are live times for right now, not for a later bus."
_NOTE_OK_FROM_START = (
    " They leave from the START of each direction, so a bus reaches a stop "
    "further along later than that."
)
_NOTE_OK_NO_STOP = (
    " I do not know which stop anyone is at, so do not name one. If they NAMED a "
    "place, call again with it as `near` for times at that stop."
)
_NOTE_AT_STOP = (
    "These are the next buses AT the stop in `near`, one entry per direction that "
    "calls there, live for right now."
)
_NOTE_SCHEDULED = (
    " A departure with `scheduled` true came off the timetable, not off a bus "
    "anybody can see: say so if you quote it."
)
_NOTE_STOP_NO_SERVICE = (
    "That route is real and does stop at the place in `near`, but nothing is "
    "showing there right now; the last buses have most likely gone. Say that, "
    "and do not say the route skips that stop or invent a time."
)
# The place-axis outcomes, appended to whichever base note the route answer
# earned. Each is a DIFFERENT claim, and «no stop by that name» said about a
# place the route merely skips is the same wrong-confident collapse R-27
# exists for, one level down.
_NEAR_SUFFIX = {
    "not_on_route": (
        " About the place they named: it is a real stop, but this route does "
        "not go there. Say exactly that; do not say the place does not exist."
    ),
    "not_found": (
        " About the place they named: no bus stop in either operator's data "
        "goes by that name. Say you do not know that stop and ask them to "
        "check the name, without guessing one."
    ),
    "ambiguous": (
        " About the place they named: it matches stops in more than one "
        "place, listed in `near.could_be`. Ask which one they mean."
    ),
    "unavailable": (
        " About the place they named: I could not read the stop data just "
        "now, so I cannot say whether this route goes there. Say so if it "
        "matters."
    ),
}
_NOTE_NO_SUCH_ROUTE = (
    "Neither KMB nor MTR lists a route with that number. Say you cannot find that "
    "route and ask them to check it. Do NOT say there are no buses: that is a "
    "different claim, and false about a route that does not exist."
)
_NOTE_NO_SERVICE = (
    "That route is real, and nothing is showing on it right now; the last buses "
    "have most likely gone for the night. Say that, and do not say the route "
    "does not exist or invent a time."
)
_NOTE_UNAVAILABLE = (
    "I could not reach the bus data just now, so I know nothing about this route "
    "either way. Say you cannot check, never that there are no buses or that "
    "the route does not exist."
)


def _ok_note(near: PlaceResolution | None, directions: Sequence[dict[str, Any]]) -> str:
    """How to report an `ok` (ADR 0015 §3), built per field present: the
    `scheduled` clause only when a quoted departure came off a timetable, the
    from-the-start clauses only when the times are not at a named stop."""
    if near is not None and near.status == "ok":
        note = _NOTE_AT_STOP
    elif near is None:
        note = _NOTE_OK_LIVE + _NOTE_OK_FROM_START + _NOTE_OK_NO_STOP
    else:
        note = _with_near(_NOTE_OK_LIVE + _NOTE_OK_FROM_START, near)
    if any(dep.get("scheduled") for d in directions for dep in d.get("departures", ())):
        note += _NOTE_SCHEDULED
    return note


def describe(
    route: str,
    facts_complete: bool,
    known_operators: Sequence[str],
    answers: Sequence[Answer],
    near: PlaceResolution | None = None,
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

    The envelope is NOT wrapped `untrusted="true"`: every field
    below is a number, a boolean, an RFC 3339 instant or a destination from a
    published route table — this is closer to `listGroups` than to a web page.
    `operator_note` is the exception: it is an
    operator-authored sentence, so it is escaped and wrapped even though its
    envelope is typed.

    `near`, when a place was asked about, adds one more dimension WITHOUT
    adding states: the three route statuses stay what they were, the place
    outcome rides in a `near` object plus a sentence on the note, and the two
    axes never blur — `nothing_found` at a resolved stop says the STOP is
    quiet, not that the place or the route is unreal (R-27b).
    """
    # `route` is echoed only when it IS one; `nora.infra.transit.client` passes
    # an empty string for a value that never matched a route number, and this
    # drops the key rather than reflecting the model's own string back at it.
    # `near.asked` is the model's word too — rendered here, SCREENED at the
    # tool layer (`nora.agent.tools`), which drops it if it is an identifier;
    # this module sits below `nora.agent` and cannot reach the scanner.
    echo: dict[str, Any] = {"route": route} if route else {}
    if not known_operators:
        if not facts_complete:
            return {"status": "unavailable", **echo, "known_route": None,
                    "note": _NOTE_UNAVAILABLE}
        return {"status": "nothing_found", **echo, "known_route": False,
                "note": _NOTE_NO_SUCH_ROUTE}

    at_stop = near is not None and near.status == "ok"
    answered = [a for a in answers if a.reachable]
    if not answered:
        return {"status": "unavailable", **echo, "known_route": True,
                **_near_fields(near), "note": _with_near(_NOTE_UNAVAILABLE, near)}

    with_service = [a for a in answered if a.directions]
    remark = _pick_remark(a.remark for a in answered)
    if not with_service:
        out: dict[str, Any] = {
            "status": "nothing_found", **echo, "known_route": True,
            "operator": "/".join(a.operator for a in answered),
            **_near_fields(near),
            "note": (_NOTE_STOP_NO_SERVICE if at_stop
                     else _with_near(_NOTE_NO_SERVICE, near)),
        }
        if remark:
            out["operator_note"] = wrap_outside_text(
                "transit_remark", answered[0].operator, remark,
            )
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
            # published by both operators today (none is, as of
            # the published route tables), so paying for this field on every answer would be paying
            # for a case that has never occurred.
            if len(with_service) > 1:
                rendered["operator"] = answer.operator
            directions.append(rendered)

    note = _ok_note(near, directions[:MAX_DIRECTIONS])
    result: dict[str, Any] = {
        "status": "ok", **echo, "known_route": True,
        "operator": "/".join(a.operator for a in with_service),
        "as_of": stamps[0] if stamps else None,
        **_near_fields(near),
        "directions": directions[:MAX_DIRECTIONS],
        "truncated": any(a.truncated for a in with_service) or len(directions) > MAX_DIRECTIONS,
        "note": note,
    }
    if remark:
        result["operator_note"] = wrap_outside_text(
            "transit_remark", with_service[0].operator, remark,
        )
    return result


def _near_fields(near: PlaceResolution | None) -> dict[str, Any]:
    """The `near` object: outcome, echo, and — only when they exist — the
    resolved name or the candidates. Stop IDS never appear: they are Nora's
    working state, not something a person can stand at by name (DEC-24)."""
    if near is None:
        return {}
    rendered: dict[str, Any] = {"status": near.status, "asked": near.asked}
    if near.place:
        rendered["stop"] = near.place
    if near.could_be:
        rendered["could_be"] = list(near.could_be)
    return {"near": rendered}


def _with_near(note: str, near: PlaceResolution | None) -> str:
    """The route note, plus the place-axis sentence when the place did not
    resolve. A resolved place needs no suffix — its notes are whole."""
    if near is None or near.status == "ok":
        return note
    return note + _NEAR_SUFFIX.get(near.status, "")


