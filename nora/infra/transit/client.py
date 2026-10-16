"""`TransitClient` — the one public entry point behind the `transit` tool.

Free, keyless, and two hosts wide. It resolves the operator itself
(`nora.infra.transit.catalogue`) because DEC-24 forbids asking the model to
supply one: an operator is a fact Nora can look up, and every value a schema
asks for is a value the model has to invent on every call.

**The fan-out is real, and it is where the wall clock goes.** One tool that hits
two endpoints one after another is dead time nobody else can remove. The two
route catalogues are fetched with a single `asyncio.gather`, and so are the ETA
calls when a route number turns out to belong to more than one operator. Strands
runs tools concurrently; it cannot parallelise the inside of one.

**Nothing here raises.** Every transport failure lands as `unavailable` with a
sentence that is not a claim about the route — `nora.infra.transit.arrivals`
holds the argument for why those two must never collapse.

No key, no credentials, no `config.toml` section. The three URLs are published
government endpoints and the timeout is a property of them rather than of this
deployment, so both are constants here — the same argument
`nora.infra.honcho.client` makes for `_MAX_DISTANCE`. They are constructor
parameters only so a test can point them at a server that behaves like the real
ones.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from .arrivals import Answer, describe, read_kmb, read_mtr
from .catalogue import KMB, MTR, RouteCatalogue, RouteFacts, get_text
from .places import PlaceResolution, StopDirectory, normalise_place, resolve_place

log = logging.getLogger("nora.transit")

KMB_BASE_URL = "https://data.etabus.gov.hk"
MTR_SCHEDULE_URL = "https://rt.data.gov.hk/v1/transport/mtr/bus/getSchedule"
MTR_ROUTES_URL = "https://opendata.mtr.com.hk/data/mtr_bus_routes.csv"
MTR_STOPS_URL = "https://opendata.mtr.com.hk/data/mtr_bus_stops.csv"

# How many of a resolved place's stop ids may each cost a KMB per-stop ETA
# call. A terminus is at most two poles per route (one per bound — 265M's
# real shape); four is headroom, not a target, and anything past it is a
# resolution too diffuse to be one place anyway.
MAX_STOP_ETA_CALLS = 4

# Eight seconds total per call: the KMB route list (350 KB) and both ETA
# endpoints answer well inside one second each. This is the
# ceiling on how much of a turn a bus question may spend before Nora says she
# could not check, not a latency target.
TIMEOUT_SECONDS = 8.0

# EVERY route number on both networks, and nothing else. This is also the only
# thing standing between a model-supplied string and a URL path — `transit` takes
# one model-invented argument, and `../../` in it must never become a request.
_ROUTE = re.compile(r"[A-Z0-9]{1,7}")


class TransitClient:
    """Live bus and rail-feeder arrivals for Hong Kong.

    Constructed once per process and held by `nora.agent.registry.ToolRegistry`
    — the route catalogues cache on it, and a per-tag instance would re-pull
    350 KB for every question anybody asked.
    """

    def __init__(
        self,
        *,
        kmb_base_url: str = KMB_BASE_URL,
        mtr_schedule_url: str = MTR_SCHEDULE_URL,
        mtr_routes_url: str = MTR_ROUTES_URL,
        mtr_stops_url: str = MTR_STOPS_URL,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        self._kmb_base_url = kmb_base_url.rstrip("/")
        self._mtr_schedule_url = mtr_schedule_url
        self._timeout_seconds = timeout_seconds
        self._catalogue = RouteCatalogue(
            kmb_routes_url=f"{self._kmb_base_url}/v1/transport/kmb/route/",
            mtr_routes_url=mtr_routes_url,
            timeout_seconds=timeout_seconds,
        )
        self._stops = StopDirectory(
            kmb_stops_url=f"{self._kmb_base_url}/v1/transport/kmb/stop",
            mtr_stops_url=mtr_stops_url,
            timeout_seconds=timeout_seconds,
        )

    async def arrivals(self, route: str, near: str = "") -> dict[str, Any]:
        """The next departures on `route`, or why there are none. NEVER raises.

        `near`, when given, is the place somebody NAMED — the model's one
        legitimate place decision under DEC-24 — and the resolver
        (`nora.infra.transit.places`) turns it into that operator's own stop
        on this route so the times come back for where the person actually
        is. It never reaches a URL: only dataset-authored stop ids do.
        """
        cleaned = str(route or "").strip().upper()
        if not _ROUTE.fullmatch(cleaned):
            # Not a route number at all, so no catalogue could list it and no
            # endpoint should be asked. `nothing_found` rather than a refusal:
            # from where the model sits, «I have no such route» is the same
            # answer whether the string was malformed or merely unknown.
            #
            # The string itself is NOT echoed back, and that is the same hole
            # `nora.agent.tools._asked_about` exists for: `route` is the one
            # model-supplied value here, echoing helps only when it is a route
            # number, and `«../../ETC/PASSWD»` handed back inside a tool result
            # is a model's own invention reflected at it with Nora's authority.
            return describe("", True, (), ())
        facts = await self._catalogue.lookup(cleaned)
        place = str(near or "")
        resolution: PlaceResolution | None = None
        if facts.operators and normalise_place(place):
            resolution = await self._resolve(facts, place)
        if resolution is not None and resolution.status == "ok":
            answers = await self._ask_at(facts, resolution)
        else:
            answers = await self._ask(facts)
        return describe(cleaned, facts.complete, facts.operators, answers, resolution)

    async def _resolve(self, facts: RouteFacts, place: str) -> PlaceResolution:
        """One `near` against the stop data — directory (cached) plus, for a
        KMB route, the two route-stop lists that say which stops it serves."""
        (kmb_stops, mtr_stops), route_stop_ids = await asyncio.gather(
            self._stops.stops(),
            self._kmb_route_stop_ids(facts),
        )
        return resolve_place(
            place, route=facts.route,
            kmb_stops=kmb_stops, mtr_stops=mtr_stops,
            kmb_route_stop_ids=route_stop_ids,
            kmb_runs_route=facts.kmb_service_type is not None,
            mtr_runs_route=bool(facts.mtr_directions),
        )

    async def _kmb_route_stop_ids(self, facts: RouteFacts) -> frozenset[str] | None:
        """Which stops this KMB route serves, both bounds at once. `None` when
        the route is not KMB's — or when EITHER bound could not be read: half
        a route would turn «I could not check» into a confident
        «not_on_route» for every stop on the unread half."""
        if facts.kmb_service_type is None:
            return None
        bounds = await asyncio.gather(*(
            self._kmb_route_stop_bound(facts.route, bound, facts.kmb_service_type)
            for bound in ("outbound", "inbound")
        ))
        if any(b is None for b in bounds):
            return None
        return frozenset(stop for bound in bounds if bound for stop in bound)

    async def _kmb_route_stop_bound(
        self, route: str, bound: str, service_type: str,
    ) -> tuple[str, ...] | None:
        url = (f"{self._kmb_base_url}/v1/transport/kmb/route-stop/"
               f"{route}/{bound}/{service_type}")
        payload = _json(await get_text(url, self._timeout_seconds), url)
        if not isinstance(payload, dict):
            return None
        rows = payload.get("data")
        if not isinstance(rows, list):
            return None
        return tuple(
            row["stop"] for row in rows
            if isinstance(row, dict) and isinstance(row.get("stop"), str)
        )

    async def _ask_at(
        self, facts: RouteFacts, resolution: PlaceResolution,
    ) -> tuple[Answer, ...]:
        """The ETA reads, scoped to the resolved stop. Only the operator that
        runs the route contributed stop ids, so this fans out exactly like
        `_ask` does."""
        pending = []
        if resolution.kmb_stop_ids and facts.kmb_service_type is not None:
            pending.append(self._kmb_at(
                facts.route, facts.kmb_service_type,
                resolution.kmb_stop_ids[:MAX_STOP_ETA_CALLS],
            ))
        if resolution.mtr_stop_ids and facts.mtr_directions:
            pending.append(self._mtr_at(
                facts.route, facts.mtr_directions,
                frozenset(resolution.mtr_stop_ids),
            ))
        if not pending:
            return ()
        return tuple(await asyncio.gather(*pending))

    async def _kmb_at(
        self, route: str, service_type: str, stop_ids: tuple[str, ...],
    ) -> Answer:
        """`GET /v1/transport/kmb/eta/{stop_id}/{route}/{service_type}` (spec
        v1.05 §8) per resolved pole, merged into one
        payload for `read_kmb`: every row is already at the asked-for stop, so
        the origin logic's «lowest seq per direction» picks each bound's own
        pole and nothing else. The ids are DATASET-authored — the one string
        the model chose (`near`) never reaches a URL."""
        payloads = await asyncio.gather(*(
            self._kmb_stop_eta(stop_id, route, service_type) for stop_id in stop_ids
        ))
        readable = [p for p in payloads if isinstance(p, dict)]
        if not readable:
            return Answer(operator=KMB, reachable=False)
        merged: dict[str, Any] = dict(readable[0])
        merged["data"] = [
            row for payload in readable
            for row in (payload.get("data") or [])
        ]
        return read_kmb(merged)

    async def _kmb_stop_eta(
        self, stop_id: str, route: str, service_type: str,
    ) -> Any | None:
        url = (f"{self._kmb_base_url}/v1/transport/kmb/eta/"
               f"{stop_id}/{route}/{service_type}")
        return _json(await get_text(url, self._timeout_seconds), url)

    async def _mtr_at(
        self, route: str, towards: Mapping[str, str], at_stops: frozenset[str],
    ) -> Answer:
        body = await get_text(
            self._mtr_schedule_url, self._timeout_seconds,
            payload={"language": "en", "routeName": route},
        )
        payload = _json(body, self._mtr_schedule_url)
        if payload is None:
            return Answer(operator=MTR, reachable=False)
        return read_mtr(payload, towards, at_stops)

    async def _ask(self, facts: RouteFacts) -> tuple[Answer, ...]:
        """Every operator that lists this route, asked at the same time."""
        pending = []
        if facts.kmb_service_type is not None:
            pending.append(self._kmb(facts.route, facts.kmb_service_type))
        if facts.mtr_directions:
            pending.append(self._mtr(facts.route, facts.mtr_directions))
        if not pending:
            return ()
        return tuple(await asyncio.gather(*pending))

    async def _kmb(self, route: str, service_type: str) -> Answer:
        url = f"{self._kmb_base_url}/v1/transport/kmb/route-eta/{route}/{service_type}"
        payload = _json(await get_text(url, self._timeout_seconds), url)
        if payload is None:
            return Answer(operator=KMB, reachable=False)
        return read_kmb(payload)

    async def _mtr(self, route: str, towards: Mapping[str, str]) -> Answer:
        # POST with a JSON body, and `language: "en"` because every other string
        # this tool returns is English and mixing the two inside one answer
        # would only make her pick one at random. She speaks Cantonese to the
        # group either way; a destination name is data, not voice.
        body = await get_text(
            self._mtr_schedule_url, self._timeout_seconds,
            payload={"language": "en", "routeName": route},
        )
        payload = _json(body, self._mtr_schedule_url)
        if payload is None:
            return Answer(operator=MTR, reachable=False)
        return read_mtr(payload, towards)


def _json(body: str | None, url: str) -> Any | None:
    if body is None:
        return None
    try:
        return json.loads(body)
    except ValueError:
        log.warning("transit %s did not return JSON", url)
        return None
