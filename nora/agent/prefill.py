"""Render the room floor and framing for engagement passes."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from nora.domain.composed import Composed, compose_body, legacy_composed

from ..domain.blocks import (
    BlockKey,
    ContextBlock,
    render_elements,
    restamp,
    speaker_name,
)
from ..domain.external_files import render_file_links
from ..domain.framing import (
    CLOSING_LINE,
    SPOKEN_TURN,
    TAG_HEADER,
    ClockLine,
    ContextHeader,
)
from ..domain.models import TagContext, TranscriptRow
from ..domain.prompt import age, escape
from ..domain.quote_window import first_by_ts
from ..domain.self_presence import Presence
from ..language.catalogue_render import render_tool_catalogue
from ..language.computer_render import render_computer
from ..language.documents_render import render_documents
from ..language.presence_render import render_presence
from ..language.subagents_render import render_subagents
from .clock import local_now, state_time

# `TAG_HEADER`, `CLOSING_LINE` and the text of the clock line and of the
# `<context_header>` message live in `nora.domain.framing` with every other
# piece of authored framing text; this module names the pieces and says where
# they go.
#
# `escape`/`_escape_attr` live in `nora.domain.prompt` because the doctrine
# loader renders frontmatter into section attributes with the identical rule,
# and the layer contract runs agent -> doctrine, so the loader cannot import
# them from here. Re-exported under the names this module uses.
__all__ = ["TAG_HEADER", "engagement_floor", "engagement_prefill", "escape", "render_transcript"]

def _message(role: str, text: str) -> dict:
    return {"role": role, "content": [{"text": text}]}


def _now_line(now: datetime | None) -> str:
    """The current local time, stated plainly.

    Without it 今晚, 琴日, 而家 and 尋日 are ungrounded and «what time is it»
    is unanswerable. Deliberately NOT a tool: it is relevant to every turn, so
    a tool means either paying a round trip every time or the model deciding
    it does not need one, and a model that does not know the time also does
    not know that it doesn't. It is short and cannot fail.

    `now` is injected rather than read from the clock here so a test can pin
    it.

    It names the zone and the place the zone is named for, because «HKT» alone
    is an abbreviation she has to decode, and a picture that needs a place
    («Hong Kong» on a poster) needs something in her input to copy it from.
    The wording is `nora.agent.clock.state_time`, the same formatter the `now`
    tool answers with, so a mid-turn `now()` states exactly this line's facts.
    """
    if now is None:
        now = local_now()
    return ClockLine(state_time(now)).render()


# Order and scope for blocks stamped by this module.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("members", 4, "room"),
    ("transcript", 70, "room"),
    ("spoken_turn", 75, "turn"),
)

#: `name -> (order, key)`, derived from the table above so a block function
#: can look up its own row instead of repeating it.
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}


def _block(
    name: str, *, body: Composed, attrs: tuple[tuple[str, str], ...] = (),
) -> ContextBlock:
    """One block, built from the table's row plus the two things only this
    render knows: its `body` and its own attributes.

    The split is the point. `order` and `key` are ASSEMBLY facts and live in
    one table; the content is this render's. Neither half can be forgotten: the
    table entry without a function renders nothing and `assemble` drops it, and
    a function without a table entry cannot call this at all.

    There is no `note` parameter: what a block IS comes from
    `nora.domain.block_types`, keyed on `name`, so a caller cannot describe a
    block its own way.
    """
    order, key = _ROW[name]
    return ContextBlock(
        name=name, order=order, key=key,
        body=body, attrs=attrs,
    )


def _context_header(header: str = TAG_HEADER) -> str:
    """Keep the occasion's framing in a stable prefix."""
    return ContextBlock(name="context_header",
        body=legacy_composed(ContextHeader(header).render())).render()


def _label(row: TranscriptRow, display_name: str) -> str:
    """The one string a row is ever called by.

    Shared with the arrow and with `_replying_to_block` on purpose: the name
    after ↳ has to be BYTE-IDENTICAL to the label on the parent's own line, so
    the model joins the two by literal match with nothing left to infer.

    `person_name` FIRST (DEC-80): the identity-canonical name is what every other
    surface of the prompt calls this person (`tag_message from=`,
    `group_members`, `lookup_person`), so preferring it makes the transcript
    join to them by literal match too. Falling back to the self-set Signal
    profile name keeps an unmapped account visible as SOMEBODY rather than
    collapsing it to "unknown"; the assembler leaves `person_name` None
    precisely there.
    """
    if row.is_nora:
        return display_name
    # THROUGH THE HELPER, because the guard has to run AFTER the collapse.
    # `person_name or sender_name or "unknown"` tests the RAW string, so a
    # self-set profile name of a lone non-breaking space is truthy, the
    # fallback never fires, and `ContextBlock.render()`'s collapse then empties
    # it, rendering `<replying_to>` with no author at all beside siblings that
    # have one.
    return speaker_name(row.person_name, row.sender_name)


#: One resolver for a reply's quote, shared with the debrief's transcript and the
#: tool-result transcript: FIRST wins when two rows share a `ts`
#: (`nora.domain.quote_window.first_by_ts` says why). The name `_by_ts` is the
#: one the audit store's contract test refers to (DEC-195).
_by_ts = first_by_ts


def _parent_of(
    row: TranscriptRow, by_ts: Mapping[int, TranscriptRow]
) -> TranscriptRow | None:
    """The row this one replies to, resolved from the window already in hand.

    In-window only, which is the whole mechanism: an O(n) dict over rows
    `recent` already returned, with no query, no `raw_json` parsing on the
    reply path and no second column. A pointer at a row outside the window, at
    a pruned or pre-migration row, or at itself resolves to nothing.

    An unresolved parent renders a PLAIN line, never `↳ ?`. Teaching the model
    that a symbol can mean "unknown" invites it to emit one; silent
    degradation is R-5's temperament.

    Pass `_by_ts(...)`, not a dict comprehension: see that function for the
    tie-break the two resolvers have to share.
    """
    if row.quoted_ts is None:
        return None
    parent = by_ts.get(row.quoted_ts)
    return None if parent is None or parent.id == row.id else parent


def render_transcript(
    transcript: Sequence[TranscriptRow], *, display_name: str, now: datetime,
) -> str:
    """The `<m>` elements `<transcript>` carries, without the block
    around them: the room exactly as she is shown it.

    Public for ONE reader outside this module: the eval harness hands a judge
    the window a case was seeded with (`nora.subject.sandbox`), and a judge
    asked whether «X said Y» can only check it against a source that names
    who said what, the way SHE saw it. Rendering it here keeps the two from
    drifting: the same `_label` (her rows under `display_name`, a member under
    the identity-canonical name, never an account id), the same handle, age
    and reply index.

    `transcript` is expected ALREADY NAMED (`Persons.name_transcript`), which
    is the assembler's job in production and the caller's here.
    """
    from nora.domain.context_data import TranscriptLine
    from nora.language.context_render import transcript_rows

    return transcript_rows(tuple(TranscriptLine(row, "") for row in transcript),
                           display_name=display_name, now=now)


def _transcript_block(
    ctx: TagContext, display_name: str, now: datetime, context_window: int,
) -> ContextBlock:
    """The room, with a HANDLE on every line.

    `#n` is what `send_reply(reply_to=n)` takes, and it is a per-render index
    rather than a row id or a timestamp for `note_handle`'s reason: «an id is
    not something a person says in a chat». Three properties follow from the
    index being ours and small:

    * It is a CLOSED SET. Only 1..N exist, so an out-of-range value is
      refusable rather than a lookup that might hit some other row. A model
      cannot invent a message that was never in front of it.
    * The real handles never reach the model. `ts`, `sender_id` and the
      platform's own message id stay on this side; the tool resolves the index
      back to the row. That keeps a raw identifier off the wire, which is
      R-18's floor.
    * It survives the render. The mapping is positional over
      `ctx.transcript`, so the tool resolves against the same tuple that was
      shown, not a fresh read that may have moved.

    The numbers are stable only WITHIN one turn, which is all `reply_to` needs.

    **A message is an element, not a line.** A line per row joined with `\n`
    would let a newline in the body write a whole extra row: a handle
    COLLIDING with a real one, an age the writer chose, and a name that was
    somebody else's. `escape` does not stop it (a newline is not `&`, `<` or
    `>`, and R-13/R-13b's tests pin only the angle brackets). A member
    pressing enter mid-message is the common case, and it would produce a
    continuation line with no handle, age or name immediately above the next
    real row, reading as a fragment belonging to whoever is adjacent.

    Collapsing the body would close it at the cost of the shape of every
    paragraph, list and pasted block. `render_messages` closes it by changing
    the SEPARATOR instead of the content: each message is its own `<m>`
    element, `escape` makes a forged `</m>` unwritable, and the body between
    the tags keeps its line breaks. That module's docstring carries the
    argument and the serialisers it follows.

    So this renderer does NOT call `one_line` on the body and must not. The
    speaker name is collapsed: inside an attribute a newline cannot break out,
    but it makes the tag unreadable for nothing.
    """
    rows = render_transcript(ctx.transcript, display_name=display_name, now=now)
    # The typed source events of the same window (docs/design/platform/
    # observations.md): `<source_event>` elements INTERLEAVED into the
    # transcript at their own time — a vote between two messages reads
    # between them, not as an appendix after the last word. They are not
    # messages and take no `#n` handle (a reply cannot name one). Each
    # says its kind, the operation, the source's own time (and WHICH
    # clock, when it is not the upstream's), the actor the source
    # names — a roster name or nothing, never a speaker line. An event
    # the source pinned to a message says `on="n"` — the message's own
    # `#n` handle, never an invented quote or reply target; a source
    # that named no message takes none.
    #
    # `#n` is `render_transcript`'s positional index over `ctx.transcript`,
    # so the same map resolves it here.
    index_of = {row.id: i for i, row in enumerate(ctx.transcript, 1)}

    def _event(e: Any) -> str:
        # The affected message, when the transcript does not already show
        # it: `target_*` is the recorded row's own words and author as
        # labelled context — bounded, roster-named, and never the event's
        # actor's words. An `on=` row needs none of it (the target IS the
        # shown row); a named-but-unresolvable target says so once.
        shown = e.target is not None and e.target_message_id not in index_of
        return render_elements(
            [e], tag="source_event",
            attrs=lambda ev, _i: (
                ("kind", ev.kind), ("op", ev.operation),
                # `at` is the SOURCE's clock — it appears only when the
                # source actually gave a time. An unknown clock shows
                # `time="unknown"` plus `received` (the bridge's own
                # clock, stated as receipt), never a dressed-up `at`.
                ("at", age(ev.at_ms, now)
                        if ev.at_ms > 0 and ev.time_quality != "unknown"
                        else None),
                ("actor", ev.actor or (
                    "anonymous" if ev.actor_status == "anonymous" else None)),
                ("time", ev.time_quality if ev.time_quality != "upstream"
                        else None),
                ("received", age(ev.received_ms, now)
                        if ev.received_ms and ev.time_quality != "upstream"
                        else None),
                ("rev", ev.revision or None),
                ("coverage", ev.coverage or None),
                ("object", ev.handle),
                ("on", (f"{index_of[ev.target_message_id]}"
                        if ev.target_message_id in index_of else None)),
                ("target_author", ev.target.author if shown else None),
                ("target_at", age(ev.target.at_ms, now)
                        if shown and ev.target.at_ms > 0 else None),
                ("target_text", ev.target.text if shown else None),
                ("target_edited", age(ev.target.edited_ms, now)
                        if shown and ev.target.edited_ms else None),
                ("target_was", ev.target.was if shown else None),
                ("target_deleted", "yes"
                        if shown and ev.target.deleted else None),
                ("target_unresolved", "yes"
                        if (ev.target_message_id is not None
                            and ev.target_message_id not in index_of
                            and ev.target is None) else None),
            ),
            body=lambda ev: ev.summary,
        )

    events = sorted(ctx.source_events, key=lambda e: e.at_ms)
    if events:
        # The `<m>` elements correspond 1:1 to `ctx.transcript` in order;
        # split at element boundaries — an element body cannot contain
        # `\n<m ` (escape keeps `<` out of every body and attribute).
        chunks = re.split(r"(?=\n<m )", rows) if rows else []
        merged: list[str] = []
        pending = iter(zip(ctx.transcript, chunks, strict=False))
        row, chunk = next(pending, (None, ""))
        for e in events:
            while row is not None and row.ts <= e.at_ms:
                merged.append(chunk)
                row, chunk = next(pending, (None, ""))
            merged.append(_event(e))
        while row is not None:
            merged.append(chunk)
            row, chunk = next(pending, (None, ""))
        rows = "\n".join(c for c in merged if c)
    if ctx.source_reads_failed:
        rows = f"{rows}\n<source_reads unavailable/>" if rows else \
            "<source_reads unavailable/>"
    # `window` is R-15, on the block it is about. A model that does not know
    # how far back it can see cannot tell «nobody said that» from «not in what
    # you were shown», and «further back than you were shown» means nothing
    # without a number. It is a per-render value, so it is an attribute (data
    # about this block, on this block) and not part of the type's description.
    #
    # `len(ctx.transcript)`, NOT the `context_window` parameter:
    # `TranscriptInjection._recent` sizes the fetch PAST `context_window` to
    # chase a reply's quote outside it, so the configured default understates
    # what she was actually shown, the wrong direction for R-15, which exists
    # so she can trust the boundary she is told. `context_window` stays a
    # parameter for the caller's own bookkeeping; this block does not read it.
    return _block(
        "transcript",
        attrs=(("window", str(len(ctx.transcript))),
               ("source_more", str(ctx.source_events_more)))
        if ctx.source_events_more else (("window", str(len(ctx.transcript))),),
        body=compose_body(rows),
    )


def _room_members_block(ctx: TagContext) -> ContextBlock | None:
    """`<room_members>`: this room's listed persons, each with the other
    names the group calls them, so a nickname used ABOUT someone resolves
    to them (aliases otherwise reach her only for the tagger).

    Authored data (identity.toml via the room's own `persons` list), never
    the whole file and never a platform id (R-18). Absent when the room lists
    nobody identity maps, which is also a `persons = "*"` room."""
    from nora.domain.context_data import MembersView
    from nora.language.context_render import render_members

    return restamp(render_members(MembersView(room="this room", people=ctx.room_members)), _ROW)


def _presence_block(ctx: TagContext) -> ContextBlock:
    """Where she is on this turn, as `<presence>`.

    Always a block. A context nobody read a presence for (an offline harness
    with no reader) renders `Presence.minimal`: the network alone, so a turn
    never has no idea where it is.

    Rendered by `render_presence`, the one builder every presence reader calls.
    Nothing here composes a line of its own.
    """
    presence = ctx.presence or Presence.minimal(
        ctx.message.platform, direct=ctx.message.conversation_type == "dm",
    )
    return render_presence(presence)


def _spoken_turn_block(ctx: TagContext) -> ContextBlock | None:
    """`<spoken_turn>` when the message being answered was spoken, else nothing.

    Read off `ctx.message.voice`, the typed field the Discord adapter also
    routes the reply on (`target.voice`), so the prompt and the delivery cannot
    disagree about whether this reply will be heard.
    """
    if not ctx.message.voice:
        return None
    return _block("spoken_turn", body=compose_body(SPOKEN_TURN))


def _replying_to_block(ctx: TagContext, display_name: str) -> ContextBlock | None:
    """R-20: the quoted text shown to the model is the RESOLVED PARENT ROW's
    text, or nothing.

    `ctx.message.quote`'s own text comes out of the QUOTING sender's envelope,
    so Alice could construct a quote asserting Bob said anything at all and
    Nora would relay it back into the room as Bob's words. R-13 escapes it;
    escaping does not make a forged attribution safe.

    An unresolvable pointer omits the block entirely rather than rendering the
    author alone: the only author left would be `Quote.author_id`, a raw
    platform identifier, which is exactly what R-18 exists to keep off the
    wire and what no block in this module puts in a prompt.

    The author is a name Nora recorded herself, not the `authorName`
    signal-cli does not send.
    """
    quote = ctx.message.quote
    if quote is None:
        return None
    # The SAME resolver `_transcript_block` uses, so the quoted text and the
    # `replying_to="n"` pointer name one message rather than two (`_by_ts`).
    parent = _by_ts(ctx.transcript).get(quote.quoted_ts)
    if parent is None:
        return None
    return ContextBlock(
        name="replying_to", attrs=(("author", _label(parent, display_name)),),
        body=legacy_composed(escape(render_file_links(parent.body or ""))),
    )


def _tag_block(ctx: TagContext, tagger_name: str, quote: str = "") -> ContextBlock:
    tagger = ctx.tagger
    # Tag and fire turns share this block. Its contents do not establish a
    # direct call; the occasion header says why the turn opened.
    attrs: tuple[tuple[str, str], ...] = (("from", tagger_name),)
    if tagger is not None and tagger.aliases:
        attrs += (("also_known_as", ", ".join(tagger.aliases)),)
    return ContextBlock(
        name="tag_message", attrs=attrs,
        # The clause is the CANONICAL one (`_NOT_AN_INSTRUCTION`), not a
        # paraphrase: a paraphrase can carry the meaning and still match
        # neither spelling the framing gate accepts, so the block would ship
        # with a marker and a clause the gate cannot see.
        body=compose_body(f"{quote}{escape(render_file_links(ctx.message.body))}"),
        when_empty="render",
    )


def engagement_floor(
    ctx: TagContext, *, display_name: str, tagger_name: str, context_window: int,
    now: datetime, catalogue: str, proactive_event: bool = False,
) -> tuple[ContextBlock, ...]:
    """The declared room context for engagement passes."""
    blocks = (
        _room_members_block(ctx),
        ContextBlock("clock", compose_body(_now_line(now).strip()),
                     attrs=(("as_of", now.isoformat()),)),
        _presence_block(ctx),
        render_tool_catalogue(catalogue) if catalogue else None,
        render_computer(ctx.computer) if not proactive_event else None,
        render_documents(ctx.documents) if not proactive_event else None,
        render_subagents(ctx.subagents) if not proactive_event else None,
        _transcript_block(ctx, display_name, now, context_window),
        _spoken_turn_block(ctx) if not proactive_event else None,
        _replying_to_block(ctx, display_name) if not proactive_event else None,
        _tag_block(ctx, tagger_name),
    )
    return tuple(b for b in blocks if b is not None)


def engagement_prefill(header: str) -> list[dict]:
    return [
        _message("user", _context_header(header)),
        _message("assistant", CLOSING_LINE),
    ]
