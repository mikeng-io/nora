"""Renders one conversation's `Standing` into the `<standing>` block — Nora's
own position, handed back to her at the top of the next turn.

**This is the one injected block that is HERS**, and every other one is about
somebody else: `<notes>` is what the room has not settled, and
`<group_transcript>` is what it said. `<standing>` is what she concluded last
turn. That difference is why the
block note below does not reuse `ROOM_BLOCK_NOTE` — «distilled from this room's
own messages» is false here for a reason that matters: a position she wrote is
not evidence about the room, and reading it as evidence is how a guess she made
once becomes a thing she believes she observed.

**And it still HEDGES ITSELF.** That looks contradictory beside «this is
hers» and it is the single most important line in this module. Standing is
folded from `tag_trace.messages`, which holds raw tool results, and it
is the only injected block in the prompt that is **revised in place** rather
than rebuilt from the store each turn — so an injection that lands in every
other block lives one turn, and an injection that lands HERE persists until a
later fold happens to drop it. The sink screens identifiers and coordinates
before the write (`nora.agent.dream_screen`); the sentence in the type is the
second half of that, and neither is sufficient alone. The hedge is that
sentence, not an `untrusted="true"` attribute, whose definition this audience
does not load.

**A lossy position, said out loud.** The tier is lossy by design — 1200
characters, refuse-and-consolidate, no history in the row — so the note says
the position may be out of date and points at the tiers that actually keep
things. That sentence is what stops `<standing>` becoming the place a wedding
venue lives: she has to be told that this block forgets,
or the cheapest place to put something is the place least able to hold it.
"""

from __future__ import annotations

from datetime import datetime

from ..domain.blocks import ContextBlock, render_elements
from ..domain.prompt import age as _age
from ..domain.prompt import escape
from ..domain.standing import WORKING_SET_TTL_HOURS, Standing, WorkingSetItem


def render_standing(
    standing: Standing | None, *, now: datetime | None = None,
) -> ContextBlock | None:
    """`None` when there is no position yet — a conversation Nora has not folded
    is not a conversation she has an empty opinion about, and rendering an empty
    block would say she had considered it and concluded nothing.

    **`written` is the block's age** (from the row's `updated_at`). Without it
    the note would claim recency it cannot support: «as of last turn» is true
    in a busy room and false in one that went quiet over a weekend, with
    nothing in the block to tell her which she is in. It is the same rendering
    as `prefill._age`, for the same reason: undated items all read as equally
    recent.

    Escaped, per R-13: the body is model-authored text folded from a transcript
    the group wrote, so a forged closing tag is reachable from the room's own
    messages two hops away.
    """
    if standing is None or not standing.body.strip():
        return None
    written = _age(standing.updated_at, now or datetime.now().astimezone())
    # The sectioned schema's other two sections, `I hold open` and `I owe`, are
    # appended only when a fold has revised them; a row without them renders
    # byte-identical to the `body` alone (`Where it stands`).
    body = escape(standing.body)
    if standing.hold_open:
        body = f"{body}\n\nI hold open: {escape(standing.hold_open)}"
    if standing.i_owe:
        body = f"{body}\n\nI owe: {escape(standing.i_owe)}"
    return ContextBlock(
        name="standing", attrs=(("written", written),),
        body=body,
    )


def render_standing_working_set(
    working_set: tuple[WorkingSetItem, ...], *, now: datetime | None = None,
) -> ContextBlock | None:
    """`<standing_working_set>` — options found and not yet given the room.

    **Fail-closed on the TTL, by construction, not by a sweep.** An item
    older than `WORKING_SET_TTL_HOURS` is filtered out HERE, before the block
    is built — the same place `render_standing` already computes `written`
    as an age at render time rather than trusting a stored label. An expired
    item is never emitted: it does not render as stale, it does not render
    at all, identical to a room whose working set is empty. No new
    deterministic sweep exists or is needed — the fold's own next pass
    rewrites the stored array independently (`nora.engagement.stages.debrief`).

    `None` when there is nothing LIVE to show — an empty `working_set`, or
    one whose every item has aged out — for `render_standing`'s reason: a
    room with nothing found is not a room she considered and found nothing
    in, and the type's own description already says so.
    """
    if not working_set:
        return None
    now = now or datetime.now().astimezone()
    now_ms = int(now.timestamp() * 1000)
    ttl_ms = int(WORKING_SET_TTL_HOURS * 3_600_000)
    live = tuple(
        item for item in working_set if now_ms - item.found_at <= ttl_ms
    )
    if not live:
        return None
    body = render_elements(
        live, tag="item",
        attrs=lambda item, _i: (
            ("source", item.source),
            ("found", _age(item.found_at, now)),
            ("bears_on", item.bears_on),
        ),
        body=lambda item: item.what,
    )
    return ContextBlock(name="standing_working_set", body=body)
