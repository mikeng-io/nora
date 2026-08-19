"""R-28's identifier/coordinate egress screen. Applied at every place where
a string that might name a person or carry a raw coordinate is about to
leave the host in a tool argument (Google Places/Routes today; anything
else with the same shape later) — moved out of `nora.agent.tools` so
`nora.app.events.EventService` can apply the identical screen to a venue
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
"""

from __future__ import annotations

import re

from .egress import scan_identifiers as scan_identifiers

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
