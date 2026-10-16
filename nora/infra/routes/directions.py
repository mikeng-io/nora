"""Google's computeRoutes envelope turned into one bounded answer. Pure; never raises.

This is `route`'s `nora.infra.transit.arrivals`: no socket, no clock, no
injected collaborator, one public entry point (`describe`), and every shortfall
expressed as a returned value. `nora.infra.routes.client` does the I/O and hands
the payload here.

WHAT THE ROUTES API SENDS (v2 reference). `computeRoutes` returns
`routes[]`, each with `duration` ("358s" — a string of seconds with a trailing
`s`), `distanceMeters` (an int), and `legs[]`. A TRANSIT leg's `steps[]` carry a
`transitDetails` on the riding steps (walking steps carry none), and that object
holds everything a rider needs: `transitLine.nameShort` ("K73"),
`transitLine.vehicle.type` ("BUS" / "TRAM" / "SUBWAY"), `transitLine.agencies[]`
`.name` (the operator), `stopDetails.departureStop.name`, `stopDetails.
departureTime` (RFC 3339 UTC), `headsign` (where the vehicle is signed for) and
`stopCount`.

**Every field rendered here is TYPED** — a duration, a distance, an enum vehicle
type, an RFC 3339 instant, or a name from Google's transit feed (GTFS, the same
kind of published operator table KMB's `dest_en` comes from). So the envelope is
NOT wrapped `untrusted="true"`, exactly as the transit
envelope is not. There is deliberately no free-text field to wrap: `route`'s
field mask does not request `navigationInstruction` (Google-authored step prose)
or place `reviews` (stranger-written), which are the two free-text surfaces this
API can return — so there is nothing here an adversary writes, and nothing is
wrapped. Were a later tool to render either, THAT field would be wrapped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# A journey has a handful of legs; more than this is either a pathological
# result or a place so vague the route is meaningless. Bounds the intake the
# same way `arrivals.MAX_DIRECTIONS` does — a tool result is re-sent on every
# subsequent model call, so its cost is `tokens x calls remaining`.
MAX_STEPS = 8
# Names from the GTFS feed run long ("Tin Shui Chi Station Public Transport
# Interchange"); clipped rather than refused, like `arrivals.MAX_TOWARDS_CHARS`.
MAX_NAME_CHARS = 80


@dataclass(frozen=True)
class TransitLeg:
    """One riding step of a transit journey, already normalised."""

    line: str
    vehicle: str
    agency: str
    departure_stop: str
    departure_time: str
    arrival_stop: str
    arrival_time: str
    headsign: str
    stop_count: int | None


@dataclass(frozen=True)
class Journey:
    """One computed route, already bounded. `reachable` is not `bool(legs)`: a
    DRIVE or WALK route has a duration and a distance and no transit legs at
    all, and that is a complete answer, not an empty one."""

    duration_seconds: int | None
    distance_meters: int | None
    legs: tuple[TransitLeg, ...] = ()
    truncated: bool = False


def _text(value: Any, limit: int = MAX_NAME_CHARS) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _duration_seconds(value: Any) -> int | None:
    """`"358s"` -> 358. The API sends durations as a string of seconds with a
    trailing `s`; anything else is None rather than a guess."""
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        return int(value[:-1])
    except ValueError:
        return None


def _one_leg(details: dict[str, Any]) -> TransitLeg:
    line = details.get("transitLine")
    line = line if isinstance(line, dict) else {}
    vehicle = line.get("vehicle")
    vehicle = vehicle if isinstance(vehicle, dict) else {}
    agencies = line.get("agencies")
    agency = ""
    if isinstance(agencies, list) and agencies and isinstance(agencies[0], dict):
        agency = _text(agencies[0].get("name"))
    stops = details.get("stopDetails")
    stops = stops if isinstance(stops, dict) else {}
    dep = stops.get("departureStop")
    dep = dep if isinstance(dep, dict) else {}
    arr = stops.get("arrivalStop")
    arr = arr if isinstance(arr, dict) else {}
    count = details.get("stopCount")
    return TransitLeg(
        # `nameShort` is "K73"; `name` ("Tin Heng — Yuen Long West") is the
        # fallback when a line has no short name, e.g. the MTR heavy rail.
        line=_text(line.get("nameShort")) or _text(line.get("name")),
        vehicle=_text(vehicle.get("type")),
        agency=agency,
        departure_stop=_text(dep.get("name")),
        departure_time=_text(stops.get("departureTime"), 40),
        arrival_stop=_text(arr.get("name")),
        arrival_time=_text(stops.get("arrivalTime"), 40),
        headsign=_text(details.get("headsign")),
        stop_count=count if isinstance(count, int) else None,
    )


def read_journey(payload: Any) -> Journey | None:
    """The first `route` in a computeRoutes envelope -> one `Journey`, or None
    when the envelope carries no route (Google returns `{}` for an
    origin/destination pair it cannot connect). NEVER raises."""
    routes = payload.get("routes") if isinstance(payload, dict) else None
    if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
        return None
    route = routes[0]
    legs_in: list[dict[str, Any]] = []
    for leg in route.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        for step in leg.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("transitDetails"), dict):
                legs_in.append(step["transitDetails"])
    truncated = len(legs_in) > MAX_STEPS
    legs = tuple(_one_leg(d) for d in legs_in[:MAX_STEPS])
    return Journey(
        duration_seconds=_duration_seconds(route.get("duration")),
        distance_meters=(
            route.get("distanceMeters")
            if isinstance(route.get("distanceMeters"), int) else None
        ),
        legs=legs,
        truncated=truncated,
    )


# ---- the one public entry point ---------------------------------------------

_NOTE_OK_TRANSIT = (
    "Each entry in `legs` is one vehicle to board, in order: `line` is the "
    "route, `vehicle` its kind, `departure_stop`/`departure_time` where and "
    "when to get on, `headsign` where it is signed for. `duration_seconds` is "
    "the whole journey. Times are the ones Google scheduled; say 'about' when "
    "you quote one."
)
_NOTE_OK_PLAIN = (
    "`duration_seconds` and `distance_meters` are the whole journey for this "
    "mode. There are no boarding steps to report: it is one continuous trip. "
    "It is an estimate; say 'about' when you quote it."
)


def describe(
    origin: str,
    destination: str,
    mode: str,
    journey: Journey | None,
    reachable: bool,
) -> dict[str, Any]:
    """One bounded, model-facing answer. NEVER raises.

    Three states, distinct notes: `ok` (a journey Google
    returned), `no_route` (Google was reached and returned no way to connect
    the two points), `unavailable` (Google could not be reached, so nothing is
    known either way). `origin`/`destination` are echoed as Nora resolved them —
    the model's own words are screened for identifiers at the tool seam, not
    here (this module sits below the scanner).

    The envelope is NOT wrapped `untrusted="true"`: see the module docstring —
    every field is typed, and the two free-text surfaces this API can return
    are not in the field mask.
    """
    if not reachable:
        return {
            "status": "unavailable", "origin": origin, "destination": destination,
            "note": "I could not reach the routing service just now, so I don't know "
                    "the way or how long it takes. Say you couldn't check, not that "
                    "there is no route.",
        }
    if journey is None:
        return {
            "status": "no_route", "origin": origin, "destination": destination,
            "note": "Google could not find a way to get between those two places by "
                    "that mode. Say so; that is different from not being able to check.",
        }
    is_transit = mode == "TRANSIT"
    out: dict[str, Any] = {
        "status": "ok", "origin": origin, "destination": destination, "mode": mode,
        "duration_seconds": journey.duration_seconds,
        "distance_meters": journey.distance_meters,
        "note": _NOTE_OK_TRANSIT if is_transit else _NOTE_OK_PLAIN,
    }
    if is_transit:
        out["legs"] = [_render_leg(leg) for leg in journey.legs]
        out["truncated"] = journey.truncated
    return out


def _render_leg(leg: TransitLeg) -> dict[str, Any]:
    """One transit leg as a bounded dict, empty fields dropped so the result
    stays small."""
    rendered: dict[str, Any] = {}
    for key, value in (
        ("line", leg.line), ("vehicle", leg.vehicle), ("agency", leg.agency),
        ("departure_stop", leg.departure_stop), ("departure_time", leg.departure_time),
        ("arrival_stop", leg.arrival_stop), ("arrival_time", leg.arrival_time),
        ("headsign", leg.headsign),
    ):
        if value:
            rendered[key] = value
    if leg.stop_count is not None:
        rendered["stop_count"] = leg.stop_count
    return rendered


def candidates_note(origin: str, destination: str, which: Sequence[tuple[str, str]]) -> str:
    """The note when a place did not resolve to one point. `which` is a list of
    (label, detail) — one per place that missed — so the model can ask rather
    than guess (ambiguity is an answer)."""
    parts = "; ".join(f"{label}: {detail}" for label, detail in which)
    return (
        f"I could not pin down a single place for this route ({parts}). Ask which "
        "place they mean rather than guessing, and do not send a route until you know."
    )
