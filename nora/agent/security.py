"""R-28's identifier/coordinate egress screen. Applied at every place where
a string that might name a person or carry a raw coordinate is about to
leave the host in a tool argument (Google Places/Routes), so that
`nora.agent.tools` and `nora.app.gatherings.GatheringService` apply the
identical screen to a venue string without a second copy of this logic.

`scan_identifiers` is re-exported here rather than redefined: its body
lives in `nora.agent.egress`, paired there with `scan_reply` over a shared
pattern table, and that module's docstring says the split between the two
predicates is deliberate and that a third name for either job is how the wrong
one gets called later. Re-exporting keeps one source of truth.

`looks_like_coordinate` lives here so callers outside `tools.py` can use it
without reaching into that tool-definitions module for one regex predicate.

`screen_and_resolve_place` is the FULL R-28 screen-call-screen sandwich
(outbound identifier/coordinate/person screen, the call wrapped in
`try/except`, and a return-path screen on Google's own `name`/`address`).
`nora.agent.tools`'s `place_info` tool and
`GatheringService._resolve_place` share it, so a Google-returned `name` or
`address` that itself trips `scan_identifiers`/`looks_like_coordinate` is never
persisted as `venue_name`/`venue_address`.
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
# A comma OR plain whitespace between the two numbers: Google Text Search locates a bare pair
# written either way.
_COORD_RE = re.compile(r"-?\d{1,3}\.\d+(?:\s*,\s*|\s+)-?\d{1,3}\.\d+")


def looks_like_coordinate(text: str) -> bool:
    """Whether a route argument is a raw coordinate (R-28).

    This is the outbound barrier for presence: a stored position (from Life360)
    must never leave in a tool ARGUMENT to Google. A person names a place —
    «沙田廣場», «Yoho Mall» — they do not type a decimal lat/lng pair, so
    blocking the pair costs no real query and closes the one exfil shape
    `scan_identifiers`/`_names_a_person` do not see. Range is NOT checked: an
    out-of-range pair is still a leaked coordinate, and blocking it too is the
    safe direction."""
    return _COORD_RE.search(text) is not None


#: What lands on a span in place of text the egress floor refuses. It NAMES the
#: pattern that fired, because «the trace shows nothing» and «the trace refused
#: to show it» are different readings and only one of them is a bug.
TRACE_WITHHELD = "(withheld from the trace: {reason})"


def trace_safe(text: str) -> tuple[str, str]:
    """`(what may go on a span, why the rest did not)`.

    **A trace is an egress.** The collector is a third party over the network,
    and `nora.agent.egress`'s own docstring says an identifier is «never safe to emit
    **or transmit**, whatever the destination». A prompt carries a turn's raw
    tool results — a `where_is` result is a live coordinate — so the two floor
    predicates that guard every other write of that class run here too.

    **Reused, not reinvented**, and deliberately the same pair
    `nora.agent.dream_screen` calls rather than `screen` itself: `screen` counts
    `nora_dream_screened_total{adaptor}`, and a trace decision incrementing a
    Dreamer series would make that counter answer two questions at once.
    `scan_identifiers` and not `scan_reply`, for `dream_screen`'s stated reason
    — `scan_reply`'s model-name pattern matches nine ordinary English words and
    is only ever right for text addressed to the group.

    **Fails closed at whole-text granularity**, which is the known cost: one
    coordinate anywhere in a prompt withholds the whole prompt. The provenance
    survives it — a caller stamps a `*_sha256` either way and the turn's own
    row is retained in Postgres — so the text is re-derivable by reference
    exactly when it is not readable inline. Per-block redaction would be finer
    but is a larger change.

    **It lives HERE rather than in `nora.obs.tracing`** because `nora.obs` sits
    BELOW `nora.agent` in the import contract, so the tracing helper cannot
    reach these predicates. The screen is therefore the CALLER's obligation at
    every generation span (`docs/observability.md`), and `generation`'s
    `prompt`/`completion` parameters take already-screened text.
    """
    offence = scan_identifiers(text)
    if offence is not None:
        return TRACE_WITHHELD.format(reason=offence), offence
    if looks_like_coordinate(text):
        return TRACE_WITHHELD.format(reason="coordinate"), "coordinate"
    return text, ""


def _names_a_person(text: str, names: frozenset[str]) -> bool:
    """Whether a venue/route argument names a known person. Substring,
    because a home address is phrased AROUND a name (「陳生屋企」), not as
    one. It is the third leg of the same outbound screen with
    `scan_identifiers` and `looks_like_coordinate`."""
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


def _refused_before_the_call(query: str, person_names: frozenset[str] | None) -> str | None:
    """Why a place query may not leave the host, or `None`: an identifier or a coordinate
    (`identifier`), or a known person's name (`person`). The outbound half of R-28, shared
    by `screen_and_resolve_place` and `screen_and_locate_place` so the two cannot drift."""
    if scan_identifiers(query) is not None or looks_like_coordinate(query):
        return "identifier"
    if person_names is not None and _names_a_person(query, person_names):
        return "person"
    return None


class PlaceLocator(Protocol):
    async def locate(self, name: str) -> dict[str, Any]: ...


async def screen_and_locate_place(
    query: str,
    *,
    place_locator: PlaceLocator | None,
    person_names: frozenset[str] | None = None,
) -> PlaceScreenResult:
    """`screen_and_resolve_place`'s twin for a SERVICE that needs the coordinate: the
    same outbound screen, the call wrapped so it never raises, and the same return-path screen on
    Google's name, but the reply carries `lat` and `lng`. Only a caller that stores the pair may
    use it; its result must never reach a tool result (DEC-24)."""
    reason = _refused_before_the_call(query, person_names)
    if reason is not None:
        return PlaceScreenResult(status="refused", reason=reason)
    if place_locator is None:
        return PlaceScreenResult(status="unavailable")
    try:
        out = await place_locator.locate(query)
    except Exception:
        log.exception("screen_and_locate_place: locate call failed")
        return PlaceScreenResult(status="unavailable")
    out = dict(out)
    if scan_identifiers(str(out.get("name", ""))) is not None:
        out.pop("name", None)
    return PlaceScreenResult(status="resolved", data=out)


async def screen_and_resolve_place(
    query: str,
    *,
    route_source: _PlaceSource | None,
    person_names: frozenset[str] | None = None,
) -> PlaceScreenResult:
    """The FULL R-28 screen-call-screen sandwich for resolving a free-text
    venue/place name through Google Places — shared by `nora.agent.tools`'s
    `place_info` tool and `nora.app.gatherings.GatheringService._resolve_place`.

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

    `person_names` is optional: `GatheringService` has no per-tag `persons`/room
    the way a tool closure does, so it cannot always build that set itself.
    A caller that has it should always pass it; one without it still gets
    the identifier/coordinate half of the screen, both ways.
    """
    reason = _refused_before_the_call(query, person_names)
    if reason is not None:
        return PlaceScreenResult(status="refused", reason=reason)
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
