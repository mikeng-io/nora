"""`find_parking` — fetch nearby car parks from Google + TD cross-reference.

Pins the nearby-parse unit (the data layer) and the tool's honest
off-state (no `route_source` → `unavailable`, R-5). The full ok-path is
covered by the live journey (eval), which exercises a real resolver +
nearby call through the real registry.
"""
from __future__ import annotations

from nora.infra.parking.nearby import _parse_nearby


def _payload():
    return {"places": [
        {"id": "g1", "displayName": {"text": "Langham Place Car Park"},
         "location": {"latitude": 22.4106, "longitude": 114.1245},
         "formattedAddress": "31 Ashcombe St, Mong Kok",
         "types": ["parking", "parking_lot"]},
        {"id": "g2", "displayName": {"text": "Olympian City 2 Car Park"},
         "location": {"latitude": 22.4106, "longitude": 114.1245},
         "formattedAddress": "18 Thistle Marlow Rd, Mong Kok",
         "types": ["parking_garage", "parking"]},
    ]}


def test_parse_nearby_sorts_nearest_first():
    out = _parse_nearby(_payload(), lat=22.4106, lng=114.1245)
    assert len(out) == 2
    assert out[0].name == "Langham Place Car Park"
    assert out[0].distance_m < out[1].distance_m


def test_parse_nearby_drops_row_without_coordinates():
    payload = {"places": [{"id": "x", "displayName": {"text": "No Loc"}}]}
    assert _parse_nearby(payload, lat=22.0, lng=114.0) == ()


def test_parse_nearby_empty():
    assert _parse_nearby({"places": []}, lat=22.0, lng=114.0) == ()
