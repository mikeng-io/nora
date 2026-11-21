"""Parsing Life360's `/circles/{id}/places` body into `SavedPlace`s.

The wire shape:

    {"places": [{"id": …, "ownerId": …, "circleId": …, "name": "Home",
                 "latitude": "22.4…", "longitude": "114.0…", "radius": "80",
                 "type": null, "typeLabel": null}, …]}

Coordinates and radius arrive as strings. An unparseable entry leaves
coverage partial even when the other entries parse.
"""

from __future__ import annotations

import logging
import math

from nora.domain.presence import SavedPlace

log = logging.getLogger("nora.life360.places")

__all__ = ["parse_places"]


def parse_places(
    body: object, *, circle_id: str, now_ms: int,
) -> tuple[list[SavedPlace], bool]:
    """Type the places for the requested circle and report partial coverage."""
    if not isinstance(body, dict) or not isinstance(body.get("places"), list):
        return [], True
    out: list[SavedPlace] = []
    seen: set[str] = set()
    partial = False
    for raw in body["places"]:
        if not isinstance(raw, dict):
            partial = True
            continue
        place_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        lat = _to_float(raw.get("latitude"))
        lng = _to_float(raw.get("longitude"))
        if not place_id or not name or lat is None or lng is None:
            partial = True
            log.debug("skipping unparseable place (id=%r present=%s)",
                      bool(place_id), lat is not None and lng is not None)
            continue
        if place_id in seen:
            continue
        seen.add(place_id)
        out.append(
            SavedPlace(
                circle_id=circle_id,
                place_id=place_id,
                name=name,
                lat=lat,
                lng=lng,
                address=_address(raw),
                radius_m=_to_float(raw.get("radius")),
                updated_ms=now_ms,
            )
        )
    return out, partial


def _address(place: dict[str, object]) -> str | None:
    """Life360's /places wire address, assembled the same way the member-fix
    `_address` does (nora/infra/life360/members.py) — address1 then address2,
    joined with ", " when both are present, None when neither resolves."""
    parts = [str(place[k]).strip() for k in ("address1", "address2")
             if str(place.get(k) or "").strip()]
    if not parts:
        return None
    return ", ".join(parts)


def _to_float(value: object) -> float | None:
    """Parse a stringly-typed wire number, rejecting non-finite values: a wire
    `radius: "Infinity"` would otherwise store `inf`, and `_within_place`'s
    `d <= radius_m` then reports EVERY fix on Earth as inside that place.
    `nan`/`inf`/`-inf` all become None."""
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
