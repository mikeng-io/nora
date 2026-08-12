"""`render_map` — the third deterministic skill (this plan's Architecture
note): a list of (lat, lng) points in, one Google Static Maps API call, an
inline base64-embedded `<img>` tag out. Static, not the Google Maps
JavaScript API -- no live map JS ships in the artifact by default (Nora
Compute spec §8's self-contained-by-default rule); an interactive map is the
allowlisted exception, not this tool's job.
"""

from __future__ import annotations

import base64
import os

import requests

STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"


def render_map(*, locations: list[tuple[float, float]], title: str = "") -> str:
    api_key = os.environ["GOOGLE_MAPS_API_KEY"]
    markers = "|".join(f"{lat},{lng}" for lat, lng in locations)
    params = {
        "size": "640x400",
        "markers": markers,
        "key": api_key,
    }
    response = requests.get(STATIC_MAPS_URL, params=params, timeout=15)
    response.raise_for_status()
    encoded = base64.b64encode(response.content).decode("ascii")
    alt = title or "map"
    return f'<img src="data:image/png;base64,{encoded}" alt="{alt}" />'
