"""GET /app/v1/presence, GET /app/v1/presence/{person_id}/history — circle
members' live and historical positions for the web portal's map.

Deliberately does NOT apply `nora.agent.whereis.DisclosureGuard`/R-21: that
guard answers "may THIS ROOM be told?", and a portal page is not a
conversation — there is no room roster to check a circle against. Web
presence visibility is instead gated the same way every other portal page
already is: `checkSession()`'s identity.toml allowlist, enforced by
`requireApiSession()` before either handler below is ever reached. Every
logged-in friend sees every circle member here, matching status/memory's
existing group-wide model — a deliberate product decision, not an
oversight. See `Dependencies.presence`'s docstring in
`nora/app/composition.py` for the fuller reasoning.

`presence=None` (either `[presence] enabled = false`, or identity.toml
yields no circle — see `nora.app.composition`) makes both endpoints answer
the R-27 shape: an empty list / 404, never an error — there genuinely is
nothing to show, not a read that failed.
"""

from __future__ import annotations

import time
from typing import Any

from nora.agent.whereis import Circle, PresenceReader
from nora.domain.presence import PresenceFix, haversine_distance_m

# Consecutive fixes within this radius of the last EMITTED point
# collapse into it, so a stationary phone's GPS jitter doesn't zigzag the
# rendered route. Matches `nora.agent.whereis`'s `_MOVEMENT_FLOOR_M` in
# spirit (a noise floor under real-world GPS drift) but is a distinct,
# smaller constant — that one gates whether Nora SAYS "moving" in a
# sentence, this one gates whether the map draws a new vertex.
_STAY_POINT_RADIUS_M = 10.0

# Upper bound on a history request's window and row count — defends the
# shared engine `PresenceStore` sits on (composition.py: "the reader shares
# the audit engine — one pool, one lifecycle") against an authenticated
# request scanning a person's ENTIRE retained history in one call. 30 days
# is generous for a map's "recent route" use case; the frontend's own
# HOUR_OPTIONS tops out at 168h (7d).
_MAX_HISTORY_HOURS = 24 * 30
_MAX_HISTORY_POINTS = 5000


def _fix_to_dict(fix: PresenceFix, *, now_ms: int) -> dict[str, Any]:
    return {
        "lat": fix.lat,
        "lng": fix.lng,
        "ts": fix.ts,
        "ageSeconds": max(0, (now_ms - fix.ts) // 1000),
        "wireState": fix.wire_state.value,
        **({"batteryPercent": fix.battery} if fix.battery is not None else {}),
        **({"isDriving": fix.is_driving} if fix.is_driving is not None else {}),
        **({"speedKmh": fix.speed_kmh} if fix.speed_kmh is not None else {}),
        **({"address": fix.address} if fix.address else {}),
    }


async def build_presence_payload(
    *, presence: PresenceReader | None, circles: tuple[Circle, ...],
) -> list[dict[str, Any]]:
    """One entry per known circle member (resolved via identity.toml — an
    unmapped Life360 account has no name to show and is skipped, same as
    `Circle.people`'s own construction), with their latest fix if any.

    Deduped by `person_id` across circles, one ENTRY per person — but the
    fix itself is the most recent across EVERY circle they belong to, not
    just the first (`_latest_fix_across`). identity.toml can place one real
    person in more than one circle, and the collector may have written
    their most recent fix under either `circle_id` — checking only the
    first circle would render `current: null` for someone with a real,
    recent fix recorded under the other one."""
    if presence is None:
        return []
    now_ms = int(time.time() * 1000)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for circle in circles:
        for ref in circle.people:
            if ref.person_id in seen:
                continue
            seen.add(ref.person_id)
            fix = await _latest_fix_across(presence, circles, ref.person_id)
            out.append(
                {
                    "personId": ref.person_id,
                    "displayName": ref.name,
                    "current": _fix_to_dict(fix, now_ms=now_ms) if fix is not None else None,
                }
            )
    return out


def _circles_for(circles: tuple[Circle, ...], person_id: str) -> list[Circle]:
    return [c for c in circles if person_id in c.person_ids]


async def _latest_fix_across(
    presence: PresenceReader, circles: tuple[Circle, ...], person_id: str,
) -> PresenceFix | None:
    """The most recent fix for `person_id` across EVERY circle they belong
    to — a fix lives under whichever `circle_id` the collector wrote it
    under, which need not be the first circle in `circles`' order."""
    candidates = [
        fix
        for circle in _circles_for(circles, person_id)
        if (fix := await presence.latest_fix(circle_id=circle.circle_id, person_id=person_id))
        is not None
    ]
    return max(candidates, key=lambda f: f.ts) if candidates else None


def _snap_stay_points(fixes: list[PresenceFix]) -> list[dict[str, Any]]:
    """Oldest-first `fixes` (the store's own ordering) reduced to a polyline
    where a run of fixes within `_STAY_POINT_RADIUS_M` of the last emitted
    point contributes no new vertex — the point "stays at the same
    location" rather than jittering."""
    out: list[dict[str, Any]] = []
    anchor: PresenceFix | None = None
    for fix in fixes:
        if (
            anchor is not None
            and haversine_distance_m(anchor.lat, anchor.lng, fix.lat, fix.lng)
            <= _STAY_POINT_RADIUS_M
        ):
            continue
        anchor = fix
        out.append({"lat": fix.lat, "lng": fix.lng, "ts": fix.ts})
    return out


async def build_presence_places_payload(
    *, presence: PresenceReader | None, circles: tuple[Circle, ...],
) -> list[dict[str, Any]]:
    """Every circle's saved places (Life360's named landmarks — "Home",
    etc.), deduped by name: a person in more than one circle can otherwise surface the same
    household place twice. Distinct from `build_presence_payload`'s person
    entries by TYPE, not just by field shape (`nora.domain.presence`'s own
    module docstring: a fix is where a PERSON was seen, a place is
    somewhere the household chose to name) — the map renders them as
    visually distinct markers for exactly that reason."""
    if presence is None:
        return []
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for circle in circles:
        for place in await presence.places_for(circle_id=circle.circle_id):
            if place.name in seen:
                continue
            seen.add(place.name)
            out.append({"name": place.name, "lat": place.lat, "lng": place.lng})
    return out


async def build_presence_history_payload(
    *, presence: PresenceReader | None, circles: tuple[Circle, ...],
    person_id: str, hours: int,
) -> dict[str, Any] | None:
    """`person_id`'s fix history over the last `hours`, stay-point-snapped.
    `None` means "no such circle member" (a 404) — distinct from a real,
    empty history (`points: []`, a 200) for someone with no fixes yet.

    `hours` is clamped to `_MAX_HISTORY_HOURS` regardless of what the
    caller asked for — the route layer's own validation only rejects
    non-positive values, so this is the actual upper bound.

    Windowed across EVERY circle `person_id` belongs to, merged and
    re-sorted oldest-first before snapping — same reasoning as
    `_latest_fix_across`: their fixes may live under more than one
    `circle_id`, and reading only the first circle would return an
    empty route for someone with real history recorded elsewhere."""
    matching_circles = _circles_for(circles, person_id)
    if not matching_circles:
        return None
    hours = min(hours, _MAX_HISTORY_HOURS)
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - hours * 3_600_000
    fixes: list[PresenceFix] = []
    if presence is not None:
        for circle in matching_circles:
            fixes.extend(
                await presence.window(
                    circle_id=circle.circle_id, person_id=person_id,
                    since_ts=since_ms, until_ts=now_ms,
                )
            )
        fixes.sort(key=lambda f: f.ts)
    return {
        "hours": hours,
        "from": since_ms,
        "to": now_ms,
        "points": _snap_stay_points(fixes[-_MAX_HISTORY_POINTS:]),
    }
