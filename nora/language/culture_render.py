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

import asyncio
import logging
from collections.abc import Awaitable
from datetime import datetime

from ..domain.blocks import ContextBlock
from ..domain.culture import CultureRevision
from ..domain.prompt import age as _age
from ..domain.prompt import escape
from ..domain.withdrawal import render_supported
from ..obs.metrics import Metrics

log = logging.getLogger(__name__)

#: How long any occasion waits for a culture head before giving the block up.
#: The same default the fire path's standing read uses: a background tier's
#: store must never be able to hold a turn or a pass open.
DEFAULT_CULTURE_READ_TIMEOUT_SECONDS = 5.0


async def read_culture(
    head: Awaitable[CultureRevision | None] | None,
    *,
    stage: str,
    platform: str,
    metrics: Metrics | None,
    timeout_seconds: float = DEFAULT_CULTURE_READ_TIMEOUT_SECONDS,
) -> CultureRevision | None:
    """One room's account for a prompt, or `None` — every occasion's ONE read.

    It used to be written once, in `FirePath._read_culture`, when that was the
    only reader (DEC-207 widened the tier to every runtime). Six copies of a
    bounded, counted, fail-soft read would be six places for the bound or the
    count to go missing, so the read is here and each occasion only names its
    `stage`.

    `head` is the caller's own `reader.head(key=…)` call, or `None` when there
    is no reader or no key — a room outside the policy, which is not an error.
    The CALLER makes the call rather than handing the reader over, so every
    Dreamer adaptor's store call stays on `self.<collaborator>` where
    `tests/architecture/test_dream_store_calls_are_declared.py` can count it.
    The key is a `primary_id` (DEC-181).

    `None` collapses five states the prompt cannot tell apart: no reader (the
    `[culture] enabled = false` default), no key, no account yet, a failed
    read, and an account the withdrawal cascade marked `unsupported_at`. Only
    the failed read is counted, under `nora_errors_total{stage}`: a head that
    exists and could not be fetched, reported as «there is none», is
    `unavailable` substituted for `nothing_found` (R-27).

    **WITHDRAWAL IS APPLIED HERE, for every occasion at once**
    (design/cognition-loop/10-tiers.md §2 rule 7, `nora.domain.withdrawal`).
    This is every prompt's way to a culture head, so a head whose source was
    withdrawn stops rendering in the tag turn and the fire turn (and, until
    2026-10-03, proactive, note-candidates, the curator and the event fold)
    alike, and a new occasion
    that reads through here cannot forget it: the rule used to be two copies at
    the tag and fire call sites, and the Dreamer prompts had none, so a
    retracted message or a forgotten person's account could be read by a model
    that goes on to write a note or a fold. A fold's OWN prior (the culture
    fold's `previous_account`) is read from the store directly, not through
    here, on purpose: the fold has to see what it wrote to supersede it, and
    `tests/architecture/test_withdrawal_reaches_every_prompt.py` pins that
    exemption by name.

    BOUNDED, because a hung Postgres connection with no `command_timeout`
    otherwise holds the caller's slot — the defect fixed in `debrief.run` and
    then re-created on the fire path in the same change.
    """
    if head is None:
        return None
    try:
        found = await asyncio.wait_for(head, timeout=timeout_seconds)
    except Exception:
        log.exception("%s: culture read failed; continuing without it", stage)
        if metrics is not None:
            metrics.inc("nora_errors_total", stage=stage, platform=platform)
        return None
    return render_supported(found)


def render_culture(
    culture: CultureRevision | None, *, now: datetime | None = None,
    room: str | None = None,
) -> ContextBlock | None:
    """`None` when this room has no account yet.

    A room nobody has folded is not a room she has an empty opinion about, and
    an empty block would say she had looked and found it featureless —
    `render_standing`'s reasoning, and the same failure it avoids.

    The absence is also the COMMON case for a while after this ships: a fold
    needs new messages and a day, so a new room simply has no account, and a
    block that appeared blank would be read as «this room has no character».

    `room` names WHICH room the account is of, and is passed only by an
    occasion that reads more than one room in one prompt (the since-deleted proactive pass and note
    curator): there, an unlabelled account is an account of nowhere. An
    occasion inside one room omits it, and its block is byte-identical to the
    participation turn's from before the tier was widened.
    """
    if culture is None or not culture.body.strip():
        return None
    written = _age(culture.written_at, now or datetime.now().astimezone())
    attrs: tuple[tuple[str, str], ...] = (("written", written),)
    if room:
        attrs = (("room", room), *attrs)
    return ContextBlock(
        name="culture", attrs=attrs,
        body=escape(culture.body),
    )
