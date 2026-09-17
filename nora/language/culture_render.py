"""Renders one room's culture head into the `<culture>` block — an account of
how this room works and why (`design/culture-tier`).

**What makes it different from every other block in the prompt.** `<notes>` is
what the room has not settled, `<group_transcript>` is what it said,
`<standing>` is where she had got to. All three are about a moment. This one is
about the room ITSELF — not what happened in it, but what it is and why it
became so.

That distinction is the tier's whole reason to exist and it is why the note
below never says «recently» or «lately». The memory system already produces
`[when] what happened`; a chronicle of dynasties and their dates is not a
culture, and if this block reads as one more list of recent events then the
account it holds was written wrong and the block is worth nothing.

**The body is HEDGED even though it is hers.** It is model-authored prose
folded from the room's own messages, so a forged closing tag is reachable from
what the room wrote, two hops away — `<standing>`'s reasoning exactly, and for
once the sibling's argument transfers without amendment. It used to say that
with `untrusted="true"`; since #314 it says it in the type's own words, which
is the difference between a token this audience has no definition of and a
sentence it can act on.

**`written` matters MORE here than anywhere else, not less.** Nothing in this
tier retires (DEC-183): a quiet room's account is not stale, it is accurate, so
a head may legitimately be weeks old and still be the right answer. Every other
block's age says «how much has this decayed»; this one's says «how long has this
room been like this», and a block claiming to say how a room works while
carrying no indication of when it was derived is R-27's trap written in prose.
"""

from __future__ import annotations

from datetime import datetime

from ..domain.blocks import ContextBlock
from ..domain.culture import CultureRevision
from ..domain.prompt import age as _age
from ..domain.prompt import escape


def render_culture(
    culture: CultureRevision | None, *, now: datetime | None = None,
) -> ContextBlock | None:
    """`None` when this room has no account yet.

    A room nobody has folded is not a room she has an empty opinion about, and
    an empty block would say she had looked and found it featureless —
    `render_standing`'s reasoning, and the same failure it avoids.

    The absence is also the COMMON case for a while after this ships: a fold
    needs new messages and a day, so a new room simply has no account, and a
    block that appeared blank would be read as «this room has no character».
    """
    if culture is None or not culture.body.strip():
        return None
    written = _age(culture.written_at, now or datetime.now().astimezone())
    return ContextBlock(
        name="culture", attrs=(("written", written),),
        body=escape(culture.body),
    )
