"""`RouteClient` — the one public entry point behind the `route` tool.

Paid, external, keyed — the opposite of `transit` on every axis (design/14 §1).
It answers "how do I get from A to B, and how long does it take", and unlike
`transit` it can answer about a FUTURE arrival: Google Routes accepts an
`arrivalTime`, which is why `route` is the first `time_shiftable=True` tool
(§5.1). `rt.data.gov.hk` cannot time-travel; Google can.

**Two halves behind one seam.** `route` takes place NAMES, never coordinates
(DEC-24). `PlaceResolver` (`nora.infra.routes.places`) turns each name into a
coordinate — and it is the scope boundary design/14 §5.2 demanded: places only,
never a person. `RouteClient` then asks Google Routes for the journey between
the two coordinates. The person half of §2.2's hard block does NOT live here —
it lives at the tool seam (`nora.agent.tools`, R-28), because that is where the
roster and the identifier scanner are. This module is below the scanner, exactly
as `nora.infra.transit` is.

**Nothing here raises.** Every failure — a place that would not resolve, a
Google call that failed, a pair of points with no route between them — lands as
one of the three states `nora.infra.routes.directions.describe` produces
(`ok` / `no_route` / `unavailable`), never as an exception into the agent loop.

**The field mask is a bill, and it names only what is rendered** (§the module
`http` docstring). `ROUTES_FIELD_MASK` requests the duration, the distance and
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
# is the default (design/14 §5: this is a group that takes the bus), and an
# unrecognised value falls back to it rather than erroring — the model names a
# mode or it does not.
_MODES = ("TRANSIT", "DRIVE", "WALK")
DEFAULT_MODE = "TRANSIT"

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
    ) -> dict[str, Any]:
        """A journey from `origin` to `destination`, or why there is none. NEVER raises.

        Both are place NAMES; the resolver turns each into a coordinate before
        anything is asked of the Routes API, and those coordinates never appear
        in the return value (DEC-24). `mode` is one of TRANSIT / DRIVE / WALK;
        `arrive_by`, when given, is an RFC 3339 instant that only TRANSIT
        honours (§5.1).
        """
        chosen = str(mode or "").strip().upper()
        if chosen not in _MODES:
            chosen = DEFAULT_MODE
        try:
            return await self._route(origin, destination, chosen, arrive_by)
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
    ) -> dict[str, Any]:
        origin_res, dest_res = await asyncio.gather(
            self._resolver.resolve(origin),
            self._resolver.resolve(destination),
        )
        # A place that could not be READ makes the whole route unavailable —
        # there is nothing honest to say, exactly as an unread catalogue does in
        # transit (R-27's hinge).
        if origin_res.status == "unavailable" or dest_res.status == "unavailable":
            return describe(origin, destination, chosen, None, reachable=False)
        # A place that resolved to nothing, or to several different places, is
        # `no_route` with the ambiguity in the note — never a guessed point
        # sent to Google (design/14 §5.2).
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
        journey = await self._compute(origin_res.place, dest_res.place, chosen, arrive_by)
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

    async def _compute(
        self, origin: Place, destination: Place, mode: str, arrive_by: str,
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
        # `arrivalTime` is a transit-only field on this API; applying it to a
        # DRIVE request would be rejected, so it is set only where it means
        # something (§5.1).
        if arrive_by and mode == "TRANSIT":
            body["arrivalTime"] = arrive_by
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
