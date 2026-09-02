"""Parking value types — what the parking ports return.

Pure data, no I/O: the live fetchers (`nora.infra.parking`, Google Places via
`nora.infra.routes`) build these, and the canned eval doubles
(`nora.testing.eval_backends`) construct the same shapes — the domain layer is
the one place BOTH sides may import, so the records live here rather than
beside either implementation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NearbyParking:
    """One nearby car park, as `find_parking` renders it.

    Coordinates are Nora's working state (DEC-24) — the model never sees them;
    `distance_m` is the human proxy (「300 米」).
    """

    place_id: str
    name: str
    address: str
    lat: float
    lng: float
    distance_m: int


@dataclass(frozen=True)
class StreetParking:
    """What `find_parking` renders for on-street metered parking near a point."""

    total: int
    vacant: int
    streets: tuple[str, ...]
