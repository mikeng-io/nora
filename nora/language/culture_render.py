"""Renders one room's culture head into the `<culture>` block — an account of
how this room works and why.

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
once the sibling's argument transfers without amendment. The hedge is stated in the
type's own words, a sentence the reader can act on, not a bare `untrusted`
token.

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

    Every occasion shares this one bounded, counted, fail-soft read (DEC-207),
    so the bound and the count live in one place; each occasion only names its
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
    occasion that reads more than one room in one prompt: there, an unlabelled
    account is an account of nowhere. An occasion inside one room omits it.
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
    """The `<unit_culture>` block: what the room's whole unit is like.
    `None` while the unit has no account,
    for `render_culture`'s reason: an empty block would say she looked and
    found nothing."""
    if culture is None or not culture.body.strip():
        return None
    return ContextBlock(
        name="unit_culture",
        attrs=(("written", _age(culture.written_at, now or datetime.now().astimezone())),),
        body=escape(culture.body),
    )


#: The most characters of her own account the `<self>` block carries. It rides
#: first in every tag, fire and Cognition prompt and is re-sent on each model
#: call of a loop, so it is capped on what is rendered, whatever the store
#: holds (`BODY_MAX_CHARS` is what a revision may be). Accounts are prose in
#: one to three lines, so a cap on LINES bounds nothing. 4,000 is more than
#: twice the longest observed account (`ACCOUNT_CHARS_OBSERVED_MAX`): a cap is not a
#: reservation, so headroom costs nothing until an account uses it.
#: ponytail: one number for every unit; a config knob if a real account is cut.
SELF_MAX_CHARS = 4_000


def render_self(
    account: CultureRevision | None, *, now: datetime | None = None,
) -> ContextBlock | None:
    """The `<self>` block: her own Character · Nora for this unit,
    capped at `SELF_MAX_CHARS` characters.

    `None` while the unit has no account, for `render_culture`'s reason: an
    empty block would say she looked and found herself blank. Lineage is not
    applied, as for `<unit_culture>` and `<character>`: the account is keyed
    by the UNIT, which is the read boundary (DEC-231), so every room of the
    unit may read it; whether THIS room may is decided before the read
    (`Policy.shows_self_in`, R-31, DEC-292). A body cut by the cap says how much
    it dropped.
    """
    if account is None or not account.body.strip():
        return None
    body = account.body.strip()
    dropped = len(body) - SELF_MAX_CHARS
    if dropped > 0:
        kept = body[:SELF_MAX_CHARS]
        body = f"{kept}\n[{dropped} more characters not shown]"
    return ContextBlock(
        name="self",
        attrs=(("written", _age(account.written_at, now or datetime.now().astimezone())),),
        body=escape(body),
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
    account is one per (unit, person), and `members` are the rendering room's
    LISTED persons, so a person's account renders only in a room of their unit
    that lists them.
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


#: The most characters ONE person's account may carry in `<character>`, and the
#: most the whole RENDERED block may be (wrapper tags, `written` ages, escaped
#: bodies and markers all counted, so the cap bounds what is sent, not just the
#: prose). The block rides every model call of a turn, and a revision may be
#: `BODY_MAX_CHARS` long, so the render is capped whatever the store holds. The
#: caps sit above the largest observed account and the whole observed roster,
#: and the block cannot grow past them however many accounts or however much
#: escaping: a cap is not a reservation, so headroom costs nothing until an
#: account uses it.
#: ponytail: constants, not config; a knob only if a real account is cut.
PERSON_ACCOUNT_MAX_CHARS = 2_500
CHARACTER_BLOCK_MAX_CHARS = 12_000

#: Room kept for the closing `[N more characters not shown]` line, so the block
#: including it never exceeds `CHARACTER_BLOCK_MAX_CHARS` (N has at most 8 digits).
_CLOSING_MARKER_RESERVE = len("\n[00000000 more characters not shown]")


def _cut(body: str, room: int) -> str:
    """`body` cut to `room` characters, saying how many it dropped."""
    dropped = len(body) - room
    return body if dropped <= 0 else f"{body[:room]}\n[{dropped} more characters not shown]"


def render_character(
    accounts: Sequence[tuple[str, CultureRevision]], *, now: datetime | None = None,
) -> ContextBlock | None:
    """The `<character>` block: one `<person_account>` per person who has one.

    `None` when nobody has an account yet, for `render_culture`'s reason: an
    empty block would say she looked and found them featureless. Each account
    carries its own `written` age, because nothing here retires and a head may
    be weeks old and still right.

    Capped twice, each cut saying how many characters it dropped: one account
    at `PERSON_ACCOUNT_MAX_CHARS`, and the RENDERED block (wrappers, ages,
    escaped bodies and every marker included) at `CHARACTER_BLOCK_MAX_CHARS`.
    Accounts are taken in the order given: the FIRST one the remaining budget
    cannot hold whole is CUT to what is left, and every later one is left out
    whole and counted in one closing `[N more characters not shown]` line, whose
    room is reserved, so the whole block never exceeds the cap.
    """
    clock = now or datetime.now().astimezone()
    budget = (
        CHARACTER_BLOCK_MAX_CHARS
        - len(ContextBlock(name="character", body="").render())
        - _CLOSING_MARKER_RESERVE
    )

    def row(name: str, rev: CultureRevision, body: str) -> str:
        return ContextBlock(
            name="person_account",
            attrs=(("name", name), ("written", _age(rev.written_at, clock))),
            body=escape(body),
        ).render()

    rows: list[str] = []
    omitted = 0
    closed = False  # once an account is cut or left out, every later one is left out whole
    for name, rev in accounts:
        body = rev.body.strip()
        if not body:
            continue
        if closed:
            omitted += len(body)
            continue
        room = min(len(body), PERSON_ACCOUNT_MAX_CHARS)
        shown = row(name, rev, _cut(body, room))
        # The first account the budget cannot hold whole is CUT to what is
        # left (rendered, escaped, marker and wrapper included); none is
        # left when even a bare wrapper does not fit.
        while len(shown) + 1 > budget and room > 0:
            room = min(room - 1, max(0, room - (len(shown) + 1 - budget)))
            shown = row(name, rev, _cut(body, room))
        if len(shown) + 1 > budget:
            closed = True
            omitted += len(body)
            continue
        rows.append(shown)
        budget -= len(shown) + 1
        if room < len(body):
            closed = True
    if omitted:
        rows.append(f"[{omitted} more characters not shown]")
    return ContextBlock(name="character", body="\n".join(rows)) if rows else None
