"""R-28's identifier/coordinate egress screen. Applied at every place where
a string that might name a person or carry a raw coordinate is about to
leave the host in a tool argument (Google Places/Routes today; anything
else with the same shape later) — moved out of `nora.agent.tools` so
`nora.app.gatherings.GatheringService` can apply the identical screen to a venue
string without a second, unaudited copy of this logic.

`scan_identifiers` is re-exported here rather than redefined: its body
lives in `nora.agent.egress`, paired there with `scan_reply` over a shared
pattern table, and that module's docstring is explicit that the split
between the two predicates is deliberate and that a third name for either
job is how the wrong one gets called later. Re-exporting keeps that pairing
intact — one source of truth, reachable from here too — instead of forking
a second copy of the pattern table into this module.

`looks_like_coordinate` (formerly `nora.agent.tools._looks_like_coordinate`)
moves here in full: it had no other home, and callers outside `tools.py`
need it without reaching into that 3000+ line tool-definitions module for
one regex predicate.

`screen_and_resolve_place` (Fix I4) finishes what the move above started:
`nora.agent.tools`'s `place_info` tool applied the FULL R-28 screen-call-screen
sandwich (outbound identifier/coordinate/person screen, the actual call
wrapped in `try/except`, and a return-path screen on Google's own `name`/
`address`), but `EventService._resolve_place` only ever got the outbound
half — no person-name check, no `try/except` around the call, and no
return-path screen, so a Google-returned `name`/`address` that itself trips
`scan_identifiers`/`looks_like_coordinate` was written straight into
`venue_name`/`venue_address` and persisted. Both call sites now share this
one function instead of one carrying two-thirds of an audited guard.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .egress import scan_identifiers as scan_identifiers
from .room import _key

log = logging.getLogger(__name__)

# A decimal lat,lng pair — «22.31,114.16» / «22.4106, 114.1245». BOTH numbers
# carry a decimal point, which is what tells a coordinate apart from a street
# address («12 Larkspur Rd» is integers): no legitimate place NAME is written this
# way, so a coordinate in a route argument is a leaked machine position, not
# somewhere a person typed. See `looks_like_coordinate`.
_COORD_RE = re.compile(r"-?\d{1,3}\.\d+\s*,\s*-?\d{1,3}\.\d+")


def looks_like_coordinate(text: str) -> bool:
    """Whether a route argument is a raw coordinate (design/19 §5, R-28).

    This is the outbound barrier for presence: a stored position (from Life360,
    once `whereis` is wired) must never leave in a tool ARGUMENT to Google. A
    person names a place — «沙田廣場», «Yoho Mall» — they do not type a decimal
    lat/lng pair, so blocking the pair costs no real query and closes the one
    exfil shape `scan_identifiers`/`_names_a_person` do not see (council #4,
    2026-08-07). Range is NOT checked: an out-of-range pair is still a leaked
    coordinate, and blocking it too is the safe direction."""
    return _COORD_RE.search(text) is not None


def _names_a_person(text: str, names: frozenset[str]) -> bool:
    """Whether a venue/route argument names a known person. Substring,
    because a home address is phrased AROUND a name (「陳生屋企」), not as
    one. Moved here alongside `looks_like_coordinate` (Fix I4) — it is the
    third leg of the same outbound screen, and `screen_and_resolve_place`
    needs it without a second copy living only in `nora.agent.tools`."""
    key = _key(text)
    return any(name in key for name in names)


class _PlaceSource(Protocol):
    async def place_info(self, name: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class PlaceScreenResult:
    """`screen_and_resolve_place`'s outcome. `status` is one of `"refused"`
    (screened out before any call — see `reason`), `"unavailable"` (no
    `route_source`, or the call itself raised), or `"resolved"` (the call
    completed; `data` is Google's own reply, already return-path-screened —
    it may still carry `data["status"]` of `"ok"`/`"ambiguous"`/
    `"not_found"`, which is the caller's to interpret)."""

    status: str
    reason: str | None = None
    data: dict[str, Any] | None = None


async def screen_and_resolve_place(
    query: str,
    *,
    route_source: _PlaceSource | None,
    person_names: frozenset[str] | None = None,
) -> PlaceScreenResult:
    """The FULL R-28 screen-call-screen sandwich for resolving a free-text
    venue/place name through Google Places — shared by `nora.agent.tools`'s
    `place_info` tool and `nora.app.gatherings.GatheringService._resolve_place`
    (Fix I4).

    Three phases, all load-bearing:
      1. OUTBOUND screen — `scan_identifiers`/`looks_like_coordinate` on the
         query, then (when `person_names` is given) the person-name check: a
         query naming a known person («Tyler's», «陳生屋企») is refused
         before it ever reaches Google, the same as a route argument is.
      2. THE CALL — `route_source.place_info(query)`, wrapped in
         `try/except Exception`: an exception escaping a tool is a silent
         turn (R-5), so it degrades to `unavailable` instead of propagating.
      3. RETURN-PATH screen — Google's own `name`/`address` are screened the
         same way before a caller can persist, serve, or hand them back to
         the model: Google is a third party Nora does not control, and an
         identifier or coordinate that comes BACK is exactly as much a leak
         as one that goes out.

    `person_names` is optional: `EventService` has no per-tag `persons`/room
    the way a tool closure does, so it cannot always build that set itself.
    A caller that has it should always pass it; one without it still gets
    the identifier/coordinate half of the screen, both ways.
    """
    if scan_identifiers(query) is not None or looks_like_coordinate(query):
        return PlaceScreenResult(status="refused", reason="identifier")
    if person_names is not None and _names_a_person(query, person_names):
        return PlaceScreenResult(status="refused", reason="person")
    if route_source is None:
        return PlaceScreenResult(status="unavailable")
    try:
        out = await route_source.place_info(query)
    except Exception:
        log.exception("screen_and_resolve_place: place_info call failed")
        return PlaceScreenResult(status="unavailable")
    out = dict(out)
    if scan_identifiers(str(out.get("name", ""))) is not None:
        out.pop("name", None)
    addr = str(out.get("address", ""))
    if scan_identifiers(addr) is not None or looks_like_coordinate(addr):
        out.pop("address", None)
    return PlaceScreenResult(status="resolved", data=out)
