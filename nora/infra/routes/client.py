"""`RouteClient` — the one public entry point behind the `route` tool.

Paid, external, keyed — the opposite of `transit` on every axis.
It answers "how do I get from A to B, and how long does it take", and unlike
`transit` it can answer about a FUTURE arrival: Google Routes accepts an
`arrivalTime`, which is why `route` is the first `time_shiftable=True` tool.
`rt.data.gov.hk` cannot time-travel; Google can.

**Two halves behind one seam.** `route` takes place NAMES, never coordinates
(DEC-24). `PlaceResolver` (`nora.infra.routes.places`) turns each name into a
coordinate — and it is the scope boundary: places only, never a person.
`RouteClient` then asks Google Routes for the journey between the two
coordinates. The person half of that block does NOT live here — it lives at
the tool seam (`nora.agent.tools`, R-28), because that is where the roster
and the identifier scanner are. This module is below the scanner, exactly as
`nora.infra.transit` is.

**Nothing here raises.** Every failure — a place that would not resolve, a
Google call that failed, a pair of points with no route between them — lands as
one of the three states `nora.infra.routes.directions.describe` produces
(`ok` / `no_route` / `unavailable`), never as an exception into the agent loop.

**The field mask is a bill, and it names only what is rendered** (see the
`http` module). `ROUTES_FIELD_MASK` requests the duration, the distance and
the per-leg transit details the tool renders — and nothing else. It does NOT
request the polyline (large, unrendered) or per-step `navigationInstruction`
(free text, unrendered, and a dearer SKU).

The URLs and timeout are constants: they are properties of the Google endpoints,
not of this deployment (the same argument `nora.infra.transit.client` makes).
They are constructor parameters only so a test can point them at a server that
behaves like the real one. The API KEY is not a constant — it is the one secret,
passed in already resolved from the environment by the composition root (DEC-8).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .directions import Journey, candidates_note, describe, read_journey
from .http import post_json
from .places import Place, PlaceResolution, PlaceResolver

log = logging.getLogger("nora.routes")

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

# Eight seconds total per call, the same ceiling and argument as transit: it
# bounds how much of a turn a route question may spend before Nora says she
# could not check, not a latency target.
TIMEOUT_SECONDS = 8.0

# The three modes Google supports here that make sense for this group. TRANSIT
# is the default (this is a group that takes the bus), and an
# unrecognised value falls back to it rather than erroring — the model names a
# mode or it does not.
_MODES = ("TRANSIT", "DRIVE", "WALK")
DEFAULT_MODE = "TRANSIT"

# The transit SUB-mode a person NAMES (「搭巴士」「by MTR」「坐輕鐵」), mapped to Google
# Routes `transitPreferences.allowedTravelModes`. Per the Google docs
# (developers.google.com/maps/documentation/routes/reference/rest/v2/
# TransitPreferences): the enum is BUS / SUBWAY / TRAIN / LIGHT_RAIL / RAIL —
# there is NO TRAM value, and LIGHT_RAIL is documented "light rail or tram", so
# 電車/叮叮 and 輕鐵 both map there (Google cannot separate them at request time;
# in HK they are geographically disjoint, so the restriction resolves per area).
# RAIL is deliberately NOT used for MTR: the docs define it as SUBWAY+TRAIN+
# LIGHT_RAIL combined, which would pull 輕鐵 into a「港鐵」request. An unknown
# keyword maps to nothing and the request stays all-modes — the same fallback
# shape `_MODES` has: the person names a mode or they do not.
_TRANSIT_MODES: Mapping[str, list[str]] = {
    "bus": ["BUS"],
    "mtr": ["SUBWAY", "TRAIN"],
    "light_rail": ["LIGHT_RAIL"],
    "tram": ["LIGHT_RAIL"],
    "train": ["TRAIN"],
}

# `transitPreferences.routingPreference` — what a person asks FOR (「唔想行咁遠」/
# 「唔想轉車」), not plumbing. Same closed-enum, unknown-omitted discipline.
_ROUTING_PREFERENCES: Mapping[str, str] = {
    "less_walking": "LESS_WALKING",
    "fewer_transfers": "FEWER_TRANSFERS",
}


def _rfc3339(value: str) -> str:
    """`value` iff it parses as an RFC 3339 instant, else "" — the unknown→omitted
    discipline the enums use, applied to time. A malformed or model-invented
    timestamp is dropped at THIS boundary rather than POSTed to Google (the
    docstring already tells the model to take it from the clock, never invent
    one). `datetime.fromisoformat` accepts the `+08:00`-offset form Nora emits."""
    if not value:
        return ""
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return ""
    return value

# ONLY what `directions.describe` renders — every field here is billed. Duration
# and distance are the spine of any answer; the `transitDetails` subtree is what
# a rider needs (line, vehicle, agency, boarding stop, time, headsign, stop
# count). No polyline, no navigationInstruction: unrendered and dearer.
ROUTES_FIELD_MASK = (
    "routes.duration,"
    "routes.distanceMeters,"
    "routes.legs.steps.transitDetails.transitLine.nameShort,"
    "routes.legs.steps.transitDetails.transitLine.name,"
    "routes.legs.steps.transitDetails.transitLine.vehicle.type,"
    "routes.legs.steps.transitDetails.transitLine.agencies.name,"
    "routes.legs.steps.transitDetails.stopDetails.departureStop.name,"
    "routes.legs.steps.transitDetails.stopDetails.departureTime,"
    "routes.legs.steps.transitDetails.stopDetails.arrivalStop.name,"
    "routes.legs.steps.transitDetails.stopDetails.arrivalTime,"
    "routes.legs.steps.transitDetails.headsign,"
    "routes.legs.steps.transitDetails.stopCount"
)


class RouteClient:
    """Google Routes + Places, behind the `route` tool.

    Constructed once per process and held by `nora.agent.registry.ToolRegistry`
    — it owns the resolver and the one HTTP identity. A per-tag instance would
    rebuild both for every question.
    """

    def __init__(
        self,
        *,
        api_key: str,
        routes_url: str = ROUTES_URL,
        resolver: PlaceResolver | None = None,
        timeout_seconds: float = TIMEOUT_SECONDS,
        region_code: str = "HK",
        language_code: str = "zh-HK",
    ) -> None:
        self._api_key = api_key
        self._routes_url = routes_url
        self._timeout_seconds = timeout_seconds
        self._region_code = region_code
        self._language_code = language_code
        self._resolver = resolver if resolver is not None else PlaceResolver(
            api_key=api_key, timeout_seconds=timeout_seconds,
            region_code=region_code, language_code=language_code,
        )

    async def route(
        self, origin: str, destination: str,
        mode: str = DEFAULT_MODE, arrive_by: str = "",
        by: str = "", prefer: str = "", depart_after: str = "",
        local_places: Mapping[str, Place] | None = None,
    ) -> dict[str, Any]:
        """A journey from `origin` to `destination`, or why there is none. NEVER raises.

        Both are place NAMES; the resolver turns each into a coordinate before
        anything is asked of the Routes API, and those coordinates never appear
        in the return value (DEC-24). `mode` is one of TRANSIT / DRIVE / WALK;
        `arrive_by`/`depart_after`, when given, are RFC 3339 instants that only
        TRANSIT honours, and they are mutually exclusive (arrive-by wins).

        `local_places` (DEC-60) maps CASEFOLDED household place
        names to pre-resolved `Place`s — the saved-places layer the tool builds
        per room, already subset-checked THERE (the guard needs the room, which
        this process-wide client must not hold). A hit here skips the Google
        Places text search entirely — the name never reaches Google — and the
        stored coordinate travels only as the Routes waypoint the person's own
        request requires. A miss falls through to the public lookup, so an
        unsaved (or unauthorised — the caller passes none then) name behaves
        exactly as today.

        The three TRANSIT sub-preferences are intent a person states, not
        plumbing: `by` names a sub-mode (bus/mtr/light_rail/
        tram/train → `transitPreferences.allowedTravelModes`), `prefer` names a
        routing preference (less_walking/fewer_transfers). Each is a closed enum
        validated in `_compute`; an unknown value is dropped, never sent.
        """
        chosen = str(mode or "").strip().upper()
        if chosen not in _MODES:
            chosen = DEFAULT_MODE
        try:
            return await self._route(
                origin, destination, chosen, arrive_by,
                by=by, prefer=prefer, depart_after=depart_after,
                local_places=local_places,
            )
        except Exception:
            # The never-raises contract's backstop. Every collaborator here is
            # documented not to raise (`PlaceResolver.resolve` and `post_json`
            # both turn failure into a value), so this catches only a contract
            # violation — and it must degrade to `unavailable`, never propagate
            # into the agent loop where an exception costs the whole reply (R-5).
            log.exception("route failed for a resolved pair")
            return describe(origin, destination, chosen, None, reachable=False)

    async def _route(
        self, origin: str, destination: str, chosen: str, arrive_by: str,
        by: str = "", prefer: str = "", depart_after: str = "",
        local_places: Mapping[str, Place] | None = None,
    ) -> dict[str, Any]:
        async def resolve(text: str) -> PlaceResolution:
            # The household layer first: a casefolded name match
            # short-circuits Google Places — the name stays on the host. Logged
            # as a count-free line (never the name: it is household data).
            if local_places:
                place = local_places.get(text.strip().casefold())
                if place is not None:
                    log.info("route endpoint resolved from a saved place")
                    return PlaceResolution(status="ok", asked=text, place=place)
            return await self._resolver.resolve(text)

        origin_res, dest_res = await asyncio.gather(
            resolve(origin), resolve(destination),
        )
        # A place that could not be READ makes the whole route unavailable —
        # there is nothing honest to say, exactly as an unread catalogue does in
        # transit (R-27's hinge).
        if origin_res.status == "unavailable" or dest_res.status == "unavailable":
            return describe(origin, destination, chosen, None, reachable=False)
        # A place that resolved to nothing, or to several different places, is
        # `no_route` with the ambiguity in the note — never a guessed point
        # sent to Google.
        unresolved = _unresolved(origin_res, dest_res)
        if unresolved:
            return {
                "status": "no_route", "origin": origin, "destination": destination,
                "note": candidates_note(origin, destination, unresolved),
                **_candidate_fields(origin_res, dest_res),
            }
        # Both resolved to exactly one place here — `_unresolved` returned empty
        # and `unavailable` was handled above — but narrow explicitly rather than
        # asserting, so a fourth resolver status added later degrades to
        # `unavailable` instead of tripping an assertion inside a turn.
        if origin_res.place is None or dest_res.place is None:
            return describe(origin, destination, chosen, None, reachable=False)
        journey = await self._compute(
            origin_res.place, dest_res.place, chosen, arrive_by,
            by=by, prefer=prefer, depart_after=depart_after,
        )
        if journey is _FAILED:
            return describe(origin, destination, chosen, None, reachable=False)
        result = describe(
            origin, destination, chosen,
            journey if isinstance(journey, Journey) else None, reachable=True,
        )
        # The typed dining bonus on the DESTINATION, when it carries any and the
        # route was found. Numbers, an enum and a boolean — not wrapped.
        if result["status"] == "ok":
            dining = dest_res.place.dining()
            if dining:
                result["destination_info"] = dining
        return result

    async def place_info(self, name: str) -> dict[str, Any]:
        """Google's structured facts about ONE named place — rating, price band,
        open-now — reusing the Places resolver the route path already uses. NEVER
        raises. This is the reliable answer to «好唔好味»: a 4.2★ from Google is
        structured truth, unlike a scraped page.

        Four statuses mirror the resolver: `ok` (with whatever
        dining fields Google returned — a place may have none), `ambiguous` (the
        name matches several distinct places; ask which), `not_found`, and
        `unavailable`. The place's COORDINATE is resolved internally and never
        returned — same as route (DEC-24); only the name, address and the typed
        rating/price/open-now fields, none of which is a coordinate."""
        try:
            res = await self._resolver.resolve(name)
        except Exception:
            log.exception("place_info resolve failed")
            return {"status": "unavailable"}
        if res.status == "ambiguous":
            return {"status": "ambiguous", "could_be": list(res.could_be)}
        if res.status == "not_found":
            return {"status": "not_found"}
        if res.status != "ok" or res.place is None:
            return {"status": "unavailable"}
        place = res.place
        out: dict[str, Any] = {
            "status": "ok", "name": place.name, "place_id": place.place_id,
        }
        if place.address:
            out["address"] = place.address
        # The typed dining fields (rating/price_level/open_now) — numbers, an
        # enum and a bool, not free text, so not `untrusted`-wrapped.
        # Empty for a bus stop or a street corner, which is honest.
        out.update(place.dining())
        return out

    async def locate(self, name: str) -> dict[str, Any]:
        """The coordinate of ONE named place, for a SERVICE and never for the model
        NEVER raises. Same resolver as `place_info`, so the same
        four statuses (`ok`, `ambiguous`, `not_found`, `unavailable`), but `ok` carries
        `lat` and `lng` as well as `name`.

        DEC-24 keeps a coordinate out of every model-facing schema, and this method is
        why that still holds: it is for a service that writes the pair into a stored
        `Map`, reached through `screen_and_locate_place`, never into a tool result.
        A tool must not call it."""
        try:
            res = await self._resolver.resolve(name)
        except Exception:
            log.exception("locate resolve failed")
            return {"status": "unavailable"}
        if res.status == "ambiguous":
            return {"status": "ambiguous", "could_be": list(res.could_be)}
        if res.status == "not_found":
            return {"status": "not_found"}
        if res.status != "ok" or res.place is None:
            return {"status": "unavailable"}
        place = res.place
        return {"status": "ok", "name": place.name, "lat": place.lat, "lng": place.lng}

    async def nearby_parking(
        self, *, lat: float, lng: float, radius_m: float | None = None,
    ) -> tuple[Any, ...] | None:
        """Car parks near a point, for `find_parking`. NEVER raises.

        This is the FIND layer — «附近邊度有停車場» — answered from Google
        Places Nearby Search (`includedTypes: ["parking"]`). It reuses the
        same key/HTTP identity as `route`/`place_info`; the field mask keeps
        billing to what the tool renders. `None` = the call failed (tool
        reports `unavailable`); an empty tuple = Google searched and found
        nothing nearby (honest answer, never a fabricated list).
        """
        # Imported lazily: `parking.nearby` imports `routes.http`, which is
        # already partially imported here — a module-level import is a cycle.
        from ..parking.nearby import SEARCH_RADIUS_M, search_nearby
        try:
            return await search_nearby(
                api_key=self._api_key, lat=lat, lng=lng,
                radius_m=radius_m or SEARCH_RADIUS_M,
                timeout_seconds=self._timeout_seconds,
            )
        except Exception:
            log.exception("nearby_parking failed")
            return None

    async def _compute(
        self, origin: Place, destination: Place, mode: str, arrive_by: str,
        by: str = "", prefer: str = "", depart_after: str = "",
    ) -> Journey | object | None:
        """The Routes call for two resolved points. Returns a `Journey`, `None`
        (Google answered but there is no route), or the `_FAILED` sentinel (the
        call itself failed — which must become `unavailable`, not `no_route`)."""
        body: dict[str, Any] = {
            "origin": _waypoint(origin),
            "destination": _waypoint(destination),
            "travelMode": mode,
            "languageCode": self._language_code,
            "regionCode": self._region_code,
        }
        # `transitPreferences`, `arrivalTime` and `departureTime` are all set
        # ONLY under TRANSIT — the tool's one conservative rule, rather
        # than tracking Google's per-field acceptance (arrivalTime is IGNORED off
        # TRANSIT; departureTime is future-only). Every sub-field is validated at
        # THIS boundary — the enums through a table (unknown → omitted), the
        # timestamps as RFC 3339 (malformed → omitted) — so nothing free-text or
        # model-invented reaches Google. origin/destination are the only free
        # text and R-28 screened them upstream. No new egress hole.
        if mode == "TRANSIT":
            prefs: dict[str, Any] = {}
            allowed = _TRANSIT_MODES.get(by.strip().lower())
            if allowed:
                prefs["allowedTravelModes"] = allowed
            preference = _ROUTING_PREFERENCES.get(prefer.strip().lower())
            if preference:
                prefs["routingPreference"] = preference
            if prefs:
                body["transitPreferences"] = prefs
            # depart-at and arrive-by are mutually exclusive on this API. A
            # person distinguishes 「八點出發」 from 「八點到」, so both are exposed;
            # arrive-by wins when both are given (the more specific intent), and
            # depart-after is dropped rather than sent alongside it.
            arrive, depart = _rfc3339(arrive_by), _rfc3339(depart_after)
            if arrive:
                body["arrivalTime"] = arrive
            elif depart:
                body["departureTime"] = depart
        payload = await post_json(
            self._routes_url, api_key=self._api_key,
            field_mask=ROUTES_FIELD_MASK, body=body,
            timeout_seconds=self._timeout_seconds,
        )
        if payload is None:
            return _FAILED
        # Google returns `{}` (no `routes`) for a pair it cannot connect — that
        # is `no_route`, and `read_journey` maps it to None.
        return read_journey(payload)


# A sentinel distinct from None: `_compute` must tell "Google failed" (→
# unavailable) apart from "Google answered, no route" (→ no_route), and both are
# falsy. This is R-27's ok/nothing/unavailable split, one layer down.
_FAILED = object()


def _waypoint(place: Place) -> dict[str, Any]:
    return {"location": {"latLng": {"latitude": place.lat, "longitude": place.lng}}}


def _unresolved(
    origin: PlaceResolution, dest: PlaceResolution,
) -> list[tuple[str, str]]:
    """The (label, detail) pairs for any endpoint that did not resolve to one
    place — for the note the model asks a follow-up from."""
    out: list[tuple[str, str]] = []
    for label, res in (("origin", origin), ("destination", dest)):
        if res.status == "not_found":
            out.append((label, f"no place found for «{res.asked}»"))
        elif res.status == "ambiguous":
            out.append((label, f"«{res.asked}» could be {', '.join(res.could_be)}"))
    return out


def _candidate_fields(
    origin: PlaceResolution, dest: PlaceResolution,
) -> dict[str, Any]:
    """The ambiguous candidates as a structured field beside the note, so the
    model can offer them without re-parsing the sentence."""
    out: dict[str, Any] = {}
    for label, res in (("origin", origin), ("destination", dest)):
        if res.status == "ambiguous" and res.could_be:
            out[f"{label}_could_be"] = list(res.could_be)
    return out
