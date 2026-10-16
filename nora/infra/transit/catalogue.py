"""Which operator runs a route number — the lookup `transit` must own (DEC-24).

WHY THIS MODULE EXISTS, and why it is not optional. A Hong Kong route number
does not say who runs it: `68X` is KMB, `K73` is MTR, and both are four
characters of the same alphabet. Ask the wrong operator and you get `200` with
an empty body, which is indistinguishable from "no buses right now".

**And the empty answer is genuinely ambiguous on the wire.** The live MTR
endpoint answers a real route that is not running exactly as it answers a fake
one (late-night sample):

    routeName        busStop   status  caseNumber  routeStatus  routeStatusRemarkTitle
    ZZZ999 (fake)          0      "0"           1          "1"  Arrival time temporarily…
    K74    (real)          0      "0"           1          "1"  Arrival time temporarily…
    K73    (real)         27      "0"           0          "0"  null

Eleven of the twenty-six published MTR routes — K12, K14, K51A, K52A, K52P,
K53S, K58, K65A, K74, K75S, K76S — returned the ZZZ999 envelope **field for
field** late at night, because they had stopped running for the night. So the
tempting shortcut (`caseNumber == 1` means "no such route") would have told the
group 「K74 呢條線唔存在」 about a bus that runs every twenty minutes at noon.
`caseNumber`, `routeStatus`, `routeColour` and `routeStatusRemarkTitle` are also
every one of them marked **Deprecated** in MTR's own Data Dictionary (v1.19), so
that shortcut is not merely wrong today, it is wrong on a field nobody has
promised to keep.

Nothing in an ETA response can answer "does this route exist". Only a route
CATALOGUE can, so this is the one thing consulted before either ETA endpoint,
and a catalogue that could not be read makes the answer `unavailable` rather
than a claim about the route (R-27).

## The two catalogues

**KMB** — `GET /v1/transport/kmb/route/`, documented as updated at 05:00 daily
(API spec v1.05 §2). 1,605 rows, 791 distinct route numbers, each carrying
`bound` and `service_type`. Note the live response omits the `co` and
`data_timestamp` fields its own specification's sample shows; nothing here reads
either, which is why that divergence is recorded rather than handled.

**MTR** — there is **no route-list endpoint**. `getSchedule` is the entire API.
The Data Dictionary's own appendix points at a CSV instead
(`opendata.mtr.com.hk/data/mtr_bus_routes.csv`, ~4 KB, updated monthly), and its
route table carries the warning "this does not reflect the availability of the
routes" — which is the point: it answers EXISTENCE, and availability is what the
ETA call is for. The CSV also supplies the one thing the ETA response cannot: a
destination name. `lineRef` on the wire is `K73_YLW`, and only `LINE_UP` /
`LINE_DOWN` against `ROUTE_NAME_ENG` turns that into 「Yuen Long West」.

Both are fetched CONCURRENTLY and cached for `CATALOGUE_TTL_SECONDS`. Neither
is on the reply path unless somebody actually asks about a bus.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import aiohttp

log = logging.getLogger("nora.transit")

KMB = "KMB"
MTR = "MTR"

# Six hours. KMB republishes at 05:00 daily and MTR's CSV changes monthly, so
# this is an order of magnitude tighter than either source moves; it exists to
# bound staleness across a long-running container, not to chase updates. It is
# a property of the two published feeds rather than of this deployment, which is
# why it is a constant here and not a `config.toml` key.
CATALOGUE_TTL_SECONDS = 6 * 3600


@dataclass(frozen=True)
class RouteFacts:
    """What the catalogues know about one route number.

    `complete` is the field R-27 turns on: False means at least one catalogue
    could not be read, so "no operator lists this route" is not a conclusion
    anybody is entitled to draw.
    """

    route: str
    kmb_service_type: str | None = None
    # `lineRef` -> where that direction is heading, e.g. `K73_YLW` -> Yuen Long West.
    mtr_directions: Mapping[str, str] = field(default_factory=dict)
    complete: bool = True

    @property
    def operators(self) -> tuple[str, ...]:
        found = []
        if self.kmb_service_type is not None:
            found.append(KMB)
        if self.mtr_directions:
            found.append(MTR)
        return tuple(found)


def parse_kmb_routes(payload: Any) -> dict[str, str]:
    """Route number -> the service type to ask for. NEVER raises.

    **The LOWEST service type, and that is a deliberate narrowing.** 68X is
    published as service types 1, 3 and 4 outbound and 1 inbound; type 1 is the
    ordinary all-day service both ways (`route-eta/68X/1` covers both
    directions), and 3 and 4 are supplementary peak departures.
    Asking for all of them would be three ~48 KB responses to answer 「68X 幾時
    到」 once. If a route ever has no type 1, the lowest it does have is still
    its main service.
    """
    out: dict[str, str] = {}
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        route = row.get("route")
        service = row.get("service_type")
        if not isinstance(route, str) or not route:
            continue
        # A STRING in the route list and an int in an ETA row — that divergence
        # is on the wire. Compared as an int so "10" sorts after "9".
        text = str(service) if service is not None else "1"
        known = out.get(route)
        if known is None or _as_int(text, 99) < _as_int(known, 99):
            out[route] = text
    return out


def parse_mtr_routes(text: str) -> dict[str, dict[str, str]]:
    """Route number -> {lineRef: destination name}. NEVER raises.

    `LINE_UP` runs towards the second place named in `ROUTE_NAME_ENG` and
    `LINE_DOWN` towards the first — verified against every row of the live CSV
    (`K12_EG` / `K12_TPMS` for "Tai Po Market Station to Eightland Garden";
    `K73_YLW` / `K73_TH` for "Tin Heng to Yuen Long West"). A circular route has
    no second direction and keeps the whole name, because "towards Tuen Mun
    Station" is not what a circular route is doing.
    """
    out: dict[str, dict[str, str]] = {}
    try:
        # The live file is UTF-8 **with a BOM**. `response.text()` decodes it as
        # plain UTF-8 and leaves U+FEFF in place, which would make the first
        # column's key `﻿ROUTE_ID` and every lookup miss — silently, with an
        # empty catalogue that reads exactly like a network failure.
        rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    except (csv.Error, ValueError):
        log.warning("mtr route csv did not parse")
        return out
    for row in rows:
        route = (row.get("ROUTE_ID") or "").strip()
        name = (row.get("ROUTE_NAME_ENG") or "").strip()
        if not route:
            continue
        circular = (row.get("IS_CIRCULAR") or "").strip() == "1"
        head, _, tail = name.partition(" to ")
        directions = out.setdefault(route, {})
        for column, towards in (("LINE_UP", tail), ("LINE_DOWN", head)):
            line_ref = (row.get(column) or "").strip()
            if not line_ref:
                continue
            directions[line_ref] = name if (circular or not tail) else towards.strip()
    return {route: lines for route, lines in out.items() if lines}


def _as_int(text: str, default: int) -> int:
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


class RouteCatalogue:
    """Both operators' route tables, fetched concurrently and cached.

    Constructed once per process and held by `TransitClient`; the cache is an
    instance attribute rather than a module global, per CLAUDE.md's test-seam
    policy. `_lock` is what stops two tags arriving together from each pulling
    350 KB.
    """

    def __init__(
        self,
        *,
        kmb_routes_url: str,
        mtr_routes_url: str,
        timeout_seconds: float,
        ttl_seconds: float = CATALOGUE_TTL_SECONDS,
    ) -> None:
        self._kmb_routes_url = kmb_routes_url
        self._mtr_routes_url = mtr_routes_url
        self._timeout_seconds = timeout_seconds
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._kmb: dict[str, str] | None = None
        self._mtr: dict[str, dict[str, str]] | None = None
        self._fetched_at = 0.0

    async def lookup(self, route: str) -> RouteFacts:
        """What is known about `route`, refreshing both catalogues if stale."""
        async with self._lock:
            if self._stale():
                await self._refresh()
        kmb = self._kmb
        mtr = self._mtr
        return RouteFacts(
            route=route,
            kmb_service_type=(kmb or {}).get(route),
            mtr_directions=(mtr or {}).get(route, {}),
            # A catalogue that has never loaded is not an empty catalogue. This
            # is R-27's whole hinge: with either side unknown, "no operator runs
            # this route" is unsupported and the tool must say `unavailable`.
            complete=kmb is not None and mtr is not None,
        )

    def _stale(self) -> bool:
        if self._kmb is None or self._mtr is None:
            return True
        return (time.monotonic() - self._fetched_at) > self._ttl_seconds

    async def _refresh(self) -> None:
        """Both catalogues at once. A failure leaves the previous value in place."""
        kmb, mtr = await asyncio.gather(
            self._fetch_kmb(), self._fetch_mtr(),
        )
        if kmb is not None:
            self._kmb = kmb
        if mtr is not None:
            self._mtr = mtr
        if self._kmb is not None and self._mtr is not None:
            self._fetched_at = time.monotonic()

    async def _fetch_kmb(self) -> dict[str, str] | None:
        body = await get_text(self._kmb_routes_url, self._timeout_seconds)
        if body is None:
            return None
        # `response.json()` is not used and the content type is not asserted:
        # `data.etabus.gov.hk` serves `application/json` today, but a proxy
        # rewriting it to `text/plain` would make aiohttp raise, and this path
        # must degrade to `unavailable` rather than die inside a turn.
        try:
            payload = json.loads(body)
        except ValueError:
            log.warning("transit catalogue %s did not return JSON", self._kmb_routes_url)
            return None
        return parse_kmb_routes(payload) or None

    async def _fetch_mtr(self) -> dict[str, dict[str, str]] | None:
        body = await get_text(self._mtr_routes_url, self._timeout_seconds)
        if body is None:
            return None
        return parse_mtr_routes(body) or None


async def get_text(
    url: str, timeout_seconds: float, payload: Any | None = None,
) -> str | None:
    """One HTTP call — GET, or POST when `payload` is given — or None. NEVER raises.

    Shared by the catalogues and by both ETA endpoints so there is exactly one
    place that decides what a failure looks like. A session per call, like
    `nora.infra.honcho.client`: these are three different hosts reached a
    handful of times an hour, and a pooled session would be state to own for
    no benefit.
    """
    try:
        timeout_config = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout_config) as session:
            request = (
                session.get(url) if payload is None
                else session.post(url, json=payload)
            )
            async with request as response:
                if response.status != 200:
                    log.warning("transit %s answered %s", url, response.status)
                    return None
                return await response.text()
    except Exception as exc:                                        # noqa: BLE001
        # Broad on purpose and never re-raised: this runs inside an agent turn,
        # where an exception costs the whole reply. `aiohttp` alone can raise
        # `ClientError`, `asyncio.TimeoutError`, `UnicodeDecodeError` and
        # `ssl.SSLError` from this block, and a fifth one appearing in a later
        # release must degrade to `unavailable` rather than to silence.
        log.warning("transit %s unreachable: %s", url, exc)
        return None
