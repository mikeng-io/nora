"""`geocode_address` — the fourth deterministic skill (context-channel spec
§3.1). A place's own ADDRESS in, its coordinate out — resolved here, inside
Smith's own sandbox, from data Nora already relayed freely (a business's
address is public; this module never receives a person's name or a raw
coordinate to begin with). The coordinate this returns is model-facing
INSIDE Smith only; it is never sent back to Nora, and Nora's own
`place_info`/`route` never return a coordinate at all (DEC-60/63/64,
untouched by this document).
"""

from __future__ import annotations

import os

import requests

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


class GeocodeError(Exception):
    """Google could not resolve the given address to a place."""


def geocode_address(*, address: str) -> tuple[float, float]:
    api_key = os.environ["GOOGLE_MAPS_API_KEY"]
    response = requests.get(
        GEOCODE_URL, params={"address": address, "key": api_key}, timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "OK" or not data.get("results"):
        raise GeocodeError(f"could not geocode {address!r}: {data.get('status')}")
    location = data["results"][0]["geometry"]["location"]
    return (location["lat"], location["lng"])
