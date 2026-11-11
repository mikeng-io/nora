from __future__ import annotations

import logging
from types import SimpleNamespace

from nora.agent.security import _names_a_person, scan_identifiers
from nora.agent.security import looks_like_coordinate as _looks_like_coordinate
from nora.agent.toolkit.results import _record
from nora.agent.whereis import (
    DisclosureGuard,
)
from nora.location.adapters.tools.shared import _WEATHER_OFF, _weather_result

log = logging.getLogger("nora.tools")


def _make_reverse_geocode(deps):
    content_checks = deps.content_checks
    geocoder = deps.geocoder
    metrics = deps.metrics
    _disclosed_live_addresses = deps._disclosed_live_addresses

    async def reverse_geocode(lat: float, lng: float) -> dict:
        """What place is this coordinate? Call this when a `where_been` or
        `where_is` result carried `lat`/`lng`.

        Pass them exactly as that result gave them, never rounded, and never a
        coordinate from anywhere else (one typed in chat is refused).

        Returns `status`: ok, not_found or unavailable, with a `note` on how to
        report it.

        Args:
            lat: The latitude, exactly as a where_been/where_is result gave it.
            lng: The longitude, exactly as a where_been/where_is result gave it.
        """
        if geocoder is None:
            # No geocoder wired (offline/eval compositions, or `[presence]`
            # with neither backend keyed). `unavailable`, never `not_found` —
            # a wiring gap must look like «I could not check» (R-5).
            _record(metrics, "reverse_geocode", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't look up coordinates right now. Say you couldn't "
                "check, and do not guess a place.",
            }
        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except (TypeError, ValueError):
            _record(metrics, "reverse_geocode", "unavailable", reason="args")
            return {
                "status": "unavailable",
                "note": "Those aren't coordinates I can read. Ask the presence "
                "tools again for the spot.",
            }
        address = await geocoder.address_for(lat=lat_f, lng=lng_f)
        if not address:
            # The port answers None on a real miss AND on a backend failure —
            # both are «no address found», never a crash (the port's own
            # contract). `not_found` is the honest shape for both.
            _record(metrics, "reverse_geocode", "not_found")
            return {
                "status": "not_found",
                "note": "I couldn't pin a place to that coordinate. Say so, and do not guess one.",
            }
        _record(metrics, "reverse_geocode", "ok")
        # R-57: a street address resolved THIS turn is a STORED POSITION `route` must
        # screen for — an untrusted room cannot launder a live coordinate into a route
        # origin through this tool.
        _disclosed_live_addresses.add(address.strip().casefold())
        return {
            "status": "ok",
            "address": address,
            "lat": lat_f,
            "lng": lng_f,
            "note": "Say the address in your reply."
            + (" The coordinate never goes in it." if content_checks else ""),
        }

    return reverse_geocode


def _make_weather(deps):
    metrics = deps.metrics
    weather_source = deps.weather_source

    async def weather(place: str | None = None, forecast: bool = False) -> dict:
        """The weather in Hong Kong: current conditions, any warnings, the outlook.
        Call this for anything about HK weather.

        One call gives temperatures across HK, humidity, any warnings in force and
        today's outlook. Set `forecast=True` when they ask about the days ahead.

        Returns `status`: ok or unavailable, with a `note` on how to report it.

        Args:
            place: the district or place they asked about, in their words (optional).
            forecast: True to add the 9-day forecast; default False.
        """
        if weather_source is None:
            _record(metrics, "weather", "unavailable")
            return dict(_WEATHER_OFF)
        try:
            obs = await weather_source.current(place=place, forecast=forecast)
        except Exception:  # a tool never raises into the loop (R-5)
            log.exception("weather failed")
            obs = None
        if obs is None:
            _record(metrics, "weather", "unavailable")
            return dict(_WEATHER_OFF)
        _record(metrics, "weather", "ok", forecast=forecast, warned=bool(obs.warnings))
        return _weather_result(obs)

    return weather


def _make_parking_status(deps):
    metrics = deps.metrics
    parking_source = deps.parking_source

    async def parking_status(name: str) -> dict:
        """How many spaces are free at a named car park, RIGHT NOW. Call this
        when somebody asks whether a car park or venue has parking.

        It reads the live vacancy feed, which web_search cannot. Give the venue or
        car park the way they named it.

        Returns `status`: ok or unavailable, with a `note` on how to report it.

        Args:
            name: the venue or car park name, in the group's words.
        """
        if parking_source is None:
            _record(metrics, "parking_status", "unavailable")
            return {
                "status": "unavailable",
                "note": "Parking isn't set up here. Say you couldn't check, "
                "and don't guess a number.",
            }
        try:
            data = await parking_source.vacancy(name=name)
        except Exception:  # a tool never raises into the loop (R-5)
            log.exception("parking_status failed")
            data = None
        if data is None:
            # Distinguish "no such car park" from "couldn't check" — the
            # port collapses both to None, so the tool cannot tell them
            # apart and must NOT invent either; this is the honest
            # `unavailable`, while `nothing_found` stays unreachable
            # through this provider. (A richer provider MAY distinguish.)
            _record(metrics, "parking_status", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't find that car park in the feed, or couldn't "
                "reach it. Say you couldn't check, never that there are "
                "no spaces, and don't guess a number or a nearby car "
                "park.",
            }
        _record(metrics, "parking_status", "ok")
        # ADR 0015 §3: how to report a count rides with it, per field present.
        note = []
        if data.get("lastupdate"):
            note.append(
                "Say when the count was updated (lastupdate): a count "
                "without its time sounds like right now, which you do "
                "not know."
            )
        if "parking_id" in data:
            note.append("Never read parking_id aloud.")
        note.append("Never invent a number.")
        return {"status": "ok", **data, "note": " ".join(note)}

    return parking_status


def _make__find_parking_ok_note(deps):
    def _find_parking_ok_note(car_parks: list[dict], *, street: bool) -> str:
        """How to report a `find_parking` `ok` (ADR 0015 §3), per field present:
        the vacancy clauses only when an entry has (or lacks) a live count, the
        street clause only when `street_parking` came back."""
        parts = ["Say each car park's distance (distance_m); don't rank them silently."]
        if any("vacancy" in c for c in car_parks):
            parts.append(
                "An entry with vacancy is the live answer: quote it with "
                "its lastupdate time, and don't call parking_status for "
                "that lot."
            )
        if any("vacancy" not in c for c in car_parks):
            parts.append(
                "An entry without vacancy has no live count here (private "
                "lots usually don't); parking_status can check one only "
                "if they name it."
            )
        if street:
            parts.append(
                "street_parking counts the metered bays a short walk away "
                "and how many are vacant now, by street."
            )
        return " ".join(parts)

    return _find_parking_ok_note


def _make_find_parking(deps):
    async def find_parking(place: str = "") -> dict:
        """Where the nearest car parks are, around a spot the group names. Call
        this when somebody wants parking near somewhere.

        Give `place` in their words; empty means where the asker is. Returns up
        to five, with distance and any live vacancy.

        Returns `status`: ok, ambiguous, not_found, nothing_found or unavailable,
        with a `note` on how to report it.

        Args:
            place: the spot to search around, in their words; empty means the
                asker's current position.
        """
        c = SimpleNamespace(
            place=place,
            content_checks=deps.content_checks,
            location_trusted=deps.location_trusted,
            metrics=deps.metrics,
            parking_source=deps.parking_source,
            presence_circles=deps.presence_circles,
            presence_source=deps.presence_source,
            room=deps.room,
            route_source=deps.route_source,
            street_parking_source=deps.street_parking_source,
            _find_parking_ok_note=_make__find_parking_ok_note(deps),
            _route_person_names=deps._route_person_names,
        )
        k_, _, v_ = await _find_parking__p1(c)
        if k_:
            return v_
        if c.place.strip():
            k_, _, v_ = await _find_parking__p2(c)
            if k_:
                return v_
        else:
            k_, _, v_ = await _find_parking__p3(c)
            if k_:
                return v_
        k_, _, v_ = await _find_parking__p4(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return find_parking


async def _find_parking__p1(c):
    if c.route_source is None:
        _record(c.metrics, "find_parking", "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "Parking search isn't set up here. "
                    "Say you couldn't check, and don't guess a car park."
                ),
            },
        )
    return (False, None, None)


async def _find_parking__p2(c):
    if scan_identifiers(c.place, content_checks=c.content_checks) is not None or (
        c.content_checks and _looks_like_coordinate(c.place)
    ):
        _record(c.metrics, "find_parking", "refused")
        return (
            True,
            None,
            {
                "status": "not_found",
                "note": (
                    "That doesn't look like a place name "
                    "I can search for. Ask which place they mean."
                ),
            },
        )
    if c._route_person_names is not None and (
        c.content_checks and _names_a_person(c.place, c._route_person_names)
    ):
        _record(c.metrics, "find_parking", "refused")
        return (
            True,
            None,
            {
                "status": "not_found",
                "note": (
                    "That looks like a person, not a place to park near. Ask which place they mean."
                ),
            },
        )
    try:
        c.resolved = await c.route_source._resolver.resolve(c.place)
    except Exception:
        log.exception("find_parking resolve failed")
        c.resolved = None
    if c.resolved is None:
        _record(c.metrics, "find_parking", "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": ("I couldn't look that place up just now. Say you couldn't check."),
            },
        )
    if c.resolved.status == "ambiguous":
        _record(c.metrics, "find_parking", "ambiguous")
        return (
            True,
            None,
            {
                "status": "ambiguous",
                "note": (
                    "That name matches several places; "
                    "could_be lists them. Ask which one they mean."
                ),
                "could_be": list(c.resolved.could_be),
            },
        )
    if c.resolved.status != "ok" or c.resolved.place is None:
        _record(c.metrics, "find_parking", "not_found")
        return (
            True,
            None,
            {
                "status": "not_found",
                "note": ("I couldn't find that place. Ask which one they mean."),
            },
        )
    c.lat, c.lng = (c.resolved.place.lat, c.resolved.place.lng)
    return (False, None, None)


async def _find_parking__p3(c):
    if c.presence_source is None or not c.presence_circles:
        _record(c.metrics, "find_parking", "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't see where you are right "
                    "now, and you gave me no place to search around. Say you couldn't check."
                ),
            },
        )
    c.view = await c.room.scope()
    if not c.view.available or c.view.degraded:
        _record(c.metrics, "find_parking", "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't confirm who's here, so I "
                    "can't use your position. Say you couldn't check."
                ),
            },
        )
    c.unnamed_blocks = c.content_checks and c.view.unnamed > 0 and (not c.location_trusted)
    c.room_ids = frozenset(p.person_id for p in c.view.people)
    c.pos: tuple[float, float] | None = None
    for _w1 in c.presence_circles:
        c.circle = _w1
        if c.unnamed_blocks:
            continue
        if c.content_checks and (
            not (c.location_trusted or DisclosureGuard(c.circle).permits(c.room_ids))
        ):
            continue
        c.asker = getattr(c.room, "tagger", None)
        c.candidates = tuple(
            p for p in c.view.people if c.asker is not None and p.person_id == c.asker.person_id
        ) + tuple(
            p for p in c.view.people if p.person_id != (c.asker.person_id if c.asker else None)
        )
        for _w2 in c.candidates:
            c.person = _w2
            try:
                c.fix = await c.presence_source.latest_fix(
                    circle_id=c.circle.circle_id, person_id=c.person.person_id
                )
            except Exception:
                log.exception("find_parking presence read failed")
                c.fix = None
            if c.fix is not None:
                c.pos = (c.fix.lat, c.fix.lng)
                break
        if c.pos:
            break
    if c.pos is None:
        _record(c.metrics, "find_parking", "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't use anyone's position in "
                    "this conversation right now. Ask which place they mean instead."
                ),
            },
        )
    c.lat, c.lng = c.pos
    return (False, None, None)


async def _find_parking__p4(c):
    try:
        c.parking = await c.route_source.nearby_parking(lat=c.lat, lng=c.lng)
    except Exception:
        log.exception("find_parking nearby search failed")
        c.parking = None
    if c.parking is None:
        _record(c.metrics, "find_parking", "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I couldn't reach the parking "
                    "search just now. Say you couldn't check, and don't guess a spot."
                ),
            },
        )
    if not c.parking:
        _record(c.metrics, "find_parking", "nothing_found")
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": ("No car parks nearby. Say so plainly, and don't invent one."),
            },
        )
    c.out = []
    for _w1 in c.parking[:5]:
        c.p = _w1
        c.entry: dict[str, object] = {
            "name": c.p.name,
            "address": c.p.address,
            "distance_m": c.p.distance_m,
        }
        if c.parking_source is not None:
            try:
                c.live = await c.parking_source.vacancy(name=c.p.name, strict=True)
            except Exception:  # noqa: BLE001 — vacancy enrichment is never fatal
                c.live = None
            if c.live:
                c.entry["vacancy"] = c.live.get("vacancy")
                c.entry["lastupdate"] = c.live.get("lastupdate")
        c.out.append(c.entry)
    c.result: dict[str, object] = {"status": "ok", "car_parks": c.out}
    if c.street_parking_source is not None:
        try:
            c.street = await c.street_parking_source.near(lat=c.lat, lng=c.lng)
        except Exception:
            log.exception("find_parking street check failed")
            c.street = None
        if c.street is not None:
            c.result["street_parking"] = {
                "total_bays": c.street.total,
                "vacant_bays": c.street.vacant,
                ("streets"): list(c.street.streets),
            }
    _record(c.metrics, "find_parking", "ok", count=len(c.out))
    c.result["note"] = c._find_parking_ok_note(c.out, street="street_parking" in c.result)
    return (True, None, c.result)
    return (False, None, None)
