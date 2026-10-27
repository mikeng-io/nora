"""Conversation-history pre-fill (R-13, R-15).

Exactly three messages, deterministic. All group-derived text is XML-escaped
and enclosed in a block that says, in its own opening tag, what it is and that
it is not an instruction, the same choice the vended AgentSkills plugin makes
for its own injected XML, with the label replaced by the sentence.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from ..domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_bullets,
    render_messages,
    restamp,
    speaker_name,
)
from ..domain.decision_log import (
    render_decisions,
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
from ..domain.profile import ProfileView
from ..domain.prompt import escape
from ..domain.quote_window import first_by_ts
from ..domain.self_presence import Presence
from ..language.computer_render import render_computer
from ..language.culture_render import (
    render_character,
    render_culture,
    render_self,
    render_unit_culture,
)
from ..language.documents_render import render_documents
from ..language.lesson_render import render_lessons
from ..language.memo_render import render_memos
from ..language.note_render import render_notes
from ..language.presence_render import render_presence
from ..language.profile_render import render_profile, render_profile_people
from ..language.standing_render import render_standing, render_standing_working_set
from ..language.subagents_render import render_subagents
from ..obs.tracing import annotate, span
from .clock import local_now, state_time
from .lineage import DerivedView, derived_view

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
    # WHO SHE IS HERE: her own Character, Nora for this unit. First in the
    # block message, directly after the doctrine (which is the system prompt,
    # read before this message) so the doctrine is read first and wins.
    # `unit`: it varies by the room's unit. The row is unconditional; the
    # block renders only where `TagContext.self_account` is filled, and a
    # unit with no account renders none.
    ("self", 1, "unit"),
    # WHAT SHE HAS LEARNED HERE: her active lessons for this unit, right after
    # `<self>` and so after the doctrine, which wins. Renders only where
    # `TagContext.lessons` is filled.
    ("lessons", 2, "unit"),
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
    ("room_members", 4, "room"),
    # WHERE SHE IS, FIRST. Straight after the clock, so the turn opens on when
    # and where, then her past, then theirs. `turn`, not `room`: transport
    # health varies between two turns in the same room, which is why it is
    # read at turn start and never cached.
    ("presence", 5, "turn"),
    ("peer_card", 20, "person"),
    ("peer_history", 30, "person"),
    ("people_in_window", 35, "room"),
    # THE PRE-FETCH (design/unified-memory/00 step 3b): what her memory holds
    # that this message touches, searched by code from its own seeds. Beside
    # the memory of the people talking it will replace on the graph surface,
    # and before the unit's open questions. `turn`: it is searched from one
    # message. Renders only where `TagContext.recalled` is filled.
    ("recalled", 36, "turn"),
    ("notes", 40, "unit"),
    # WHAT THE GROUP SETTLED AND KEEPS (DEC-306): unit-wide like `notes`, right
    # after them, titles and ids only. Renders only where `TagContext.memos` is
    # filled.
    ("memos", 42, "unit"),
    ("loop_decisions", 50, "unit"),
    # CULTURE. The ROW is unconditional and the BLOCK is not: it renders only
    # where `TagContext.culture` is filled (every tag and fire turn in a folded
    # room, DEC-207, and none while `[culture] enabled = false`). A row that
    # varied per turn would end the cached prefix's hit rate for everything
    # after it.
    ("culture", 54, "room"),
    # THE UNIT'S CULTURE: what the room's whole unit is like, straight after
    # this room's own, so the room is read first and the group second. `room`:
    # it varies by the room's unit. Renders only where `TagContext.unit_culture`
    # is filled.
    ("unit_culture", 55, "room"),
    # CHARACTER (DEC-278): one account per listed person, beside the room's
    # own. `room`, like `culture`: it varies by room (its members), and the
    # row is unconditional while the block renders only where
    # `TagContext.character` is filled.
    ("character", 56, "room"),
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
    ("standing", 60, "room"),
    # The Standing working set. Immediately after `standing` and before the
    # transcript, on purpose: the read order stays "here is my position, here
    # is what I have not told you, here is what has been said since". `room`,
    # `standing`'s own key: this block is keyed off the same
    # `(platform, conversation_id)` row.
    ("standing_working_set", 65, "room"),
    ("group_transcript", 70, "room"),
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
    name: str, *, body: str, attrs: tuple[tuple[str, str], ...] = (),
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

    `render_notes`, `render_standing` and `render_decisions` live in their own
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
            body="",
        )
        for name, order, key in _BLOCKS
    ]
    out += [
        ContextBlock(name=name, order=order, key="turn",
                     body="")
        for name, order in _TAG_MESSAGE_BLOCKS
    ]
    return out


#: The block names `<profile>` replaces when the assembler fills
#: `TagContext.profiles` (`[profile] render`, 45 §5). Their rows stay in
#: `_BLOCKS` — the Honcho surface is the default and the flag is the whole
#: rollback — but on a switched turn their stubs leave the declared list, so
#: the span's `blocks_declared`/`blocks_empty` describe the prompt she got.
_PROFILE_REPLACES = frozenset({
    "self", "peer_card", "peer_history", "people_in_window",
    "culture", "unit_culture", "character",
})


def _context_header(header: str = TAG_HEADER) -> str:
    """How this turn works, told to the model in its own terms.

    `header` is the SITUATION paragraph (why this turn is happening at all)
    and it is the only part that differs between audiences. Everything after it
    describes the blocks in front of her, which are the same blocks whether she
    was called or a reminder came due. The tag default is `TAG_HEADER`; see it
    for why the fire path passes its own instead of this function growing a
    branch.

    **Free of anything that changes between tags.** This block is the head of
    the prompt, and the provider's context cache matches on an exact prefix, so
    the first byte that differs from the last request ends the hit for
    everything after it. The static HEAD (this block, the doctrine and the tool
    schemas) is the bulk of the input and is cached across tags and across
    within-turn loop calls. Caching is a property of the serving provider and
    model, not of the prompt, so re-measure `cache_read_input_tokens` on the
    live route before claiming a saving.

    The ordering rule for this module: static instructions here, every
    volatile fact (the clock, the transcript, the peer card) in the block
    `build_prefill` puts AFTER this one.

    The doctrine files say who Nora IS. This says how she is being RUN: she is
    mid-conversation in a group she has been reading all along, somebody just
    called her, what each block in front of her means, and how a turn ends.
    Without it she has voice and character but no situational model, and a
    model with no situational model invents one.

    **It names no tool** (DEC-29, DEC-150). The model-facing schemas already
    carry each tool's routing trigger, and the closed-world and sourcing rules
    live in conduct.md, so a hand-written catalogue here would be a second
    copy. What is left is what is not derivable: what each BLOCK in front of
    her is, and where she is. `tests/test_prefill.py::
    test_the_header_names_no_tool_at_all` keeps the catalogue from growing
    back.
    """
    # No catalogue and no notation. A «What you can see:» list would restate,
    # in this message, what every block says about itself in the message
    # after it, and a notation paragraph would describe one block by hand in
    # the static head, leaving the other fields undefined. A field's meaning
    # travels with the block that renders it (`nora.domain.block_types`).
    #
    # What is left is the SITUATION (why this turn is happening) and the one
    # sentence telling her the blocks are material rather than scenery. That
    # sentence says what to DO with what follows, which nothing else says.
    return ContextBlock(name="context_header", body=ContextHeader(header).render()).render()


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
    """The `<m>` elements `<group_transcript>` carries, without the block
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
    by_ts = _by_ts(transcript)

    # ROW ID -> the index this render gave it, so a reply points at `n` rather
    # than carrying a clipped copy of the parent's words.
    #
    # Keyed on `id`, not on `ts`, because `_parent_of` has already CHOSEN a row
    # and this map only has to say where that row was shown. Keying on `ts`
    # would ask the same ambiguous question a second time with the opposite
    # tie-break, so a duplicated timestamp could return the index of a row
    # `_parent_of` did not pick.
    index_of = {row.id: i for i, row in enumerate(transcript, 1)}

    def _parent_index(row: TranscriptRow) -> int | None:
        parent = _parent_of(row, by_ts)
        if parent is None:
            return None
        at, mine = index_of.get(parent.id), index_of.get(row.id)
        # NEVER FORWARD. `quoted_ts` arrives in the sender's envelope, so it can
        # name a message that is BELOW this one in the window (clock skew, a
        # shared timestamp, or a hand-written value). A reply pointing down the
        # page is not a reply, and the model has no way to tell it from one.
        if at is None or mine is None or at >= mine:
            return None
        return at

    return render_messages(
        transcript,
        speaker=lambda row: _label(row, display_name),
        body=lambda row: row.body,
        timestamp=lambda row: row.ts,
        replying_to=_parent_index,
        now=now,
        edited=lambda row: row.edit.ts if row.edit else None,
        was=lambda row: row.edit.was if row.edit else None,
        turn=lambda row: row.turn_handle,
    )


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
        "group_transcript",
        attrs=(("window", str(len(ctx.transcript))),),
        body=rows,
    )


def _peer_card_block(ctx: TagContext) -> ContextBlock | None:
    """Who the person talking IS, as far as her own memory has it.

    Its contents are Honcho's model-authored distillation of what people have
    said, so the block must not read as durable, verified truth: a remembered
    fact about somebody is not an order from them. `_peer_history_block`'s
    docstring calls the card "the hedge the card does not need"; that is a
    judgement about CERTAINTY (an identity fact is not an inference about
    intent), not about this other axis.

    It hedges itself for the same reason `<group_transcript>` does (everything
    in it traces back to text people typed), in the type's own words rather
    than with `untrusted="true"`, whose definition lived in a doctrine file
    this prompt's audience is one of six to load.
    """
    if not ctx.peer_card:
        return None
    return _block(
        "peer_card",
        attrs=(("person", ctx.tagger.name if ctx.tagger else "them"),),
        body=render_bullets(ctx.peer_card),
    )


def _peer_history_block(ctx: TagContext) -> ContextBlock | None:
    """What happened with the person talking: episodes, beside their card.

    `peer_card` and this are the two halves of one person and are deliberately
    not merged: the card is who they are («Name: Miles Novak», «Aliases: …»),
    this is what has happened with them («asked what to eat for dinner»,
    dated). They are written to hold different things, so a reader seeing both is
    seeing two views rather than one thing twice.

    The `note` carries the hedge the card does not need. These conclusions are
    Honcho's distillation of messages Nora witnessed: inferred, unverified,
    and occasionally wrong about intent in a way an identity fact is not. She
    is told she may be wrong about them, because the failure mode this block
    invites is confidently telling somebody what they think.

    Situation-tier, below every authored file: conclusions distilled from
    what was said are a record, at the same authority as the transcript, and
    nothing verified them.

    **The note carries a clause saying every line is undated and none of it is
    a standing instruction.** The conclusions arrive with their leading
    timestamp stripped (`nora.infra.honcho.client._STAMP`; read that comment
    before touching this), so a line reads as a timeless fact:

        stored:   [<timestamp>] nora agreed to speak all Chinese after
                  miles_novak requested.
        injected: - nora agreed to speak all Chinese after miles_novak requested.

    Without the clause a one-time request reads as a permanent standing
    directive. **Permission is not prohibition**: «you may contradict any of
    it» does not mark a line as spent. The date is not restored because the
    stamp is BATCH-level (one stamp is shared by many conclusions) and is
    destroyed before this module can see it (`Recollection.conclusions` is
    `tuple[str, ...]`), so a restored date would be a number that is not the
    fact's own date and would be read as if it were. Saying «undated» is the
    true statement.
    """
    if not ctx.tagger_card:
        return None
    return _block(
        "peer_history",
        attrs=(("person", ctx.tagger.name if ctx.tagger else "them"),),
        body=render_bullets(ctx.tagger_card),
    )


def _room_members_block(ctx: TagContext) -> ContextBlock | None:
    """`<room_members>`: this room's listed persons, each with the other
    names the group calls them, so a nickname used ABOUT someone resolves
    to them (aliases otherwise reach her only for the tagger).

    Authored data (identity.toml via the room's own `persons` list), never
    the whole file and never a platform id (R-18). Absent when the room lists
    nobody identity maps, which is also a `persons = "*"` room."""
    if not ctx.room_members:
        return None
    def one_line(text: str) -> str:
        # A newline in a name or alias would render as a member line of its
        # own; operator-authored, but the block's shape is one line each.
        return " ".join(text.split())

    lines = [
        f"{one_line(ref.name)}: {', '.join(one_line(a) for a in ref.aliases)}"
        if ref.aliases else one_line(ref.name)
        for ref in ctx.room_members
    ]
    return _block("room_members", body=escape("\n".join(lines)))


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
      these are Honcho's inferences about intent, not identity facts.
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
            name="person", attrs=(("name", name),), body=render_bullets(conclusions),
        ).render()
        for name, conclusions in ctx.people_in_window
        if conclusions
    )
    if not rows:
        return None
    return _block(
        "people_in_window",
        body=rows,
    )


def _profile_name_of(ctx: TagContext) -> Callable[[str], str]:
    """The `told by <name>` resolver for this turn's profile blocks.

    Built from the room's listed persons and the tagger — the names already in
    this prompt, so a told line's teller joins to them by literal match. An
    id with no name in this turn renders as "someone" to keep this attribution
    readable. Operator-written person_id slugs may appear in prompts; R-18
    forbids reply egress of E.164 numbers, UUIDs and key-shaped strings.
    """
    names = {ref.person_id: ref.name for ref in ctx.room_members}
    if ctx.tagger is not None:
        names.setdefault(ctx.tagger.person_id, ctx.tagger.name)

    def name_of(person_id: str) -> str:
        return names.get(person_id, "someone")

    return name_of


def _profile_blocks(ctx: TagContext, *, now: datetime) -> list[ContextBlock]:
    """The switch's blocks (45 §5): one `<profile>` per entity of the turn,
    stamped into the rows of the blocks they replace.

    Nora's own account sits at `self`'s row (first, directly after the
    doctrine, for the same reason), the room's at `culture`'s and the unit's
    at `unit_culture`'s, and the persons share `render_profile_people`'s one
    budget at `peer_card`'s row — "her own past, then theirs", the table's
    order argument. Returns [] on the Honcho surface (`ctx.profiles` empty).
    """
    if not ctx.profiles:
        return []
    name_of = _profile_name_of(ctx)
    blocks: list[ContextBlock] = []
    people: list[ProfileView] = []
    for view in ctx.profiles:
        if view.entity == "person":
            people.append(view)
            continue
        row = {"nora": "self", "room": "culture", "unit": "unit_culture"}.get(
            view.entity,
        )
        if row is None:
            continue
        block = render_profile(view, name_of=name_of, now=now)
        if block is not None:
            order, key = _ROW[row]
            blocks.append(replace(block, order=order, key=key))
    order, key = _ROW["peer_card"]
    blocks.extend(
        replace(b, order=order, key=key)
        for b in render_profile_people(people, name_of=name_of, now=now)
    )
    return blocks


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
        "recalled", body=body,
        attrs=(("partial", "yes"),) if ctx.recalled_partial else (),
    )


def _lineage(ctx: TagContext) -> DerivedView | None:
    """Which derived rows this room may be shown. `None` when lineage is off
    for this context (no target, or `[lineage] mode = "off"`): every block
    renders unfiltered.

    The blocks this view GATES are four: `<notes>` (a row each), `<standing>`
    and `<standing_working_set>` (Standing's two sections) and `<culture>`
    (its one section). `<unit_culture>` and `<character>` render derived rows
    too but carry no lineage and pass through no view: their guard is their
    unit key, and no Character session reads presence (R-31, DEC-290). Every
    other block is authored, live, the Record of this room, or memory, and
    carries no lineage. A section that fails is dropped whole, never cut
    inside its prose; the rest of its row still renders.
    """
    target = ctx.lineage_room
    if target is None or target.mode == "off":
        return None
    return derived_view(
        notes=ctx.notes, standing=ctx.standing, culture=ctx.culture,
        target=target,
    )


def _notes_block(
    ctx: TagContext, *, now: datetime, view: DerivedView | None = None,
) -> ContextBlock | None:
    """The note tier's index. VOLATILE-TIER ONLY, and that placement is the
    point: it is rendered in `build_prefill`'s per-turn message rather than in
    `_context_header`, so the static head (the cache PREFIX) stays
    byte-identical across every room. Keyed on the UNIT rather than this room;
    a unit-wide block in the cached head would fragment the prefix across
    every unit instead of every room. Trailing newline added here, not by
    `render_notes`, so blocks concatenate onto separate lines.

    **This block is a POINTER, not the content.** Every other injected block
    is the whole of what she is given on its axis (the transcript is the
    messages, the standing block is the position). A `<notes>` line is a
    title and a summary of something whose substance lives behind `note_read`:
    awareness in the prompt, content on demand, with the read-before-write
    latch built on exactly that gap. `NOTES_BLOCK_NOTE` says so in the
    opening tag, because a model shown a summary and nothing else will act on
    the summary.

    **It has no cap on how many entries it may emit.** Its siblings take
    `max_entries` or render a fixed single row; this renders every standing
    note there is, because a note missing from the index is invisible to her
    and invisible is indistinguishable from absent. The budget is held in
    `nora.domain.notes`' per-entry caps and by closed notes being retired
    (the Cognition session's `note_retire`); see `render_notes`'s own
    docstring.
    """
    # WITH TODAY: the block's own hedge says a listed note «may already have
    # settled», so the row's anchor date has to be rendered for her to tell
    # which.
    today = now.year * 10000 + now.month * 100 + now.day
    notes = view.notes if view is not None and _enforced(ctx) else ctx.notes
    return render_notes(notes, today=today)


def _enforced(ctx: TagContext) -> bool:
    """`[lineage] mode = "on"`: a failing section is not rendered. `shadow`
    computes the same answer and only reports it on the prefill span."""
    return ctx.lineage_room is not None and ctx.lineage_room.mode == "on"


def _shown(ctx: TagContext, view: DerivedView | None, block: str) -> bool:
    if view is None or not _enforced(ctx):
        return True
    return bool(getattr(view, block))



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
    return _block("spoken_turn", body=SPOKEN_TURN)


def _standing_block(
    ctx: TagContext, view: DerivedView | None = None,
) -> ContextBlock | None:
    """Her own position in THIS conversation, as the debrief left it last turn.
    Same volatile-tier placement and trailing-newline convention as its five
    siblings above, `""` when there is no position yet.

    **It sits LAST, immediately before the transcript, and that breaks the
    widening-scope order the blocks above it follow, on purpose.** Every block
    from `<self>` to `<notes>` widens: her own account, then theirs, then the
    room's slang, then the unit's settled claims, then its open questions.
    Standing is the narrowest thing in the prompt (one conversation, not even
    a unit), so on scope alone it would sit at the front. It sits at the back
    because it is not on that axis: it is «where you had got to», and the
    thing it is about is the transcript directly underneath it. Read in order
    the pair says *here is where you were, here is what has been said since*.
    Put at the front it reads as one more thing she has been told about, next
    to five blocks that genuinely are.

    It is also the only block here that is HER OWN OUTPUT, and it still
    hedges itself; see `render_standing`'s docstring for why that is not a
    contradiction.
    """
    if not _shown(ctx, view, "standing"):
        return None
    return render_standing(ctx.standing)


def _standing_working_set_block(
    ctx: TagContext, *, now: datetime, view: DerivedView | None = None,
) -> ContextBlock | None:
    """Options she found and has not given this room, as
    `<standing_working_set>`. Sits immediately after `_standing_block` in
    `_BLOCKS` (order 65 beside `standing`'s 60); `_standing_block`'s reasoning
    applies unchanged: read as a pair, «here is my position, here is what I
    have not told you», before the transcript underneath both of them.

    `ctx.standing` already carries `working_set`: no second context field or
    store read, the same way `Standing.body` reaches `_standing_block`.
    """
    if not _shown(ctx, view, "standing_working_set"):
        return None
    return render_standing_working_set(
        ctx.standing.working_set if ctx.standing is not None else (), now=now,
    )


def _culture_block(
    ctx: TagContext, *, now: datetime, view: DerivedView | None = None,
) -> ContextBlock | None:
    """What this ROOM is like, as `<culture>`.

    **The only block in this prompt that is about the room rather than about a
    moment in it.** Its neighbours answer «what has happened»: the transcript
    is what was said, `<notes>` what is unsettled, `<standing>` where she had
    got to. This one answers «what is this place, and why is it like that»; a
    chronicle of dynasties and their dates is not a culture.

    Filled on the tag path (`TranscriptInjection._culture`) and on every fire
    occasion (`FirePath._read_culture`) (DEC-207). This renderer is blind to
    who filled it and renders whatever the context carries.

    `None` until the room has been folded at least once, which is the ordinary
    state for a while after the tier is enabled (a fold needs new messages and
    a day). `render_culture` emits no block rather than an empty one, for
    `render_standing`'s reason: a room she has never folded is not a room she
    looked at and found featureless.
    """
    if not _shown(ctx, view, "culture"):
        return None
    return render_culture(ctx.culture, now=now)


def _decisions_block(ctx: TagContext, *, now: datetime) -> ContextBlock | None:
    """What NORA'S OWN LOOP already did across this UNIT, as `<loop_decisions>`.
    `""` when there is nothing, which is every turn but the two that weigh a
    floor nobody offered them: the participation occasion and the proactive
    act turn.

    **This is the only block here that is not about the world.** Its five
    neighbours distil what people said, what they are like, what the unit has
    not settled. This one is the record of her own turns: the proactive
    decide and act turns that raised something in this room unprompted, with
    how long ago. Without it the participation turn could not tell a room
    nobody had spoken to from a room she had raised the same question in two
    hours earlier. UNIT and not room: a proactive row fires into the unit's
    home room and nowhere else, so a room-scoped block would carry nothing.

    **An empty block is not rendered, a deliberate difference from
    `render_decisions`' own default.** Its siblings here (`<notes>`,
    `<standing>`, `<self>`) all emit nothing rather than an "(empty)" line,
    because a block that appears on every turn whether or not it has content
    is a block every turn pays for. The renderer's `empty` text is for a
    caller that has ONE block and needs «nothing here» to be distinguishable
    from «no log wired»; this caller has five neighbours and follows their
    convention.

    **Named `loop_decisions`, not `decisions`**, so it cannot collide with a
    `<decisions>` block for reminder rows. Two blocks under one name in one
    prompt is an input the model cannot disambiguate, and the fix for that is
    never the instruction.

    It sits with the volatile tier, after `<notes>`: a note is the open
    question, and this is what she has already DONE about the open questions;
    read in order, the pair says *here is what is unsettled, here is what you
    already tried*. `<standing>` stays last, adjacent to the transcript it is
    about, for the reason its own docstring argues.

    Trailing newline added here, not by `render_decisions`, so blocks
    concatenate onto separate lines (`_notes_block`'s convention).
    """
    if not ctx.decisions:
        return None
    return render_decisions(ctx.decisions, now=now)


def build_prefill(
    ctx: TagContext, *, display_name: str, context_window: int,
    now: datetime | None = None, header: str = TAG_HEADER,
    catalogue: str = "",
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
        # Lineage, decided ONCE for the whole prefill: the four blocks it
        # gates (notes, culture, standing, standing_working_set) all read this
        # one view; `<unit_culture>` and `<character>` read none (`_lineage`).
        view = _lineage(ctx)
        # RENDER first, DECLARE second, ASSEMBLE third. The order the blocks
        # reach her comes from `_BLOCKS`, so a block cannot be served without
        # an entry, and an entry cannot survive a block that stopped rendering.
        # THE SWITCH (45 §5): with `ctx.profiles` filled, the seven blocks it
        # replaces are not rendered and their stubs leave the declared list —
        # the profile blocks take their rows (`_profile_blocks`).
        switched = bool(ctx.profiles)
        declared = _declared([
            # `ToolSurface.prompt_catalogue`: the SAME admitted set the turn's
            # tools were built from, so it cannot name a tool this turn cannot
            # load. A parameter rather than a `TagContext` field: it is a fact
            # about the tools, which are built beside the prefill, not about
            # what the context assembler read.
            _block("tool_catalogue", body=catalogue) if catalogue else None,
            _room_members_block(ctx),
            None if switched else _restamp(render_self(ctx.self_account, now=clock)),
            _restamp(render_lessons(ctx.lessons)),
            _restamp(_presence_block(ctx)),
            None if switched else _peer_card_block(ctx),
            None if switched else _peer_history_block(ctx),
            None if switched else _people_in_window_block(ctx),
            _restamp(_notes_block(ctx, now=clock, view=view)),
            _restamp(render_memos(ctx.memos, tz=clock.tzinfo or UTC)),
            _restamp(_decisions_block(ctx, now=clock)),
            None if switched else _restamp(_culture_block(ctx, now=clock, view=view)),
            None if switched else _restamp(render_unit_culture(ctx.unit_culture, now=clock)),
            None if switched else _restamp(render_character(ctx.character, now=clock)),
            _restamp(render_computer(ctx.computer)),
            _restamp(render_documents(ctx.documents)),
            _restamp(render_subagents(ctx.subagents)),
            _restamp(_standing_block(ctx, view)),
            _restamp(_standing_working_set_block(ctx, now=clock, view=view)),
            _transcript_block(ctx, display_name, clock, context_window),
            _spoken_turn_block(ctx),
            _recalled_block(ctx),
        ])
        if switched:
            declared = [
                b for b in declared if b.name not in _PROFILE_REPLACES
            ] + _profile_blocks(ctx, now=clock)
        # WHICH blocks were actually built (non-empty body, `assemble`'s own
        # test; see `_declared`'s docstring for why a block with nothing to
        # say is still DECLARED), and AT WHAT SIZE: each block's name already
        # names its read (`peer_card` is the peer-card store, `notes` is the
        # note tier, …), so the block name doubles as provenance for where the
        # bytes came from. Sizes are the rendered body only; the wrapping tag
        # is the assembler's cost, not the read's.
        rendered = [b for b in declared if b.body]
        # LINEAGE, on every mode but `off`: what the four derived blocks
        # withheld from this room, or would have in `shadow`. The shadow
        # reading that gates `on` is read from these two attributes.
        if view is not None and ctx.lineage_room is not None:
            annotate(**{
                "nora.prefill.lineage_mode": ctx.lineage_room.mode,
                "nora.prefill.lineage_withheld": ",".join(
                    f"{name}:{n}" for name, n in view.withheld
                ),
            })
        annotate(**{
            "nora.prefill.blocks_declared": len(declared),
            "nora.prefill.blocks_rendered": ",".join(b.name for b in rendered),
            "nora.prefill.blocks_empty": ",".join(
                b.name for b in declared if not b.body
            ),
            "nora.prefill.size_chars": sum(len(b.body) for b in rendered),
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


def _replying_to_block(ctx: TagContext, display_name: str) -> str:
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
        return ""
    # The SAME resolver `_transcript_block` uses, so the quoted text and the
    # `replying_to="n"` pointer name one message rather than two (`_by_ts`).
    parent = _by_ts(ctx.transcript).get(quote.quoted_ts)
    if parent is None:
        return ""
    # CLASS 1: a member's own words, quoted from her record of them (R-20).
    # It says what it is by going through the injector; see
    # `build_tag_message`.
    return ContextBlock(
        name="replying_to", attrs=(("author", _label(parent, display_name)),),
        body=escape(render_file_links(parent.body or "")),
    ).render() + "\n"


def build_tag_message(ctx: TagContext, *, tagger_name: str, display_name: str) -> str:
    """The tagging message, attributed.

    `also_known_as` carries the other names the GROUP uses for this person
    (阿Miles, 米高, a handle) so Nora understands a reference to them in the
    transcript instead of treating it as a stranger. It is an attribute of the
    person the platform already authenticated, never a way of deciding WHO
    sent something: sender identity comes from the platform id alone, because
    a name is spoofable and these are not checked against anything.
    """
    tagger = ctx.tagger
    quoted = _replying_to_block(ctx, display_name)
    # CLASS 1: what a person typed AT her, framed like every other block built
    # from what people typed.
    #
    # **This is a prompt-content change, not an assembly one.** This is the
    # message she was directly ASKED to answer, and hedging it could plausibly
    # make her less responsive to a genuine request, so the participation and
    # routing suites are what measure it. What she reads is a sentence saying
    # this is what they asked of you and not an instruction about how you
    # work, rather than the word «untrusted» over the request.
    attrs: tuple[tuple[str, str], ...] = (("from", tagger_name),)
    if tagger is not None and tagger.aliases:
        attrs += (("also_known_as", ", ".join(tagger.aliases)),)
    return ContextBlock(
        name="tag_message", attrs=attrs,
        # The clause is the CANONICAL one (`_NOT_AN_INSTRUCTION`), not a
        # paraphrase: a paraphrase can carry the meaning and still match
        # neither spelling the framing gate accepts, so the block would ship
        # with a marker and a clause the gate cannot see.
        body=f"{quoted}{escape(render_file_links(ctx.message.body))}",
    ).render()
