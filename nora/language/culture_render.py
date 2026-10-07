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
from collections.abc import Awaitable, Sequence
from datetime import datetime
from typing import Protocol

from ..domain.blocks import ContextBlock
from ..domain.culture import CultureRevision, character_key
from ..domain.models import PersonRef
from ..domain.prompt import age as _age
from ..domain.prompt import escape
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

    `None` collapses four states the prompt cannot tell apart: no reader (the
    `[culture] enabled = false` default), no key, no account yet, and a failed
    read. Only
    the failed read is counted, under `nora_errors_total{stage}`: a head that
    exists and could not be fetched, reported as «there is none», is
    `unavailable` substituted for `nothing_found` (R-27).

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
    return found


def render_culture(
    culture: CultureRevision | None, *, now: datetime | None = None,
    room: str | None = None,
) -> ContextBlock | None:
    """`None` when this room has no account yet.

    A room nobody has folded is not a room she has an empty opinion about, and
    an empty block would say she had looked and found it featureless —
    `render_standing`'s reasoning, and the same failure it avoids.

    The absence is also the COMMON case for a while after this ships: a fold
    needs new messages and a wake (DEC-288), so a new room simply has no account, and a
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


def render_unit_culture(
    culture: CultureRevision | None, *, now: datetime | None = None,
) -> ContextBlock | None:
    """The `<unit_culture>` block: what the room's whole unit is like
    (design/self-growth/10 §1, C3a). `None` while the unit has no account,
    for `render_culture`'s reason: an empty block would say she looked and
    found nothing."""
    if culture is None or not culture.body.strip():
        return None
    return ContextBlock(
        name="unit_culture",
        attrs=(("written", _age(culture.written_at, now or datetime.now().astimezone())),),
        body=escape(culture.body),
    )


#: The most lines of her own account the `<self>` block carries. It rides
#: first in every tag and fire prompt and is re-sent on each model call of a
#: loop, so it is capped on what is rendered, whatever the store holds
#: (`BODY_MAX_CHARS` is what a revision may be). The longest account observed
#: is `ACCOUNT_CHARS_OBSERVED_MAX` characters, a few dozen lines at most.
#: ponytail: one number for every unit; a config knob if a real account is cut.
SELF_MAX_LINES = 40


def render_self(
    account: CultureRevision | None, *, now: datetime | None = None,
) -> ContextBlock | None:
    """The `<self>` block: her own Character · Nora for this unit
    (design/self-growth/20), capped at `SELF_MAX_LINES` lines.

    `None` while the unit has no account, for `render_culture`'s reason: an
    empty block would say she looked and found herself blank. Lineage is not
    applied, as for `<unit_culture>` and `<character>`: the account is keyed
    by the UNIT, which is the read boundary (DEC-231), so every room of the
    unit may read it. A body cut by the cap says how many lines it dropped.
    """
    if account is None or not account.body.strip():
        return None
    lines = account.body.strip().splitlines()
    shown = lines[:SELF_MAX_LINES]
    dropped = len(lines) - len(shown)
    if dropped:
        shown.append(f"[{dropped} more lines not shown]")
    return ContextBlock(
        name="self",
        attrs=(("written", _age(account.written_at, now or datetime.now().astimezone())),),
        body=escape("\n".join(shown)),
    )


class _HeadReader(Protocol):
    def head(self, *, key: str) -> Awaitable[CultureRevision | None]: ...


async def read_character(
    reader: _HeadReader | None,
    *,
    unit: str,
    members: Sequence[PersonRef],
    stage: str,
    platform: str,
    metrics: Metrics | None,
    timeout_seconds: float = DEFAULT_CULTURE_READ_TIMEOUT_SECONDS,
) -> tuple[tuple[str, CultureRevision], ...]:
    """`(name, account)` for each listed person who has one, in roster order.

    The Character tier's read: one `read_culture` per person, so each keeps
    its bound and its count, and a person whose read failed or who has no
    account yet is simply absent. `unit` is the rendering room's unit: an
    account is one per (unit, person) since C2b (design/self-growth/10 §3),
    and `members` are the rendering room's LISTED persons, so a person's
    account renders only in a room of their unit that lists them
    (design/cognition-loop/50-character-and-standing.md §5).
    """
    if reader is None or not members:
        return ()
    found = await asyncio.gather(*(
        read_culture(
            reader.head(key=character_key(unit, ref.person_id)),
            stage=stage, platform=platform, metrics=metrics,
            timeout_seconds=timeout_seconds,
        )
        for ref in members
    ))
    return tuple((ref.name, rev) for ref, rev in zip(members, found, strict=True) if rev)


def render_character(
    accounts: Sequence[tuple[str, CultureRevision]], *, now: datetime | None = None,
) -> ContextBlock | None:
    """The `<character>` block: one `<person_account>` per person who has one.

    `None` when nobody has an account yet, for `render_culture`'s reason: an
    empty block would say she looked and found them featureless. Each account
    carries its own `written` age, because nothing here retires and a head may
    be weeks old and still right.
    """
    clock = now or datetime.now().astimezone()
    rows = "\n".join(
        ContextBlock(
            name="person_account",
            attrs=(("name", name), ("written", _age(rev.written_at, clock))),
            body=escape(rev.body),
        ).render()
        for name, rev in accounts
        if rev.body.strip()
    )
    return ContextBlock(name="character", body=rows) if rows else None
