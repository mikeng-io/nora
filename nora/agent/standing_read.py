"""What `standing_read` shows of a room's Standing head: the sections that
pass, each one only where its own lineage passes.

**Lineage is per section**: `Where it stands`, `I hold open`, `I owe`
and `Found, not yet given` each render only where every entry of that
section's own lineage passes for the calling room; a section that fails is
withheld and the rest is shown. A surprise (`exclude_persons`) withholds every
section at once.

**The revision id is exposed** so a revision can name the position it replaces:
it is `standing_revision.id`, or absent when the chain holds none that IS the
head (`nora.domain.standing.StandingHead`).
"""

from __future__ import annotations

from typing import Any

from nora.agent.lineage import visible_sections
from nora.agent.rollup_read import iso
from nora.domain.lineage import BODY, HOLD_OPEN, I_OWE, WORKING_SET, RenderRoom, room_key
from nora.domain.standing import StandingHead

__all__ = ["render_standing"]

_SECTIONS = (BODY, HOLD_OPEN, I_OWE, WORKING_SET)


def render_standing(head: StandingHead, target: RenderRoom) -> tuple[dict[str, Any], int]:
    """`(the shown position, sections withheld)`. A position with nothing shown is
    `{}` with a count, which the tool answers as «nothing this room may be shown»."""
    standing = head.standing
    allowed = visible_sections(
        standing.lineage, _SECTIONS, target,
        origin=room_key(standing.platform, standing.conversation_id),
        exclude_persons=standing.exclude_persons,
    )
    out: dict[str, Any] = {}
    if allowed[BODY]:
        out["where_it_stands"] = standing.body
    if allowed[HOLD_OPEN] and standing.hold_open is not None:
        out["hold_open"] = standing.hold_open
    if allowed[I_OWE] and standing.i_owe is not None:
        out["i_owe"] = standing.i_owe
    if allowed[WORKING_SET] and standing.working_set:
        out["found_not_yet_given"] = [
            {"what": w.what, "source": w.source, "found": iso(w.found_at),
             **({"bears_on": w.bears_on} if w.bears_on else {})}
            for w in standing.working_set
        ]
    withheld = sum(
        1 for name, present in (
            (BODY, True), (HOLD_OPEN, standing.hold_open is not None),
            (I_OWE, standing.i_owe is not None), (WORKING_SET, bool(standing.working_set)),
        ) if present and not allowed[name]
    )
    if out:
        # `revision` is ABSENT, not null, when no revision IS the head: a null
        # would read as a token of «no revision», an absent key as «none stated».
        out = {
            **({"revision": head.revision_id} if head.revision_id is not None else {}),
            "updated": iso(standing.updated_at), **out,
        }
    return out, withheld
