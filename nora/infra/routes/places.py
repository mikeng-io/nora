"""The place resolver, Google half — a POI NAME becomes coordinates, and NOTHING ELSE.

`route` takes place NAMES (「Yoho Mall」, 「黃大仙祠」, 「打邊爐」) and must turn
each into a coordinate before it can ask Google for a route
(DEC-24: coordinates are Nora's working state and never appear in any schema).
Open transit data (`nora.infra.transit.places`, DEC-41) covers bus STOPS; it
does not cover arbitrary points of interest, so this half exists and it is
Google Places Text Search (New).

**THE SCOPE BOUNDARY LIVES HERE, and it is places only — never a person.**
This is the in-repo half of the hard block: a route query contains
two locations and they LEAVE THE HOST to Google, so 「陳生屋企」 must never be
turned into a home address and sent. The mechanical guarantee has two layers,
and this module is the lower one: it consults **no store that knows a human** —
not `identity.toml`, not Honcho, not the roster, not `Persons`. It calls one
Google endpoint about public places and reads back coordinates. It is
structurally incapable of resolving a person to a location because it holds
nothing that maps a person to one. The UPPER layer — refusing a query that
NAMES a person before this module is ever reached — is R-28, enforced at the
tool seam (`nora.agent.tools`), where the person roster and the identifier
scanner both live. Neither layer is sufficient alone; together they hold the block.

**Ambiguity is an answer, not a guess** — the same discipline as transit's
`near=` (DEC-41). Text Search ranks by relevance, and for a specific name
(「Yoho Mall」) the top result is the place. But 「打邊爐」 legitimately means
hundreds of different hotpot restaurants across the territory, and silently
picking the first is this feature's version of the wrong confident answer. So
results are clustered by proximity to the top hit: everything within
`SAME_PLACE_RADIUS_M` is one place (a mall has several entrances Google returns
as separate points), and `MIN_AMBIGUOUS_OTHERS` or more results FURTHER than
that with different names make the resolution `ambiguous`, with the distinct
names returned as candidates for the model to ask about.

**The dining fields are a cheap bonus, and they are TYPED.** Text Search can
return `rating`, `priceLevel` and `currentOpeningHours.openNow` in the same
call for a few extra field-mask entries, and those are numbers, an enum and a
boolean — closer to `listGroups` than to a web page, so they are NOT wrapped
`untrusted="true"`. Deliberately NOT requested: `reviews`,
which are STRANGER-WRITTEN free text and would have to be wrapped, and which
belong to the later composite dining tool rather than to `route` — keeping them
out of the field mask keeps this both minimal and off the more expensive
Atmosphere billing SKU.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from .http import post_json

log = logging.getLogger("nora.routes")

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# The field mask for a place lookup — ONLY what the resolver reads or the tool
# renders, because every field here is billed. `location` is the whole point
# (it becomes a route waypoint and never leaves this process); `id` and
# `displayName` drive matching and the ambiguity candidates; the last three are
# the typed dining bonus. `reviews` and `editorialSummary` are deliberately
# absent — they are the free-text fields, and their SKU is dearer.
PLACES_FIELD_MASK = (
    "places.id,places.displayName,places.location,places.formattedAddress,"
    "places.rating,places.priceLevel,places.currentOpeningHours.openNow"
)

# A mall or a temple is several points Google returns separately (an entrance,
# a car park, a name plate); ~150 m keeps those as one place. It is the same
# figure and the same argument as transit's `SAME_PLACE_RADIUS_M`. Two hotpot
# restaurants further apart than this are two different answers to 「去邊度」.
SAME_PLACE_RADIUS_M = 150.0

# How many DISTINCT far places (beyond the top hit's cluster) it takes to call a
# name ambiguous. Two — i.e. three distinct places in all — because a single far
# runner-up is, in practice almost always the same place under its other
# name or a landmark's obvious also-ran, not a genuine fork (see `_resolve_from`).
MIN_AMBIGUOUS_OTHERS = 2

# How many candidate names an ambiguous resolution offers back. Enough to ask
# with; not a directory.
MAX_CANDIDATES = 5

# How many results to ask Google for. Small on purpose: the resolver needs the
# top hit and just enough neighbours to tell "one place" from "many", and every
# result past that is field-mask cost for candidates nobody will read.
MAX_RESULTS = 5


@dataclass(frozen=True)
class Place:
    """One resolved place. `lat`/`lng` are Nora's working state and never appear
    in any model-facing schema (DEC-24); they exist to become a route waypoint.

    The three dining fields are all optional because Text Search omits any it
    has no value for — a bus terminus has no `rating` — and each is typed:
    `rating` a float, `price_level` Google's enum string, `open_now` a bool."""

    place_id: str
    name: str
    address: str
    lat: float
    lng: float
    rating: float | None = None
    price_level: str = ""
    open_now: bool | None = None

    def dining(self) -> dict[str, Any]:
        """The typed dining fields present, as a bounded dict. Empty when the
        place carries none — a stop or a street corner."""
        out: dict[str, Any] = {}
        if self.rating is not None:
            out["rating"] = self.rating
        if self.price_level:
            out["price_level"] = self.price_level
        if self.open_now is not None:
            out["open_now"] = self.open_now
        return out


@dataclass(frozen=True)
class PlaceResolution:
    """What became of one place name. Four statuses, the routing analogue of
    transit's `PlaceResolution` (DEC-41):

    * `ok` — `place` is the resolved point (coordinates carried internally).
    * `ambiguous` — the name matches several distinct places; `could_be` lists
      them for the model to ask about, and `place` is unset.
    * `not_found` — Text Search returned zero results for a readable query.
    * `unavailable` — the call failed, so none of the other three would be
      honest (R-27's hinge, applied to Google).
    """

    status: str
    asked: str = ""
    place: Place | None = None
    could_be: tuple[str, ...] = ()


class PlaceResolver:
    """Google Places Text Search (New), and nothing that knows a human.

    Constructed once per process and held by `RouteClient`. No cache: unlike the
    transit stop datasets (one 1.2 MB file re-pulled per tag), each lookup is a
    distinct small query for a distinct name, so there is nothing static to
    hold — every call is a fresh search.
    """

    def __init__(
        self,
        *,
        api_key: str,
        search_url: str = PLACES_SEARCH_URL,
        timeout_seconds: float,
        region_code: str = "HK",
        language_code: str = "zh-HK",
    ) -> None:
        self._api_key = api_key
        self._search_url = search_url
        self._timeout_seconds = timeout_seconds
        self._region_code = region_code
        self._language_code = language_code

    async def resolve(self, query: str) -> PlaceResolution:
        """One place name to a `PlaceResolution`. NEVER raises.

        `regionCode` and `languageCode` bias the search to Hong Kong and to the
        script the group speaks, so 「黃大仙祠」 resolves to the temple in Wong
        Tai Sin rather than a same-named place elsewhere.
        """
        text = str(query or "").strip()
        if not text:
            return PlaceResolution(status="not_found", asked=text)
        payload = await post_json(
            self._search_url,
            api_key=self._api_key,
            field_mask=PLACES_FIELD_MASK,
            body={
                "textQuery": text,
                "regionCode": self._region_code,
                "languageCode": self._language_code,
                "maxResultCount": MAX_RESULTS,
            },
            timeout_seconds=self._timeout_seconds,
        )
        if payload is None:
            return PlaceResolution(status="unavailable", asked=text)
        places = parse_places(payload)
        if not places:
            # A readable 200 with no results — Google searched and found
            # nothing. Distinct from `unavailable`: the query was answered.
            return PlaceResolution(status="not_found", asked=text)
        return _resolve_from(text, places)


def parse_places(payload: Any) -> tuple[Place, ...]:
    """The Text Search envelope -> Places. NEVER raises; a row that does not
    parse (no coordinate) is dropped rather than costing the whole result."""
    rows = payload.get("places") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return ()
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        location = row.get("location")
        if not isinstance(location, dict):
            continue
        position = _coordinates(location.get("latitude"), location.get("longitude"))
        if position is None:
            continue
        display = row.get("displayName")
        name = display.get("text") if isinstance(display, dict) else ""
        opening = row.get("currentOpeningHours")
        open_now = opening.get("openNow") if isinstance(opening, dict) else None
        rating = row.get("rating")
        out.append(Place(
            place_id=str(row.get("id") or ""),
            name=str(name or "").strip(),
            address=str(row.get("formattedAddress") or "").strip(),
            lat=position[0], lng=position[1],
            rating=float(rating) if isinstance(rating, (int, float)) else None,
            price_level=str(row.get("priceLevel") or "").strip(),
            open_now=open_now if isinstance(open_now, bool) else None,
        ))
    return tuple(out)


def _resolve_from(query: str, places: tuple[Place, ...]) -> PlaceResolution:
    """Google ranks by relevance, so the top hit is the answer for a NAMED place;
    ambiguity is reserved for a name that is really a CATEGORY.

    Everything within `SAME_PLACE_RADIUS_M` of the top hit is one place (a
    venue's several entrances). Among the rest, distinct far places are counted
    BY NAME — and only `MIN_AMBIGUOUS_OTHERS` or more of them makes the query
    ambiguous.

    **The threshold is set against the live API.** A pure proximity-cluster
    rule — ambiguous on the FIRST distinct far result — would flag named
    landmarks such as 「黃大仙祠」 (two real temples, the famous one ranked first),
    「香港迪士尼樂園」 (the Chinese and English names of the SAME resort, ~200 m
    apart), and 「Yoho Mall」 (its named phases). Each has an obvious top result
    a person means, so returning candidates there is noise, not care. A genuine
    category — 「打邊爐」, hotpot places scattered across districts — returns
    many distinct far results and still resolves to `ambiguous`. Trusting
    Google's top hit for a named place, and asking only when the field is genuinely
    crowded, is what keeps the tool usable on real Hong Kong queries.
    """
    top = places[0]
    far = [
        p for p in places[1:]
        if _metres(top, p) > SAME_PLACE_RADIUS_M and _distinct_name(top, p)
    ]
    distinct_far = {p.name.casefold().strip() for p in far if p.name}
    if len(distinct_far) >= MIN_AMBIGUOUS_OTHERS:
        names = _distinct_names((top, *far), query)
        return PlaceResolution(
            status="ambiguous", asked=query, could_be=names[:MAX_CANDIDATES],
        )
    return PlaceResolution(status="ok", asked=query, place=top)


def _distinct_name(a: Place, b: Place) -> bool:
    """Two results with the same display name are the same place returned
    twice, not an ambiguity — a franchise's two entrances, say."""
    return a.name.casefold().strip() != b.name.casefold().strip()


def _distinct_names(places: tuple[Place, ...], query: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for place in places:
        label = place.name or place.address or query
        seen.setdefault(label, None)
    return tuple(seen)


def _metres(a: Place, b: Place) -> float:
    """Equirectangular metres between two places. Hong Kong spans half a degree,
    so the error is centimetres — the same approximation transit's `same_place`
    uses."""
    dy = (b.lat - a.lat) * 111_320.0
    dx = (b.lng - a.lng) * 111_320.0 * math.cos(math.radians(a.lat))
    return math.hypot(dx, dy)


def _coordinates(lat: Any, lng: Any) -> tuple[float, float] | None:
    try:
        return float(lat), float(lng)
    except (TypeError, ValueError):
        return None
