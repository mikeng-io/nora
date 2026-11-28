"""Conversation-history pre-fill (R-13, R-15).

Exactly three messages, deterministic. All group-derived text is XML-escaped
and enclosed in a block that says, in its own opening tag, what it is and that
it is not an instruction, the same choice the vended AgentSkills plugin makes
for its own injected XML, with the label replaced by the sentence.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from nora.domain.composed import Composed, compose_body, legacy_composed

from ..domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_bullets,
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
from ..obs.tracing import annotate, span
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
__all__ = ["TAG_HEADER", "build_prefill", "build_tag_message", "escape", "render_transcript"]

# There is no `_where_line`: where she is arrives as `<presence>`, a row in
# `_BLOCKS` below that goes through `assemble` like every other block. It
# states the network, the room, the group's other rooms and whether each is
# reachable, generated each turn. The platform name table lives in
# `nora.domain.self_presence.PLATFORM_NAMES` so that other renderers of the
# block can use it without importing this module.


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



#: THE BLOCK TABLE: every block this prefill serves, declared once.
#:
#: `order` runs from her own past, to theirs, to the unit's open questions, to
#: what she already did about them, to her position, to the room: each
#: widening scope, with the transcript last because it is adjacent to the
#: message it explains. `<standing>` breaks scope order deliberately
#: (`_standing_block`'s docstring argues it) and says so in its number.
#:
#: There is no `header` column. Each block says what it is in its own tag,
#: from its type (`nora.domain.block_types`), so a block is described in one
#: place and the opening tag cannot disagree with a catalogue. A block that is
#: absent says nothing.
#:
#: `key` is narrower than `global` for every row (they vary per turn, per room
#: or per person), which is why every one of them is served in the per-turn
#: message rather than the cached head. `BlockKey`'s docstring argues that
#: rule and `test_no_block_in_this_table_belongs_in_the_cached_head` pins it.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    # The tool catalogue. Ahead of `presence`, because it is the steadier of
    # the two: it varies by the room's tool SCOPE (a Discord room carries the
    # music verbs, a Signal room does not), never by the turn or the person.
    # It renders only when tools are deferred (`[llm] defer_tools`); with
    # every schema resident it would be a second copy of them.
    ("tool_catalogue", 3, "room"),
    # WHO IS WHO: this room's listed persons with the names the group calls
    # them. `room`: it changes only with policy.toml or identity.toml, so it
    # stays in the steady front of the per-turn message. Never the whole
    # identity file.
    ("members", 4, "room"),
    # WHERE SHE IS, FIRST. Straight after the clock, so the turn opens on when
    # and where, then her past, then theirs. `turn`, not `room`: transport
    # health varies between two turns in the same room, which is why it is
    # read at turn start and never cached.
    ("presence", 5, "turn"),
    ("peer_card", 20, "person"),
    ("peer_history", 30, "person"),
    ("people_in_window", 35, "room"),
    # THE PRE-FETCH (docs/design/cognition/graph.md): what her memory holds
    # that this message touches, searched by code from its own seeds. Beside
    # the memory of the people talking it will replace on the graph surface,
    # and before the unit's open questions. `turn`: it is searched from one
    # message. Renders only where `TagContext.recalled` is filled.
    ("recalled", 36, "turn"),
    # What is on her computer for this room, read fresh at turn start. `turn`:
    # its ages move every turn. Before `standing` because it is the state of
    # the machine, and `standing` + the transcript are read as the pair «where
    # I was, what was said since». Renders only in a room using the computer
    # (`ComputerContextReader`).
    ("computer", 57, "turn"),
    # This room's documents with their ids, so a revision asked for in a later
    # turn can name the document it means. `turn`: its ages move every turn.
    # Directly after `computer` (both are «what I have made in this room») and
    # before `standing`. Renders only where `[documents]` is on and the room
    # has one.
    ("documents", 58, "turn"),
    # This room's sub-agent jobs (running and finished within a day), read
    # fresh from the job rows. `turn`: its ages and a running job's progress
    # move every turn. Tag turn only, and only under `[subagent] enabled` with
    # a job in the window.
    ("subagents", 59, "turn"),
    # THE ONE STANDING BLOCK (docs/design/cognition/graph.md): her
    # position, open judgements, pending acts and what she already did —
    # projected per destination from the canonical stores, unit-wide like
    # `notes`, and last of the state blocks so the transcript follows
    # «where everything stands». It replaces the room paragraph, the
    # working set, the ledger index and the interim shared-state block.
    ("standing", 66, "unit"),
    ("transcript", 70, "room"),
    # HOW THIS TURN REACHED HER, AND HOW HER REPLY WILL. Renders only when the
    # message she is answering was spoken in a voice channel
    # (`NormalizedMessage.voice`, set only by `nora/web/voice.py`); a typed
    # turn renders nothing. Last, so it sits directly before the message it
    # describes. `turn`: it is a fact about one message. Never in the
    # doctrine or the header, both cached: a voice turn differs in what it IS,
    # not in the law.
    ("spoken_turn", 75, "turn"),
)

#: Rendered by `build_tag_message`, in the message AFTER the prefill, and
#: declared here with no body because this assembler never emits them. They
#: are built through `ContextBlock` like every other, so each says what it is
#: from its type; `<tag_message>` holds a member's verbatim words. Declaring
#: them here keeps their order data rather than the order of two statements in
#: `build_tag_message`.
_TAG_MESSAGE_BLOCKS: tuple[tuple[str, int], ...] = (
    ("replying_to", 80),
    ("tag_message", 90),
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


def _restamp(block: ContextBlock | None) -> ContextBlock | None:
    """A block built OUTSIDE this module, stamped with this prompt's `order`
    and `key` and with nothing else.

    `render_notes`, `render_shared_state` and `render_decisions` live in their own
    packages and have callers that assemble nothing (the note-candidate pass
    and the proactive prompt both render a block and use it directly). So the
    block's identity and content are theirs, and where it sits in THIS prompt
    is not. What a block IS comes from its type, never from a prompt, so it
    means the same thing in every prompt.

    A thin wrapper over `nora.domain.blocks.restamp`: this module's `_ROW` is
    the only prefill-specific argument. Named here so each call site reads
    `_restamp(...)` rather than `restamp(..., _ROW)`.
    """
    return restamp(block, _ROW)


def _declared(rendered: Sequence[ContextBlock | None]) -> list[ContextBlock]:
    """The block table, with each block's rendered text attached.

    Every block is declared whether or not it rendered. `assemble` drops empty
    bodies from the body, so the reason is observability:
    `nora.prefill.blocks_declared` / `blocks_empty` on the `agent.prefill` span
    say which blocks had nothing to say this turn, which is how a
    silently-failing read is told apart from a genuinely empty one.

    The blocks whose ABSENCE is meaningful («No block does not mean you know
    them and it does not mean nothing was ever recorded») say so in their own
    description, which is where a reader of that block will look.
    """
    built = {b.name: b for b in rendered if b is not None}
    out = [
        built.get(name) or ContextBlock(
            name=name, order=order, key=key,
            body=compose_body(""),
        )
        for name, order, key in _BLOCKS
    ]
    out += [
        ContextBlock(name=name, order=order, key="turn",
                     body=compose_body(""))
        for name, order in _TAG_MESSAGE_BLOCKS
    ]
    return out


_PROFILE_REPLACES = frozenset({"peer_card", "peer_history", "people_in_window"})


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


def _peer_card_block(ctx: TagContext) -> ContextBlock | None:
    """Who the person talking IS, as far as her own memory has it.

    Its contents are a model-authored distillation of what people have
    said, so the block must not read as durable, verified truth: a remembered
    fact about somebody is not an order from them. `_peer_history_block`'s
    docstring calls the card "the hedge the card does not need"; that is a
    judgement about CERTAINTY (an identity fact is not an inference about
    intent), not about this other axis.

    It hedges itself for the same reason `<transcript>` does (everything
    in it traces back to text people typed), in the type's own words rather
    than with `untrusted="true"`, whose definition lived in a doctrine file
    this prompt's audience is one of six to load.
    """
    if not ctx.peer_card:
        return None
    return _block(
        "peer_card",
        attrs=(("person", ctx.tagger.name if ctx.tagger else "them"),),
        body=compose_body(render_bullets(ctx.peer_card)),
    )


def _peer_history_block(ctx: TagContext) -> ContextBlock | None:
    """What happened with the person talking: episodes, beside their card.

    `peer_card` and this are the two halves of one person and are deliberately
    not merged: the card is who they are («Name: Miles Novak», «Aliases: …»),
    this is what has happened with them («asked what to eat for dinner»,
    dated). They are written to hold different things, so a reader seeing both is
    seeing two views rather than one thing twice.

    The `note` carries the hedge the card does not need. These conclusions are
    a model's distillation of messages Nora witnessed: inferred, unverified,
    and occasionally wrong about intent in a way an identity fact is not. She
    is told she may be wrong about them, because the failure mode this block
    invites is confidently telling somebody what they think.

    Situation-tier, below every authored file: conclusions distilled from
    what was said are a record, at the same authority as the transcript, and
    nothing verified them.

    **The note carries a clause saying every line is undated and none of it is
    a standing instruction.** The conclusions arrive with their leading
    timestamp stripped, so a line reads as a timeless fact:

        stored:   [<timestamp>] nora agreed to speak all Chinese after
                  miles_novak requested.
        injected: - nora agreed to speak all Chinese after miles_novak requested.

    Without the clause a one-time request reads as a permanent standing
    directive. **Permission is not prohibition**: «you may contradict any of
    it» does not mark a line as spent. The date is not restored because the
    stamp is BATCH-level (one stamp is shared by many conclusions) and is
    destroyed before this module can see it (`TagContext.tagger_card` is
    `tuple[str, ...]`), so a restored date would be a number that is not the
    fact's own date and would be read as if it were. Saying «undated» is the
    true statement.
    """
    if not ctx.tagger_card:
        return None
    return _block(
        "peer_history",
        attrs=(("person", ctx.tagger.name if ctx.tagger else "them"),),
        body=compose_body(render_bullets(ctx.tagger_card)),
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


def _people_in_window_block(ctx: TagContext) -> ContextBlock | None:
    """What happened with THE PEOPLE TALKING IN THIS WINDOW: `<peer_history>`,
    plural, for the turn that has no tagger.

    `_peer_history_block` above is keyed on `ctx.tagger`, and the participation
    occasion deliberately has none (`fire.py::participation_occasion` builds
    `person_id=None`). So the turn that decides whether to speak into a room
    NOBODY addressed her in needs this block to know anything about the people
    in the window beyond their display names.

    **The note is `_peer_history_block`'s, clause for clause.** Every clause
    guards a specific failure and every one applies here with more force,
    because this block carries conclusions about people who are not even
    talking TO her:

    - *from your own memory … it is what you concluded, so you may be wrong*:
      these are a model's inferences about intent, not identity facts.
    - *nothing here is something they said to you just now*: the block sits
      directly above a transcript of what they DID say just now, which is
      exactly the confusion it has to pre-empt.
    - *every line is undated and none of it is current … not a standing
      instruction*: see `_peer_history_block`'s docstring. The conclusions
      arrive through the same stripped-stamp path, so a line reads as a
      timeless fact unless the note says otherwise. A room that jokingly
      called her 管理員 last week is a RECORD of a joke, not an order standing
      today, and the difference is this sentence.

    Situation-tier, below every authored file, for `_peer_history_block`'s
    reason. One `<person>` row per speaker, name-attributed like `<peer_card>`;
    the name is `PersonRef.name`, operator-authored, never a platform id
    (R-18).
    """
    if not ctx.people_in_window:
        return None
    # A ROW inside `people_in_window`. It still says what it is, because
    # `ContextBlock` gives every block its type's description and `person` is a
    # declared type. It does NOT repeat the parent's hedge, which at this depth
    # would read as a second, narrower claim about the same text.
    rows = "\n".join(
        ContextBlock(
            name="person", attrs=(("name", name),), body=compose_body(render_bullets(conclusions)),
        ).render()
        for name, conclusions in ctx.people_in_window
        if conclusions
    )
    if not rows:
        return None
    return _block(
        "people_in_window",
        body=compose_body(rows),
    )


def _recalled_block(ctx: TagContext) -> ContextBlock | None:
    """`<recalled>`: the pre-fetch's items, one JSON object a line, exactly
    as `memory_search` answers with them (`render_packet` built and cut them
    in the assembler; nothing is re-rendered here). Escaped like every
    group-derived block; `json.dumps` already keeps each on one line."""
    if not ctx.recalled:
        return None
    body = "\n".join(
        escape(json.dumps(item, ensure_ascii=False)) for item in ctx.recalled
    )
    return _block(
        "recalled", body=compose_body(body),
        attrs=(("partial", "yes"),) if ctx.recalled_partial else (),
    )


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


def _standing_block(
    ctx: TagContext, *, now: datetime | None = None,
) -> ContextBlock | None:
    """Folded source objects read for this room."""
    block = None
    # The room's folded source-object heads (docs/design/platform/
    # observations.md), typed rows under the SAME Standing presentation: the open poll,
    # the pin set, the member count — each with its ordering and coverage
    # flags so a gapped or partial state never reads as settled truth.
    if ctx.source_objects:
        annotated = {row.id: i for i, row in enumerate(ctx.transcript, 1)
                     if row.reactions is not None}
        reaction_targets = {e.handle: annotated[e.target_message_id]
                            for e in ctx.source_events if e.kind == "reaction"
                            and e.target_message_id in annotated}
        index_of = {row.id: i for i, row in enumerate(ctx.transcript, 1)}
        # One expanded target per assembly: a `<source_event>` in the
        # transcript already carrying `target_*` owns it for the handle —
        # the object repeats the state, never the words.
        event_targets = {e.handle for e in ctx.source_events
                         if e.target is not None}

        def _target_shown(e: Any) -> bool:
            return (e.target is not None
                    and e.target_message_id not in index_of
                    and e.handle not in event_targets)

        objects = render_elements(
            ctx.source_objects, tag="source_object",
            attrs=lambda e, _i: (
                ("kind", e.kind), ("object", e.handle),
                ("ordering", e.ordering or None),
                ("coverage", e.coverage or None),
                ("rev", e.revision or None),
                # `on` is the numbered transcript row this reaction's tally
                # annotates — the SAME numbering `replying_to="n"` uses, so a
                # pointer and the transcript name one message. Absent while the
                # target is off-window: the row then keeps its plain source
                # state in Standing and no pointer invents a visible target.
                ("on", f"#{reaction_targets[e.handle]}"
                 if e.kind == "reaction" and e.handle in reaction_targets
                 else None),
                # `observed` is the newest SOURCE time applied — absent
                # when the source's clock is unknown, with `time` naming
                # the non-upstream clock and `received` stating the
                # bridge's own receipt instead.
                ("time", e.time_quality if e.time_quality != "upstream"
                        else None),
                ("observed",
                 age(e.at_ms, now) if now is not None and e.at_ms > 0
                 and e.time_quality != "unknown"
                 else None),
                ("received",
                 age(e.received_ms, now)
                 if now is not None and e.received_ms
                 and e.time_quality != "upstream"
                 else None),
                # The affected message, as the `<source_event>` elements
                # carry it — only when the transcript does not show it and
                # no interleaved event already expanded this handle.
                ("target_author", e.target.author if _target_shown(e) else None),
                ("target_at", age(e.target.at_ms, now)
                 if _target_shown(e) and now is not None
                 and e.target.at_ms > 0 else None),
                ("target_text", e.target.text if _target_shown(e) else None),
                ("target_edited", age(e.target.edited_ms, now)
                 if _target_shown(e) and now is not None
                 and e.target.edited_ms else None),
                ("target_was", e.target.was if _target_shown(e) else None),
                ("target_deleted", "yes"
                 if _target_shown(e) and e.target.deleted else None),
                ("target_unresolved", "yes"
                 if (e.target_message_id is not None
                     and e.target_message_id not in index_of
                     and e.target is None) else None),
            ),
            # The tally rides once, on the visible target row — `on` naming
            # it is the pointer, so the row's own body then repeats nothing.
            # Off-window there is nowhere else the state shows, and the row
            # keeps its plain `state`/`summary` in Standing.
            body=lambda e: (
                None if e.kind == "reaction" and e.handle in reaction_targets
                else e.state or e.summary),
        )
        if ctx.source_objects_more:
            objects = (f"{objects}\n<source_objects_more "
                       f"count=\"{ctx.source_objects_more}\"/>")
        if block is None:
            block = ContextBlock(name="standing", body=legacy_composed(objects))
        else:
            block = replace(block, body=legacy_composed("\n".join((block.body.text, objects))))
    return block


def build_prefill(
    ctx: TagContext, *, display_name: str, context_window: int,
    now: datetime | None = None, header: str = TAG_HEADER,
    catalogue: str = "",
    graph_profiles: tuple[ContextBlock, ...] = (),
) -> list[dict]:
    """The three prefill messages. **There is deliberately no roster block.**

    `header` is the situation paragraph (`_context_header`'s first argument
    after the window). It is a parameter rather than a second `build_*_prefill`
    because R-13/R-13b escaping is pinned to THIS function by
    `test_prefill_escapes_angle_brackets` and `test_forged_closing_tag_is_escaped`
    (`tests/test_acceptance_rules.py`): a hand-rolled prefill for the heartbeat,
    rendering the same group-authored transcript, would sit outside both named
    tests while looking correct.

    There is no roster parameter. A roster fed from the whole of
    `identity.toml` (Nora's address book across every room) would list people
    who were never members of the group, and R-18 cannot block it: by the time
    text exists the disclosure is already authorised, so scope has to be
    enforced where the answer is assembled. An unscoped roster seam, even
    defaulted to `()`, would pass every test while doing nothing until
    somebody wired it back up. `group_members` (`nora.agent.tools`) answers
    the same question from THIS conversation's own membership list, and only
    when it is asked.
    """
    # ONE root per prefill, so which blocks were built, from which reads, at
    # what size is traced, and a turn that times out or errors INSIDE this
    # assembly (a block that raises, a slow `_peer_history_block` read) leaves
    # evidence it started.
    # Un-rooted deliberately: `build_prefill` is called from both the tag path
    # (under `engagement`'s root) and the fire path (under its own
    # `occasion.kind` root, `nora.app.fire`) and from a few offline callers
    # with no root open at all (`announce`). `span` with no declared
    # `nora.trace_kind` nests under whichever root is current and, for the
    # callers with none, is exported as a root of its own (R-76's fail-open
    # default), never silently dropped.
    with span("agent.prefill", **{"nora.tag_id": ctx.tag_id}):
        # ONE read of the clock for the whole prefill, so the time she is told
        # it is and the time the transcript's ages are measured against come
        # from the same read: everything below computes on one fixed model of
        # now.
        clock = now or local_now()
        # RENDER first, DECLARE second, ASSEMBLE third. The order the blocks
        # reach her comes from `_BLOCKS`, so a block cannot be served without
        # an entry, and an entry cannot survive a block that stopped rendering.
        # Profile blocks take the place of their legacy context blocks.
        switched = bool(graph_profiles)
        declared = _declared([
            # `ToolSurface.prompt_catalogue`: the SAME admitted set the turn's
            # tools were built from, so it cannot name a tool this turn cannot
            # load. A parameter rather than a `TagContext` field: it is a fact
            # about the tools, which are built beside the prefill, not about
            # what the context assembler read.
            restamp(render_tool_catalogue(catalogue), _ROW) if catalogue else None,
            _room_members_block(ctx),
            _restamp(_presence_block(ctx)),
            None if switched else _peer_card_block(ctx),
            None if switched else _peer_history_block(ctx),
            None if switched else _people_in_window_block(ctx),
            _restamp(render_computer(ctx.computer)),
            _restamp(render_documents(ctx.documents)),
            _restamp(render_subagents(ctx.subagents)),
            _restamp(_standing_block(ctx, now=clock)),
            _transcript_block(ctx, display_name, clock, context_window),
            _spoken_turn_block(ctx),
            _recalled_block(ctx),
        ])
        if switched:
            declared = [
                b for b in declared if b.name not in _PROFILE_REPLACES
            ] + list(graph_profiles)
        # Report the blocks selected by `assemble`, including empty blocks
        # that opt into self-closing. Size counts body characters only.
        rendered = [b for b in declared if b.body or b.when_empty != "drop"]
        annotate(**{
            "nora.prefill.blocks_declared": len(declared),
            "nora.prefill.blocks_rendered": ",".join(b.name for b in rendered),
            "nora.prefill.blocks_empty": ",".join(
                b.name for b in declared if not b.body
            ),
            "nora.prefill.size_chars": sum(len(b.body.text) for b in rendered),
        })
        return [
            _message("user", _context_header(header)),
            _message(
                "user",
                # The clock leads the VOLATILE block, not the header: see
                # `_context_header`'s docstring for the cache reason. It is
                # still the first thing after the static instructions.
                #
                # THE ORDER OF THE BLOCKS BELOW IS `_BLOCKS`, not this
                # expression; the argument for the sequence is the `order`
                # column and the comment above that table.
                _now_line(clock) + assemble(declared),
            ),
            _message("assistant", CLOSING_LINE),
        ]


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
    # CLASS 1: a member's own words, quoted from her record of them (R-20).
    # It says what it is by going through the injector; see
    # `build_tag_message`.
    return ContextBlock(
        name="replying_to", attrs=(("author", _label(parent, display_name)),),
        body=legacy_composed(escape(render_file_links(parent.body or ""))),
    )


def build_tag_message(ctx: TagContext, *, tagger_name: str, display_name: str) -> str:
    """The message or fire-occasion body, attributed.

    `also_known_as` carries the other names the GROUP uses for this person
    (阿Miles, 米高, a handle) so Nora understands a reference to them in the
    transcript instead of treating it as a stranger. It is an attribute of the
    person the platform already authenticated, never a way of deciding WHO
    sent something: sender identity comes from the platform id alone, because
    a name is spoofable and these are not checked against anything.
    """
    quoted = _replying_to_block(ctx, display_name)
    return _tag_block(
        ctx, tagger_name, f"{quoted.render()}\n" if quoted is not None else "",
    ).render()


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
