"""Platform-neutral presence contracts. Standard library only —
the same framework-free rule as the rest of `nora.domain`.

Two types, and the split matters:

- `WireState` is how ONE read of a Life360 member classified — a typed enum.
  It is recorded on every stored fix and emitted per member per
  cycle, so «this member turned sharing off» is a KNOWN STATE, not a gap.
  `unavailable` is the read-level failure — the daemon could not read the member
  at all — and it must never be summed with `sharing_off`/`no_fix`, which are
  the member's own reported condition. Collapsing «I could not look» into «they
  are not sharing» is R-5's shape aimed at a location, and it is exactly the
  ambiguity the collector's canary exists to keep visible.

- `PresenceFix` is one stored row — a DISTINCT location fix, written only when
  the member's `location.timestamp` advanced. `ts` is
  epoch MILLISECONDS (Life360's own unit and the reason the column is BIGINT),
  and `recorded_at` is when Nora wrote the row — the two are kept
  apart because a stationary phone holds one fix for many minutes, so `ts` is the
  age of the position and `recorded_at` is the age of the observation. `whereis`
  carries the `ts` age to the model, never `recorded_at`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

_EARTH_RADIUS_M = 6_371_000.0


def haversine_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points, in metres. The one distance
    calculation every presence-reading caller needs (`whereis`'s movement/
    place-containment reads, the web map's stay-point snapping) — kept here,
    not duplicated per caller, so a change to the formula is one edit."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


class WireState(StrEnum):
    """How one member read classified (the Life360 wire-state enum).

    `StrEnum` so the stored `wire_state` column round-trips through
    `WireState(row.wire_state)` and so a metric label is the bare word
    (`"live"`) rather than `WireState.LIVE`.
    """

    # The phone reported a fresh position and is sharing it.
    LIVE = "live"
    # Sharing, but the last position is old — the phone is backgrounded or still.
    STALE = "stale"
    # The member has location sharing turned OFF. A KNOWN state, not a gap.
    SHARING_OFF = "sharing_off"
    # The phone is unreachable (offline, no signal) — Life360 says so.
    DISCONNECTED = "disconnected"
    # Sharing is on but there is no position at all yet (never located).
    NO_FIX = "no_fix"
    # The DAEMON could not read this member — a read-level failure, never the
    # member's own condition. Held apart from the five above on purpose.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SavedPlace:
    """One circle's named landmark («Home»), as stored and as read.

    A STATIC household name for a position — deliberately a different type from
    `PresenceFix`: a fix is where a PERSON was seen (surveillance-shaped,
    R-31-guarded); a place is somewhere the household chose to name, so they
    never share a type. `radius_m` is Life360's geofence radius; nothing reads
    it yet.
    """

    circle_id: str
    place_id: str
    name: str
    lat: float
    lng: float
    updated_ms: int               # when Nora last saw this place on the wire
    address: str | None = None    # Life360 wire address1/address2, when present
    radius_m: float | None = None


@dataclass(frozen=True)
class PresenceFix:
    """One distinct location fix for one circle member, as stored and as read.

    Mirrors `TranscriptRow`: a typed row the store produces and `whereis`
    consumes, never a `dict[str, Any]`. `lat`/`lng` are always present — a fix
    is a position by definition, and a member with no position produces no fix
    (its state is carried by `WireState` and the per-cycle metric instead, not
    by a null-island row). The optional fields are optional because Life360
    omits them for a member who is not driving, whose phone did not report a
    battery, or whose coordinate did not reverse-geocode. `speed_kmh` is
    Life360's own reported ground speed, converted from its wire m/s at parse
    time (`nora.infra.life360.members`); Life360's `-1` "unknown" sentinel is
    already collapsed to `None` there, so a fix carrying this field is a real
    speed, never a stand-in for stationary.
    """

    circle_id: str
    person_id: str
    ts: int                       # epoch MILLISECONDS — the position's own age
    lat: float
    lng: float
    wire_state: WireState
    recorded_at: int              # epoch MILLISECONDS — when Nora wrote the row
    accuracy_m: float | None = None
    is_driving: bool | None = None
    battery: float | None = None
    address: str | None = None
    speed_kmh: float | None = None
