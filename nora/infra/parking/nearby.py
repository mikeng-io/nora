"""Google Places Nearby Search for parking — `find_parking`'s data source.

One POST to `places:searchNearby` with `includedTypes: ["parking"]` inside a
location circle, so a named place (「銅鑼灣」) or a current position becomes a
short list of nearby car parks with name + address + distance. This is the
FIND layer: it answers «附近邊度有停車場»; the TD feed's `parking_status`
(per-parking vacancy) is the CHECK layer that follows when one of these is
the actual spot.

Uses the same `post_json` helper and Google key as `PlaceResolver` (nora/
infra/routes/places.py) — the key is already restricted to
Routes + Places + Geocoding, and the field mask keeps billing to the fields
actually rendered.

Nothing here raises; any failure collapses to `None` (the tool reports
`unavailable`, R-5 — a parking search that fails is never a fabricated list).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from ...domain.parking import NearbyParking as NearbyParking
from ..routes.http import post_json

log = logging.getLogger("nora.parking")

NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"

# Only what `find_parking` renders: identity, name, address and coordinates.
NEARBY_FIELD_MASK = (
    "places.id,places.displayName,places.location,places.formattedAddress"
)

# A parking search circle: 1500 m — walkable from a restaurant table. Parking
# is a coarser object than a venue (a multi-storey is one result serving a
# whole district), so the radius is wider than a typical nearby search.
SEARCH_RADIUS_M = 1500.0

# How many results to ask Google for. Ten is the cap the tool renders; more is
# field-mask cost for names nobody will read, and the model needs the NEAREST
# handful to actually drive/walk to, not a directory.
MAX_RESULTS = 10


async def search_nearby(
    *,
    api_key: str,
    lat: float,
    lng: float,
    radius_m: float = SEARCH_RADIUS_M,
    max_results: int = MAX_RESULTS,
    timeout_seconds: float,
    url: str = NEARBY_URL,
) -> tuple[NearbyParking, ...] | None:
    """Car parks within `radius_m` of (lat, lng). None = the call failed; an
    empty tuple = Google searched and found nothing. NEVER raises."""
    body = {
        "includedTypes": ["parking"],
        "maxResultCount": max_results,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius_m,
            }
        },
    }
    payload = await post_json(
        url, api_key=api_key, field_mask=NEARBY_FIELD_MASK,
        body=body, timeout_seconds=timeout_seconds,
    )
    if payload is None:
        return None
    return _parse_nearby(payload, lat=lat, lng=lng)


def _parse_nearby(payload: Any, *, lat: float, lng: float) -> tuple[NearbyParking, ...]:
    """The searchNearby envelope -> nearby car parks, sorted nearest-first.
    NEVER raises; a row without coordinates is dropped (it is not a place)."""
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
        try:
            plat = float(location.get("latitude") or 0.0)
            plng = float(location.get("longitude") or 0.0)
        except (TypeError, ValueError):
            continue
        if not (plat and plng):
            continue
        display = row.get("displayName")
        name = display.get("text") if isinstance(display, dict) else ""
        out.append(NearbyParking(
            place_id=str(row.get("id") or ""),
            name=str(name or "").strip(),
            address=str(row.get("formattedAddress") or "").strip(),
            lat=plat, lng=plng,
            distance_m=int(_metres(lat, lng, plat, plng)),
        ))
    out.sort(key=lambda p: p.distance_m)
    return tuple(out)


def _metres(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Equirectangular metres — same approximation `nora.infra.routes.places`
    uses; Hong Kong spans half a degree, error is centimetres."""
    dy = (lat2 - lat1) * 111_320.0
    dx = (lng2 - lng1) * 111_320.0 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)
