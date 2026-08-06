"""Parse a Life360 `/circles/{id}/members` body into typed members, and classify
each into the study's `WireState` enum (design/19 §4.4).

Kept apart from `client.py` because it is pure — dict in, typed value out, no
I/O — so classification is unit-testable against captured wire shapes without a
server, and the client stays about auth and transport.

**The classification is the point.** Life360 reports a member's condition in
several places, and collapsing them loses exactly what §5 says presence must
keep: a member who turned sharing OFF is a known state, not a gap. Order matters
— sharing-off and disconnected are checked BEFORE freshness, because a member
with sharing off may still carry a stale last-known location that would
otherwise read as `stale`.

Wire shapes (from the study; Life360 is loosely typed and ships numbers as
strings): `location.timestamp` is epoch SECONDS as a string; `isDriving`,
`charge`, `features.shareLocation`, `issues.disconnected` are `"1"`/`"0"`
strings; `battery` is a percentage string.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from nora.domain.presence import WireState

log = logging.getLogger("nora.presence.life360")

# A location older than this (relative to the read) is `stale` rather than
# `live`: the phone is sharing but backgrounded or still, so its last report has
# aged. Fifteen minutes is generous — the study measured a stationary phone
# holding one fix for many minutes — and it is the threshold `whereis` degrades
# around too.
STALE_AFTER_SECONDS = 15 * 60


@dataclass(frozen=True)
class Member:
    """One circle member as Life360 reports them, typed and classified.

    `life360_id` is the member id the collector maps to a `person_id` through
    the config circle mapping. The location fields are present only when
    `wire_state` is `live` or `stale`; for every other state they are None, and
    NO fix row is written (its state rides the metric, never a null-island row).
    """

    life360_id: str
    wire_state: WireState
    ts_ms: int | None = None
    lat: float | None = None
    lng: float | None = None
    accuracy_m: float | None = None
    is_driving: bool | None = None
    battery: float | None = None
    address: str | None = None

    @property
    def has_fix(self) -> bool:
        """Whether this member carries a position worth recording — true exactly
        when it is `live`/`stale` and both coordinates parsed."""
        return self.lat is not None and self.lng is not None and self.ts_ms is not None


def parse_members(body: object, *, now_ms: int) -> list[Member]:
    """Every member in a `/members` body, typed and classified.

    Tolerant by construction: a body that is not the expected shape yields `[]`
    (the collector's canary reads an *unexpectedly empty* circle as an alarm, so
    a malformed body degrades to the same signal as an empty one rather than
    raising). A single member that will not parse is skipped, not fatal to the
    others.
    """
    if not isinstance(body, dict):
        return []
    raw_members = body.get("members")
    if not isinstance(raw_members, list):
        return []
    out: list[Member] = []
    for raw in raw_members:
        if isinstance(raw, dict):
            out.append(_parse_one(raw, now_ms=now_ms))
    return out


def _parse_one(raw: dict[str, object], *, now_ms: int) -> Member:
    member_id = str(raw.get("id", ""))
    features = raw.get("features") if isinstance(raw.get("features"), dict) else {}
    issues = raw.get("issues") if isinstance(raw.get("issues"), dict) else {}
    location = raw.get("location") if isinstance(raw.get("location"), dict) else None

    # Sharing off and disconnected are the member's OWN reported condition and
    # are decided before freshness — a member sharing nothing may still carry a
    # stale last location that must not read as `stale`.
    if _flag(features, "shareLocation") is False:
        return Member(life360_id=member_id, wire_state=WireState.SHARING_OFF)
    if _flag(issues, "disconnected") is True:
        return Member(life360_id=member_id, wire_state=WireState.DISCONNECTED)
    if not isinstance(location, dict):
        return Member(life360_id=member_id, wire_state=WireState.NO_FIX)

    ts_ms = _location_ts_ms(location)
    lat = _to_float(location.get("latitude"))
    lng = _to_float(location.get("longitude"))
    if ts_ms is None or lat is None or lng is None:
        # Sharing on, but no usable coordinate/timestamp yet.
        return Member(life360_id=member_id, wire_state=WireState.NO_FIX)

    age_seconds = max(0, (now_ms - ts_ms) // 1000)
    state = WireState.STALE if age_seconds > STALE_AFTER_SECONDS else WireState.LIVE
    return Member(
        life360_id=member_id,
        wire_state=state,
        ts_ms=ts_ms,
        lat=lat,
        lng=lng,
        accuracy_m=_to_float(location.get("accuracy")),
        is_driving=_flag(location, "isDriving"),
        battery=_to_float(location.get("battery")),
        address=_address(location),
    )


def _location_ts_ms(location: dict[str, object]) -> int | None:
    """Life360's `location.timestamp` is epoch SECONDS as a string — convert to
    the milliseconds the store and `messages` both use."""
    raw = location.get("timestamp")
    try:
        return int(float(str(raw)) * 1000)
    except (TypeError, ValueError):
        return None


def _flag(obj: object, key: str) -> bool | None:
    """A Life360 `"1"`/`"0"` (or 1/0) flag as a bool, or None when absent."""
    if not isinstance(obj, dict) or key not in obj:
        return None
    value = str(obj.get(key)).strip()
    if value in ("1", "true", "True"):
        return True
    if value in ("0", "false", "False", ""):
        return False
    return None


def _to_float(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _address(location: dict[str, object]) -> str | None:
    parts = [
        str(location[k])
        for k in ("address1", "address2")
        if location.get(k) not in (None, "")
    ]
    return ", ".join(parts) if parts else None
