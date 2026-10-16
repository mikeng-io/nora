"""The place resolver, HK-open-data half — a stop NAME becomes a stop, inside Nora (DEC-41).

The model names WHAT — 「天恆」, because somebody said it — and Nora resolves
WHERE, the same division `Room` draws for people: there is no `resolve_person`
tool, the tools that need one are scoped through it, and there is likewise no
`place_lookup` tool. `transit`'s optional `near` argument is the only doorway,
and everything below it — stop ids, coordinates, sequence numbers — is Nora's
and never appears in a schema (DEC-24).

**THE SCOPE BOUNDARY LIVES HERE, and it is places only.** This module resolves
a name against the two operators' published STOP DATASETS and against nothing
else. A person's name must never resolve to a place through this seam —
「Tyler 屋企」 is not a stop, and a Life360/Places half behind this same seam
would inherit exactly this discipline: a person's name or live position is
hard-blocked in any argument that leaves the host, and the in-repo half of that
guarantee is that nothing in this module consults `identity.toml`, Honcho, the roster, or any
store that knows a human. It reads two public files about bus stops, and that
is the whole of what it may ever read.

## The two datasets, both keyless

**KMB** — `GET /v1/transport/kmb/stop` (~1.2 MB), updated 05:00 daily:
`{stop, name_tc, name_en, name_sc, lat, long}`. `lat`/`long` may be a NUMBER
or a STRING; both parse below. Route membership is NOT here: that is the Route-Stop
API, fetched per call by the client because two ~2 KB responses per question
beat caching a 5 MB all-routes dump nobody asked about.

**MTR** — `opendata.mtr.com.hk/data/mtr_bus_stops.csv` (named in the Data Dictionary's
appendix), per-route ordered stops WITH coordinates,
UTF-8 with a BOM like its sibling. Its `STATION_ID` (`K73-D070`) is the same
string `getSchedule` sends as `busStopId` — that equality is the join key this
whole resolver stands on, and it is why the MTR half needs no second endpoint.

Both are cached the way the route catalogue is: on the registry-owned client,
hours-long TTL, loaded on first use, and a dataset that never loaded is
`unavailable`, never empty — «no stop by that name» is not a claim an unread
file entitles anybody to make (R-27's hinge, applied to places).

## Matching, and why it is not exact-only

Exact-after-normalisation first, in the spirit of `RoomView.find` — the
wrapper punctuation the model inherits from chat is stripped, English is
casefolded. But exact-only is too brittle against the real KMB data:
every KMB name carries a stop code — 天恆邨總站 **(TN970)** — so the flagship
query 「天恆」 would match nothing KMB runs. Two deliberate widenings, each
bounded: the trailing `(XX999)` code is set aside for the exact pass, and when
exact still finds nothing a SUBSTRING pass runs (query at least two characters,
matches capped at `MAX_PLACE_MATCHES`). Ambiguity is then an answer, not a
guess: matches within `SAME_PLACE_RADIUS_M` of each other are one place (a
terminus is several poles metres apart), and matches further apart come back as
named candidates for her to ask about — 輕鐵 legitimately means two different
K73 stops 2.2 km apart, and picking one silently is this feature's version of
the wrong confident answer.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any

from .catalogue import get_text

log = logging.getLogger("nora.transit")

KMB = "KMB"
MTR = "MTR"

# Same figure and same argument as CATALOGUE_TTL_SECONDS: KMB republishes at
# 05:00 daily and the MTR CSV moves monthly, so six hours bounds staleness
# without chasing either feed.
STOP_DIRECTORY_TTL_SECONDS = 6 * 3600

# One terminus is several poles metres apart (the three 天恆邨總站 stops span
# ~12 m); two stops 150 m apart are still "the same place" to a person standing
# at one of them. Anything further is a different answer to 「喺邊度上車」.
SAME_PLACE_RADIUS_M = 150.0

# A substring pass that matches more stops than this is a name too vague to
# reason about (「站」 matches hundreds), and the honest answer is to ask for a
# better one rather than to cluster the whole territory.
MAX_PLACE_MATCHES = 24
# Candidate names offered back on an ambiguous match. Enough to ask with; not a
# gazetteer.
MAX_CANDIDATES = 6

# The wrapper punctuation a place name arrives in when the model lifts it out
# of a Cantonese message — the same set, for the same reason, as
# `nora.agent.room._WRAPPERS` (which this module cannot import: `nora.infra`
# sits below `nora.agent` in the layer contract). RUF001 suppressed there and
# here alike because the ambiguous characters ARE the data.
_WRAPPERS = "「」『』《》〈〉“”‘’\"'()（）[]【】 \t　"  # noqa: RUF001

# The trailing stop code KMB appends to every name — 灰沙圍 (YL353). Two
# capital letters, three digits, end of string. MTR names keep their
# parentheticals because there they are part of the name (元朗西 (輕鐵豐年路站)),
# and the pattern is anchored so it cannot touch those.
_KMB_CODE = re.compile(r"\s*\([A-Z]{2}\d{3}\)\s*$")


def normalise_place(text: str) -> str:
    """The one normalisation both sides of a place match go through."""
    return str(text).strip(_WRAPPERS).strip().casefold()


@dataclass(frozen=True)
class Stop:
    """One row of one operator's stop dataset. `route` is empty for KMB rows
    (the global stop list knows no routes) and set for MTR rows (the CSV is
    route-scoped by construction)."""

    operator: str
    stop_id: str
    name_tc: str
    name_en: str
    lat: float
    lng: float
    route: str = ""

    def display_name(self, query: str) -> str:
        """The name to say out loud — in the script the question used, with
        KMB's stop code set aside (public signage says 灰沙圍, not
        灰沙圍 (YL353))."""
        wants_chinese = any("一" <= ch <= "鿿" for ch in query)
        name = self.name_tc if wants_chinese and self.name_tc else self.name_en
        return _KMB_CODE.sub("", name).strip()

    def matches(self, wanted: str) -> bool:
        """Exact after normalisation — against either language's name, with and
        without the KMB code."""
        for name in (self.name_tc, self.name_en):
            key = normalise_place(name)
            if wanted in (key, normalise_place(_KMB_CODE.sub("", name))):
                return True
        return False

    def contains(self, wanted: str) -> bool:
        for name in (self.name_tc, self.name_en):
            if wanted and wanted in normalise_place(name):
                return True
        return False


def parse_kmb_stops(payload: Any) -> tuple[Stop, ...]:
    """The KMB stop list -> Stops. NEVER raises; a row that does not parse is
    dropped rather than costing the dataset."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return ()
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        stop_id = row.get("stop")
        position = _coordinates(row.get("lat"), row.get("long"))
        if not isinstance(stop_id, str) or not stop_id or position is None:
            continue
        out.append(Stop(
            operator=KMB, stop_id=stop_id.strip(),
            name_tc=str(row.get("name_tc") or "").strip(),
            name_en=str(row.get("name_en") or "").strip(),
            lat=position[0], lng=position[1],
        ))
    return tuple(out)


def parse_mtr_stops(text: str) -> tuple[Stop, ...]:
    """The MTR stops CSV -> Stops, route attached. NEVER raises.

    The same BOM its sibling routes CSV carries is stripped the same way. Rows
    whose `ROUTE_ID` is a variant (`REFERENCE_ID` K73-1) still carry ROUTE_ID
    `K73`, so filtering by route later needs no variant handling here.
    """
    try:
        rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    except (csv.Error, ValueError):
        log.warning("mtr stops csv did not parse")
        return ()
    out = []
    for row in rows:
        stop_id = (row.get("STATION_ID") or "").strip()
        route = (row.get("ROUTE_ID") or "").strip()
        position = _coordinates(
            row.get("STATION_LATITUDE"), row.get("STATION_LONGITUDE"),
        )
        if not stop_id or not route or position is None:
            continue
        out.append(Stop(
            operator=MTR, stop_id=stop_id,
            name_tc=(row.get("STATION_NAME_CHI") or "").strip(),
            name_en=(row.get("STATION_NAME_ENG") or "").strip(),
            lat=position[0], lng=position[1], route=route,
        ))
    return tuple(out)


def _coordinates(lat: Any, lng: Any) -> tuple[float, float] | None:
    """A position from fields the KMB feed may send as a number in
    one row and a string in the next. Both parse."""
    try:
        return float(str(lat).strip()), float(str(lng).strip())
    except (TypeError, ValueError):
        return None


def match_stops(stops: tuple[Stop, ...], query: str) -> tuple[Stop, ...]:
    """Every stop `query` could mean, exact first, substring as the bounded
    fallback. Returns () for a query nothing matches — the caller decides what
    () is allowed to MEAN (R-27: only a loaded dataset makes it a claim)."""
    wanted = normalise_place(query)
    if not wanted:
        return ()
    exact = tuple(s for s in stops if s.matches(wanted))
    if exact:
        return exact
    if len(wanted) < 2:
        # One character substring-matches half the territory; exact-only for it.
        return ()
    return tuple(s for s in stops if s.contains(wanted))


def same_place(stops: tuple[Stop, ...]) -> bool:
    """Whether every match is within SAME_PLACE_RADIUS_M of the first — one
    terminus with several poles, not several places. Equirectangular metres:
    Hong Kong spans half a degree, the error is centimetres."""
    if len(stops) <= 1:
        return True
    first = stops[0]
    for stop in stops[1:]:
        dy = (stop.lat - first.lat) * 111_320.0
        dx = (stop.lng - first.lng) * 111_320.0 * math.cos(math.radians(first.lat))
        if math.hypot(dx, dy) > SAME_PLACE_RADIUS_M:
            return False
    return True


@dataclass(frozen=True)
class PlaceResolution:
    """What became of one `near`. The five statuses are the place-axis version
    of the tool's three (R-27b): `ok`, `not_on_route` (the place is real, this
    route does not go there — a USEFUL sentence), `not_found` (no stop in
    either dataset), `ambiguous` (several different places; `could_be` is what
    to ask with), `unavailable` (the stop data could not be read, so none of
    the other four would be honest)."""

    status: str
    asked: str = ""
    place: str = ""
    could_be: tuple[str, ...] = ()
    kmb_stop_ids: tuple[str, ...] = ()
    mtr_stop_ids: tuple[str, ...] = ()


def resolve_place(
    query: str,
    *,
    route: str,
    kmb_stops: tuple[Stop, ...] | None,
    mtr_stops: tuple[Stop, ...] | None,
    kmb_route_stop_ids: frozenset[str] | None,
    kmb_runs_route: bool,
    mtr_runs_route: bool,
) -> PlaceResolution:
    """Pure. `None` for a dataset means it could NOT be read — and everything
    below treats unread and empty as different worlds, because they are.

    On-route first, then existence: a name is resolved within the stops this
    route actually serves, and only a miss there consults the full datasets —
    to distinguish «that place is not in the stop data at all» from «that
    place exists, this route just does not go there». The precedence mirrors
    `RoomView` resolving within the room before anything else, for the same
    reason: the bounded set is the one the question was about.
    """
    on_route: list[Stop] = []
    on_route_known = True
    if kmb_runs_route:
        if kmb_stops is None or kmb_route_stop_ids is None:
            on_route_known = False
        else:
            served = tuple(s for s in kmb_stops if s.stop_id in kmb_route_stop_ids)
            on_route.extend(match_stops(served, query))
    if mtr_runs_route:
        if mtr_stops is None:
            on_route_known = False
        else:
            on_route.extend(match_stops(
                tuple(s for s in mtr_stops if s.route == route), query,
            ))

    if on_route:
        matches = tuple(on_route)
        if len(matches) > MAX_PLACE_MATCHES or not same_place(matches):
            names = _distinct_names(matches, query)
            return PlaceResolution(
                status="ambiguous", asked=query, could_be=names[:MAX_CANDIDATES],
            )
        return PlaceResolution(
            status="ok", asked=query, place=matches[0].display_name(query),
            kmb_stop_ids=tuple(s.stop_id for s in matches if s.operator == KMB),
            mtr_stop_ids=tuple(s.stop_id for s in matches if s.operator == MTR),
        )

    if not on_route_known:
        # The route's own stop data could not be read: nothing honest to say.
        return PlaceResolution(status="unavailable", asked=query)

    # The route demonstrably does not serve the name. Does the PLACE exist?
    exists = bool(
        match_stops(kmb_stops or (), query) or match_stops(mtr_stops or (), query),
    )
    if exists:
        return PlaceResolution(status="not_on_route", asked=query)
    if kmb_stops is not None and mtr_stops is not None:
        return PlaceResolution(status="not_found", asked=query)
    # Not found in whichever dataset was readable, with the other unread: «no
    # stop by that name» would be a claim about a file nobody has seen.
    return PlaceResolution(status="unavailable", asked=query)


def _distinct_names(matches: tuple[Stop, ...], query: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for stop in matches:
        seen.setdefault(stop.display_name(query), None)
    return tuple(seen)


class StopDirectory:
    """Both operators' stop datasets, fetched concurrently and cached — the
    same construction, lock, TTL and unread-is-not-empty discipline as
    `RouteCatalogue`, held by the same registry-owned client, because the KMB
    half is 1.2 MB and a per-tag pull of it would be paid on every question
    that names a place."""

    def __init__(
        self,
        *,
        kmb_stops_url: str,
        mtr_stops_url: str,
        timeout_seconds: float,
        ttl_seconds: float = STOP_DIRECTORY_TTL_SECONDS,
    ) -> None:
        self._kmb_stops_url = kmb_stops_url
        self._mtr_stops_url = mtr_stops_url
        self._timeout_seconds = timeout_seconds
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._kmb: tuple[Stop, ...] | None = None
        self._mtr: tuple[Stop, ...] | None = None
        self._fetched_at = 0.0

    async def stops(self) -> tuple[tuple[Stop, ...] | None, tuple[Stop, ...] | None]:
        """`(kmb, mtr)`, refreshing both if stale. `None` means that dataset
        has never been readable — which callers must keep distinct from
        empty (R-27)."""
        async with self._lock:
            if self._stale():
                await self._refresh()
        return self._kmb, self._mtr

    def _stale(self) -> bool:
        if self._kmb is None or self._mtr is None:
            return True
        return (time.monotonic() - self._fetched_at) > self._ttl_seconds

    async def _refresh(self) -> None:
        kmb, mtr = await asyncio.gather(self._fetch_kmb(), self._fetch_mtr())
        if kmb is not None:
            self._kmb = kmb
        if mtr is not None:
            self._mtr = mtr
        if self._kmb is not None and self._mtr is not None:
            self._fetched_at = time.monotonic()

    async def _fetch_kmb(self) -> tuple[Stop, ...] | None:
        body = await get_text(self._kmb_stops_url, self._timeout_seconds)
        if body is None:
            return None
        try:
            payload = json.loads(body)
        except ValueError:
            log.warning("transit stops %s did not return JSON", self._kmb_stops_url)
            return None
        return parse_kmb_stops(payload) or None

    async def _fetch_mtr(self) -> tuple[Stop, ...] | None:
        body = await get_text(self._mtr_stops_url, self._timeout_seconds)
        if body is None:
            return None
        return parse_mtr_stops(body) or None
