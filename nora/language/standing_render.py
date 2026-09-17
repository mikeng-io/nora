"""Renders one conversation's `Standing` into the `<standing>` block — Nora's
own position, handed back to her at the top of the next turn
(`design/dreamer-agent-loop/26-standing.md` §4).

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
folded from `tag_trace.messages`, which holds raw tool results (§4.3), and it
is the only injected block in the prompt that is **revised in place** rather
than rebuilt from the store each turn — so an injection that lands in every
other block lives one turn, and an injection that lands HERE persists until a
later fold happens to drop it (§4.1, carried risk, stated and not solved). The
sink screens identifiers and coordinates before the write
(`nora.agent.dream_screen`); the sentence in the type is the second half of
that, and neither is sufficient alone. It was `untrusted="true"` until #314 —
the block with the subtlest reason to hedge, saying it with the one token whose
definition this audience does not load.

**A lossy position, said out loud.** The tier is lossy by design (§8) — 1200
characters, refuse-and-consolidate, no history in the row — so the note says
the position may be out of date and points at the tiers that actually keep
things. That sentence is what stops `<standing>` becoming the place a wedding
venue lives (`19-between-tiers.md`): she has to be told that this block forgets,
or the cheapest place to put something is the place least able to hold it.
"""

from __future__ import annotations

from datetime import datetime

from ..domain.blocks import ContextBlock
from ..domain.prompt import age as _age
from ..domain.prompt import escape
from ..domain.standing import Standing


def render_standing(
    standing: Standing | None, *, now: datetime | None = None,
) -> ContextBlock | None:
    """`None` when there is no position yet — a conversation Nora has not folded
    is not a conversation she has an empty opinion about, and rendering an empty
    block would say she had considered it and concluded nothing.

    **`written` is the block's age**, and without it the note above was making
    a recency claim it could not support: it used to say «as of last turn»,
    which is true in a busy room and false in one that went quiet over a
    weekend, with nothing in the block letting her tell which she was in.
    `updated_at` was on the row the whole time.

    This is `prefill._age`'s finding one block over. That helper exists because
    «every message looked equally recent … which is what made 今晚 and 琴日
    unanswerable even with a clock in the header», and it was applied to the
    transcript and to nothing else — including the block rendered directly
    above it. The Relevance Gate had the same gap and lost two thirds of its
    error rate closing it (2026-09-10).

    Escaped, per R-13: the body is model-authored text folded from a transcript
    the group wrote, so a forged closing tag is reachable from the room's own
    messages two hops away.
    """
    if standing is None or not standing.body.strip():
        return None
    written = _age(standing.updated_at, now or datetime.now().astimezone())
    return ContextBlock(
        name="standing", attrs=(("written", written),),
        body=escape(standing.body),
    )
