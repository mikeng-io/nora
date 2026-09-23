"""Conversation-history pre-fill (spec C.7, R-13, R-15).

Exactly three messages, deterministic. All group-derived text is XML-escaped
and enclosed in a block that says, in its own opening tag, what it is and that
it is not an instruction — the same choice the vended AgentSkills plugin makes
for its own injected XML, with the label replaced by the sentence (#314).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

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
from ..domain.models import TagContext, TranscriptRow
from ..domain.prompt import escape
from ..domain.self_presence import Presence
from ..language.culture_render import render_culture
from ..language.note_render import render_notes
from ..language.presence_render import render_presence
from ..language.self_card_render import render_self_card
from ..language.standing_render import render_standing
from ..obs.tracing import annotate, span

_CLOSING_LINE = "Understood. I'll read the conversation and reply as myself."

# `escape`/`_escape_attr` moved to `nora.domain.prompt` when the doctrine loader
# started rendering frontmatter into section attributes and needed the identical
# rule: the layer contract runs agent -> doctrine, so the loader cannot import
# them from here. Re-exported under the names this module has always used.
__all__ = ["TAG_HEADER", "build_prefill", "build_tag_message", "escape"]

# The opening paragraph of `<context_header>` for a TAG — the situation, in the
# model's own terms. Every clause of it is FALSE on a heartbeat fire turn
# (nobody called her, and that is not why the turn is happening), so it is a
# named default rather than a literal inside `_context_header`: the fire path
# passes its own (`nora.app.fire`), and everything around it — the block
# blocks and every escaping rule in this module — is shared rather than
# copied. Duplicating `build_prefill` for the fire path is the thing this
# parameter exists to prevent: R-13/R-13b escaping is pinned to THIS function
# by two named acceptance tests, and a hand-rolled second prefill rendering the
# same transcript would sit outside both.
TAG_HEADER = (
    "You are in a group chat you have been reading all along. You do not "
    "speak unless somebody calls you, and somebody just did — that is why "
    "this turn is happening."
)


# THERE IS NO `_where_line` ANY MORE (design/self-awareness/02). It was one
# sentence — «You are on Discord right now.» — added after she answered a
# Discord tag saying she was on Signal (operator, 2026-08-14), and it was the
# only situational fact she got: the network, not the room, not the group's
# other rooms, not whether any of them was reachable. `<presence>` replaced it
# and says all four, generated each turn. It is a row in `_BLOCKS` below and
# goes through `assemble` like every other block, where `_where_line` was a
# bare string concatenated in front of it. The name table it read moved to
# `nora.domain.self_presence.PLATFORM_NAMES`, because the proactive pass
# renders the same block and may not import this module.


def _message(role: str, text: str) -> dict:
    return {"role": role, "content": [{"text": text}]}


def _now_line(now: datetime | None) -> str:
    """The current local time, stated plainly.

    She had no clock at all — not here, not in the doctrine — so 今晚, 琴日, 而家
    and 尋日 were ungrounded and «what time is it» was unanswerable. Deliberately
    NOT a tool: it is relevant to every turn, so a tool means either paying a
    round trip every time or the model deciding it does not need one, and a
    model that does not know the time also does not know that it doesn't. It
    costs ~15 tokens and cannot fail.

    Injected rather than read from the clock here so a test can pin it.
    """
    if now is None:
        now = datetime.now().astimezone()
    return f"Right now it is {now.strftime('%A %-d %B %Y, %H:%M')} ({now.tzname()}).\n"



#: THE BLOCK TABLE — every block this prefill serves, declared once.
#:
#: `order` is the argument `build_prefill`'s fifteen-line comment used to make
#: and nothing could check: her own past, then theirs, then the unit's open
#: questions, then what she already did about them, then her position, then the
#: room — each widening scope, with the transcript last because it is adjacent
#: to the message it explains. `<standing>` breaks scope order deliberately
#: (`_standing_block`'s docstring argues it) and now says so in a number.
#:
#: **There is no `header` column, and its removal is the point** (#314). It
#: held each block's line in a «What you can see:» catalogue, kept here so that
#: list was DERIVED rather than hand-written beside the blocks — the list had
#: gone stale FIVE times, the last two shipping `<loop_decisions>` and
#: `<people_in_window>` on every turn with no entry at all.
#:
#: Deriving it fixed the staleness and left the real defect: a block was
#: described in TWO places, once here for the catalogue and once at the call
#: site for the opening tag, and the two could disagree (four block types did).
#: Each block now says what it is in its own tag, from its type, so the
#: catalogue has nothing left to say — and the rule that every catalogue line
#: had to survive its block being absent goes with it, because a block that is
#: absent now says nothing rather than saying something unguarded.
#:
#: `key` is narrower than `global` for every row — they vary per turn, per room
#: or per person — which is why every one of them is served in the per-turn
#: message rather than the cached head. `BlockKey`'s docstring argues that rule
#: and `test_no_block_in_this_table_belongs_in_the_cached_head` pins it.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    # design/tool-catalogue phase C. Ahead of `presence`, because it is the
    # steadier of the two: it varies by the room's tool SCOPE (a Discord room carries the
    # music verbs, a Signal room does not), never by the turn or the person.
    # It renders only when tools are deferred (`[llm] defer_tools`); with
    # every schema resident it would be a second copy of them.
    ("tool_catalogue", 3, "room"),
    # WHERE SHE IS, FIRST (design/self-awareness/02). Where `_where_line`
    # sat — straight after the clock — so the turn opens on when and where,
    # then her past, then theirs. `turn`, not `room`: transport health varies
    # between two turns in the same room, which is why it is read at turn
    # start and never cached.
    ("presence", 5, "turn"),
    ("self_card", 10, "person"),
    ("peer_card", 20, "person"),
    ("peer_history", 30, "person"),
    ("people_in_window", 35, "room"),
    ("notes", 40, "unit"),
    ("loop_decisions", 50, "unit"),
    # CULTURE (#324, rebased 2026-09-22). The catalogue line #324 carried
    # here is gone — #314 made every block say what it is from its own
    # type, so a fourth column would be a second copy of that sentence and
    # the drift `_ROW` already cost us once.
    #
    # The ROW is unconditional and the BLOCK is not: it renders only where
    # `TagContext.culture` is filled, which is the participation occasion
    # alone (`FirePath._read_culture`). A row that varied per turn would
    # end the cached prefix's hit rate for everything after it.
    ("culture", 55, "room"),
    ("standing", 60, "room"),
    ("group_transcript", 70, "room"),
)

#: Rendered by `build_tag_message`, in the message AFTER the prefill — declared
#: here with no body because this assembler never emits them. Ordered after the
#: eight above, which is where they already sat.
#:
#: They are built through `ContextBlock` like every other, so each says what it
#: is from its type. That is new: they were HAND-BUILT and carried no framing
#: at all, alone among the model-facing blocks in this prompt, and
#: `<tag_message>` is the one holding a member's verbatim words — the
#: `<peer_card>` finding of the 2026-08-20 audit, arriving on a different
#: block. Declaring them here is what keeps their order data rather than the
#: order of two statements in `build_tag_message`.
_TAG_MESSAGE_BLOCKS: tuple[tuple[str, int], ...] = (
    ("replying_to", 80),
    ("tag_message", 90),
)


#: `name -> (order, key)`, derived from the table above so a block
#: function can look up its own row instead of repeating it.
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

    **It used to take a `note` too, and removing it is #314.** What a block IS
    came in through here from every call site, so 27 of 39 composed their own
    and four block types ended up described two different ways. It comes from
    `nora.domain.block_types` now, keyed on `name`, and there is no parameter
    through which a caller could pass another.
    """
    order, key = _ROW[name]
    return ContextBlock(
        name=name, order=order, key=key,
        body=body, attrs=attrs,
    )


def _restamp(block: ContextBlock | None) -> ContextBlock | None:
    """A block built OUTSIDE this module, stamped with this prompt's `order`
    and `key` — and with NOTHING else, since #314.

    `render_notes`, `render_standing` and `render_decisions` live in their own
    packages and have callers that assemble nothing — the note-candidate pass
    and the proactive prompt both render a block and use it directly. So the
    block's identity and content are theirs, and where it sits in THIS prompt
    is not. They each used to carry an `order` and a `key` anyway, which made
    `_BLOCKS` a second copy of three rows; which copy the prompt actually used
    depended on whether the block rendered that turn.

    What a block IS used to be restamped here too, from a `header` column, and
    that was the same defect one level up: a description a prompt could
    override is a description that means something different in each prompt.
    It now comes from the type and there is nothing here to stamp it with.

    **A thin wrapper over `nora.domain.blocks.restamp` since S3** (`design/
    context-assembly/03-sequencing.md`): this module's OWN `_ROW` is the only
    thing specific to prefill, so it is the only argument this wraps. Three
    other occasions did the same three lines by hand; kept as a named function
    here rather than inlined at each of prefill's four call sites, because
    `_restamp(...)` at a call site is more legible than `restamp(..., _ROW)`
    repeated four times in one function.
    """
    return restamp(block, _ROW)


def _declared(rendered: Sequence[ContextBlock | None]) -> list[ContextBlock]:
    """The block table, with each block's rendered text attached.

    **Every block is declared whether or not it rendered, and the REASON
    changed with #314.** It used to be a cache constraint: an empty block still
    contributed its CATALOGUE line, and a catalogue that varied per turn would
    end the 95-99% prefix hit for everything after it. The catalogue is gone, so
    that reason is gone with it — `assemble` drops empty bodies from the body,
    and nothing else reads them.

    What remains is OBSERVABILITY: `nora.prefill.blocks_declared` /
    `blocks_empty` on the `agent.prefill` span say which blocks had nothing to
    say this turn, which is how a silently-failing read is told apart from a
    genuinely empty one. That is worth the list, and it is a different
    justification from the one this docstring carried — written down because a
    reason that has quietly stopped applying is how a structure outlives its
    purpose.

    The blocks whose ABSENCE is meaningful — «No block does not mean you know
    them and it does not mean nothing was ever recorded» — say so in their own
    description now, which is where a reader of that block will look.
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


def _context_header(header: str = TAG_HEADER) -> str:
    """How this turn works, told to the model in its own terms.

    `header` is the SITUATION paragraph — why this turn is happening at all —
    and it is the only part that differs between audiences. Everything after it
    describes the blocks in front of her, which are the same blocks whether she
    was called or a reminder came due. The tag default is `TAG_HEADER`; see it
    for why the fire path passes its own instead of this function growing a
    branch.

    **Deliberately free of anything that changes between tags** — this block is
    the head of the prompt, and DeepSeek's context cache matches on an exact
    prefix, so the first byte that differs from the last request ends the hit
    for everything after it. The current time used to live here, one line in,
    which meant no two tags ever shared a prefix past this point.

    **This ordering now genuinely pays off — the pinned route PREFIX-caches
    (re-measured 2026-08-08).** Caching is a property of the SERVING PROVIDER and
    MODEL, not of the prompt, and it CHANGED under us: opencode-zen went from
    full-request-only to prefix caching between two measurements of the SAME model
    string. So both numbers below are timestamped, and neither is to be trusted
    without re-measuring `cache_read_input_tokens` on the live route.

        Re-measured 2026-08-08 (Langfuse, per-generation cache_read_input_tokens):
          trace 80a1 call 1   13,182 / 13,425   98%   ← first call of a tag
          trace b005 call 1   16,772 / 16,976   99%
          tail-heavy last call of a turn         ~52-55% (falls to the static floor)

        Superseded 2026-08-06 (kept for the record — this is how much it moved):
          long shared prefix, differing tail    cached_tokens = 0
          byte-identical request                cached_tokens = 1837 / 1838
          i.e. that day it was FULL-REQUEST caching; a 1800-token prefix hit zero.

    The static HEAD — this block + the doctrine + the tool schemas, the bulk of a
    ~13-17k input — now hits 95-99% cache on the first call of every tag AND every
    within-turn loop call, so the expensive part is cached for free across tags.
    The only uncached part is the volatile block after this one (clock, transcript,
    peer card). Stabilising the transcript's relative ages to absolute timestamps
    would recover a few HUNDRED tokens against the ~13k already cached — a rounding
    error, not worth the age-semantics cost (今晚/琴日), so it is deliberately NOT done.

    The ordering rule for this module stays either way: static instructions here,
    every volatile fact — the clock, the transcript, the peer card — in the block
    `build_prefill` puts AFTER this one. Re-measure before claiming a saving; the
    caching layer has already changed once.

    The doctrine files say who Nora IS. This says how she is being RUN — that
    she is mid-conversation in a group she has been reading all along, that
    somebody just called her, what each block in front of her means, and how a
    turn ends. Without it she has voice and character but no situational model,
    and a model with no situational model invents one.

    **It no longer names a single tool, and that is the point of DEC-29.** It
    used to carry a hand-written catalogue of all seven — the second such
    catalogue in the repo, after the one in `conduct.md` that listed three of
    seven and was stale by four when it was deleted. The argument for keeping
    it was that `ToolSpec` had nothing to generate FROM: every native tool ships
    `description=""` and lets Strands derive the model-facing text from the
    callable's docstring, so a generated block would have been a bare name list.
    That was a missing field, not a reason. The registry gained a per-tool
    routing clause, and the doctrine gained one derived section generated from
    it — same table the schemas come from. DEC-150 then deleted both: the
    schemas already carried the routing trigger (measured 85% overlap), the
    closed-world and sourcing rules moved to conduct.md, and the derived
    `kind: capability` tier is gone with its loader machinery.

    What is left here is the only thing that was never derivable: what each
    BLOCK in front of her is, and where she is. Situation, not catalogue.
    `tests/test_prefill.py::test_the_header_names_no_tool_at_all` is what keeps
    the catalogue from growing back.
    """
    # NO CATALOGUE AND NO NOTATION (#314). Both were second copies:
    #
    # «What you can see:» restated, in this message, what every block says about
    # itself in the message after it — and `<notes>` therefore carried
    # `NOTES_HEADER` and `NOTES_BLOCK_NOTE`, two strings for one fact, free to
    # disagree. `catalogue()`'s own docstring argued for DERIVING that list
    # because it had gone stale five times; deriving a second copy keeps it
    # honest without making it worth paying for.
    #
    # «Notation: group_transcript holds one `m` element per message…» described
    # ONE block, by hand, in the prompt's static head — so `title`, `about`,
    # `written`, `person`, `name` and `untrusted` all reached her undefined, and
    # #292 added five more (`at`, `room`, `reply_to`, `delivery`, `role`)
    # without anything noticing. A field's meaning now travels with the block
    # that renders it (`nora.domain.block_types`), which is what stops that.
    #
    # What is left is the SITUATION — why this turn is happening — and the one
    # sentence telling her the blocks are material rather than scenery. That
    # sentence is not a catalogue: it says what to DO with what follows, which
    # nothing else says.
    return ContextBlock(
        name="context_header",
        body=(
            f"{header}\n"
            "This is your material, not just scenery: what you know about these "
            "people is what makes a reply theirs rather than anybody's. Use it "
            "the way a friend who was there would, where it fits. Quoting "
            "somebody's own words back at them is for when they asked what was "
            "said, not for winning a point."
        ),
    ).render()


def _label(row: TranscriptRow, display_name: str) -> str:
    """The one string a row is ever called by.

    Shared with the arrow and with `_replying_to_block` on purpose: the name
    after ↳ has to be BYTE-IDENTICAL to the label on the parent's own line, so
    the model joins the two by literal match with nothing left to infer.

    `person_name` FIRST (DEC-80): the identity-canonical name is what every other
    surface of the prompt calls this person — `tag_message from=`,
    `group_members`, `lookup_person` — so preferring it is what makes the
    transcript join to them by literal match too, exactly as the arrow joins to
    its parent. Falling back to the self-set Signal profile name keeps an
    unmapped account visible as SOMEBODY rather than collapsing it to
    "unknown"; the assembler leaves `person_name` None precisely there.
    """
    if row.is_nora:
        return display_name
    # THROUGH THE HELPER, because the guard has to run AFTER the collapse.
    # `person_name or sender_name or "unknown"` tests the RAW string, so a
    # self-set profile name of a lone non-breaking space is truthy, the
    # fallback never fires, and `ContextBlock.render()`'s collapse then empties
    # it — rendering `<replying_to>` with no author at all beside siblings that
    # have one.
    return speaker_name(row.person_name, row.sender_name)


def _by_ts(rows: Iterable[TranscriptRow]) -> dict[int, TranscriptRow]:
    """`ts -> the FIRST row carrying it`, which is the tie-break both resolvers
    owe each other.

    `ts` is not unique. Two rows can share a millisecond — a burst, a re-import,
    a platform that stamps by second — and until this function existed the two
    places that resolve a quote disagreed about which one it meant:
    `_replying_to_block` scanned with `next(...)` (FIRST wins) while
    `_parent_of` read a dict comprehension (LAST wins). One prompt could then
    carry `<replying_to>` quoting one message and `replying_to="n"` pointing at
    another, for the same pointer — the model reading two different parents for
    one reply.

    FIRST rather than last because a reply points at something that already
    existed: the earliest row bearing the timestamp is the one that was there
    to be quoted.
    """
    out: dict[int, TranscriptRow] = {}
    for row in rows:
        out.setdefault(row.ts, row)
    return out


def _parent_of(
    row: TranscriptRow, by_ts: Mapping[int, TranscriptRow]
) -> TranscriptRow | None:
    """The row this one replies to, resolved from the window already in hand.

    In-window only, which is the whole mechanism: an O(n) dict over rows
    `recent` already returned — no query, no `raw_json` parsing on the reply
    path, no second column. A pointer at a row outside the window, at a pruned
    or pre-migration row, or at itself resolves to nothing.

    An unresolved parent renders a PLAIN line — never `↳ ?`. Teaching the model
    that a symbol can mean "unknown" invites it to emit one; silent degradation
    is R-5's temperament.

    Pass `_by_ts(...)`, not a dict comprehension: see that function for the
    tie-break the two resolvers have to share.
    """
    if row.quoted_ts is None:
        return None
    parent = by_ts.get(row.quoted_ts)
    return None if parent is None or parent.id == row.id else parent


def _transcript_block(
    ctx: TagContext, display_name: str, now: datetime, context_window: int,
) -> ContextBlock:
    """The room, with a HANDLE on every line.

    `#n` is what `send_reply(reply_to=n)` takes, and it is a per-render index
    rather than a row id or a timestamp for `note_handle`'s reason — «an id is
    not something a person says in a chat». Three properties follow from the
    index being ours and small:

    * It is a CLOSED SET. Only 1..N exist, so an out-of-range value is
      refusable rather than a lookup that might hit some other row. A model
      cannot invent a message that was never in front of it.
    * The real handles never reach the model. `ts`, `sender_id` and the
      platform's own message id stay on this side; the tool resolves the index
      back to the row. That keeps a raw identifier off the wire, which is
      R-18's floor, and it is why the index and not the timestamp is rendered.
    * It survives the render. The mapping is positional over
      `ctx.transcript`, so the tool resolves against the same tuple that was
      shown — not a fresh read that may have moved.

    The numbers are stable only WITHIN one turn, which is all `reply_to` needs.

    **AND A MESSAGE IS AN ELEMENT, NOT A LINE.** This block used to be one line
    per row — `#n (age) [name]: body` — joined with `\n`, and a newline in the
    body wrote a whole extra row: a handle COLLIDING with a real one (so
    `reply_to` resolved to a different message than the model read), an age it
    chose, and a name that was somebody else's. `escape` is blind to it — a
    newline is not `&`, `<` or `>` — and R-13/R-13b's two tests pin only the
    angle brackets, which is why it passed them for as long as it did.

    It was never only an attack. A member pressing enter mid-message is the
    common case, and it produced a continuation line with NO handle, NO age and
    NO name immediately above the next real row — so «Grant: I will go / to
    school by bus» read to the model as a fragment belonging to whoever was
    adjacent. That is the operator-reported symptom («Blake: go to school by
    bus»), and it needed no attacker at all.

    Collapsing the body closed it and cost the shape of every paragraph, list
    and pasted block a member wrote. `render_messages` closes it without that
    cost, by changing the SEPARATOR instead of the content: each message is its
    own `<m>` element, `escape` makes a forged `</m>` unwritable, and the body
    between the tags keeps its line breaks. That module's docstring carries the
    argument and the three serialisers it follows.

    So this renderer does NOT call `one_line` and must not: collapsing here
    would throw away the shape the element format exists to preserve. The
    speaker name still is — inside an attribute a newline cannot break out, but
    it makes the tag unreadable for nothing.
    """
    by_ts = _by_ts(ctx.transcript)

    # ROW ID -> the index this render gave it, so a reply points at `n` rather
    # than carrying a clipped copy of the parent's words.
    #
    # Keyed on `id`, not on `ts`, because `_parent_of` has already CHOSEN a row
    # and this map only has to say where that row was shown. Keying on `ts`
    # asked the same ambiguous question a second time, and answered it with the
    # opposite tie-break, so a duplicated timestamp could hand back the index of
    # a row `_parent_of` did not pick.
    index_of = {row.id: i for i, row in enumerate(ctx.transcript, 1)}

    def _parent_index(row: TranscriptRow) -> int | None:
        parent = _parent_of(row, by_ts)
        if parent is None:
            return None
        at, mine = index_of.get(parent.id), index_of.get(row.id)
        # NEVER FORWARD. `quoted_ts` arrives in the sender's envelope, so it can
        # name a message that is BELOW this one in the window — by clock skew,
        # by a shared timestamp, or because somebody wrote it that way. A reply
        # pointing down the page is not a reply, and the model has no way to
        # tell it from one that is.
        if at is None or mine is None or at >= mine:
            return None
        return at

    rows = render_messages(
        ctx.transcript,
        speaker=lambda row: _label(row, display_name),
        body=lambda row: row.body,
        timestamp=lambda row: row.ts,
        replying_to=_parent_index,
        now=now,
        edited=lambda row: row.edit.ts if row.edit else None,
        was=lambda row: row.edit.was if row.edit else None,
    )
    # `window` is R-15, moved onto the block it is about (#314). The header
    # used to say «the last 40 messages» in its catalogue entry for this block,
    # which is where R-15 lived: a model that does not know how far back it can
    # see cannot tell «nobody said that» from «not in what you were shown», and
    # «further back than you were shown» means nothing without a number.
    #
    # It could not stay in the header once the catalogue went, and it could not
    # move into the DESCRIPTION either — it is a per-render value and a
    # description belongs to the type. An attribute is what it always was:
    # data about this block, on this block.
    #
    # `len(ctx.transcript)`, NOT the `context_window` parameter (design/11 §2.5,
    # amended 2026-09-22). `TranscriptInjection._recent` now sizes the fetch
    # PAST `context_window` to chase a reply's quote outside it, so the
    # configured default understates what she was actually shown — the wrong
    # direction for R-15, which exists so she can trust the boundary she is
    # told. `context_window` stays a parameter here for the caller's own
    # bookkeeping; this block no longer reads it.
    return _block(
        "group_transcript",
        attrs=(("window", str(len(ctx.transcript))),),
        body=rows,
    )


def _peer_card_block(ctx: TagContext) -> ContextBlock | None:
    """Who the person talking IS, as far as her own memory has it.

    **This block used to carry no framing at all** — no marker, no sentence,
    no hedge — and was the only model-facing block in the whole prompt that
    carried none (2026-08-20 prompt-architecture audit, F3). Its contents are
    Honcho's model-authored distillation of what people have said, so it was
    the one block reading as durable, verified truth while being neither.
    `_peer_history_block`'s docstring calls the card "the hedge the card does
    not need"; that judgement was about CERTAINTY (an identity fact is not an
    inference about intent) and it stands. It was never a judgement about the
    other axis, which is the one that matters here and is now stated: a
    remembered fact about somebody is not an order from them.

    It hedges itself for the same reason `<group_transcript>` does —
    everything in here traces back to text people typed — and since #314 it
    says so in the type's own words rather than with `untrusted="true"`, whose
    definition lived in a doctrine file this prompt's audience is one of six to
    load.
    """
    if not ctx.peer_card:
        return None
    return _block(
        "peer_card",
        attrs=(("person", ctx.tagger.name if ctx.tagger else "them"),),
        body=render_bullets(ctx.peer_card),
    )


def _self_card_block(ctx: TagContext) -> ContextBlock | None:
    """What she said and did before — episodic continuity, not self-description.

    The attribute name and the framing line are the whole safety property here,
    and they are worth more than the content. These conclusions are distilled
    from HER OWN output and nothing verified them, so the repo's «no
    self-attestation» rule applies exactly: done is never decided by the doer.
    Presented as «this is who you are» they would compete with `voice.md` and
    win, because a concrete observation beats a prescription. Presented as «you
    said this», they are a record — the same kind of thing as the transcript,
    at the same authority, and she can contradict one if she wants to.

    Which is also why the block sits BESIDE `group_transcript` rather than
    anywhere near the doctrine: it is situation-tier, below every authored file.

    **The note now carries a THIRD clause, and it is the whole of A2.** The
    conclusions arrive here with their leading timestamp stripped
    (`nora.infra.honcho.client._STAMP` — read that comment before touching
    this), so a line reads as a timeless fact:

        stored:   [2026-08-19 10:17:08] nora agreed to speak all Chinese after
                  miles_novak requested.
        injected: - nora agreed to speak all Chinese after miles_novak requested.

    A one-time request became a permanent standing directive in under three
    minutes. The old note said only «a record of the past… you may contradict
    any of it», and **permission is not prohibition**: nothing in it marked the
    line as spent. The fix is stated here rather than by restoring the date,
    because the stamp is BATCH-level — one live peer's 102 documents carry six
    distinct stamps, one shared by 77 — and it is destroyed before this module
    can see it: `Recollection.conclusions` is `tuple[str, ...]`, so restoring a
    date would mean widening the port to carry a number that is not the fact's
    own date and would be read as if it were. Saying «undated» is the true
    statement, and it is the one that closes the failure.
    """
    block = render_self_card(ctx.self_card)
    if block is None:
        return None
    return _block("self_card", body=block.body)


def _peer_history_block(ctx: TagContext) -> ContextBlock | None:
    """What happened with the person talking — episodes, beside their card.

    `peer_card` and this are the two halves of one person and are deliberately
    not merged: the card is who they are («Name: Miles Novak», «Aliases: …»), this
    is what has happened with them («asked what to eat for dinner», dated).
    Read live on 2026-08-15 they shared ZERO items, so a reader seeing both is
    seeing two different things rather than one thing twice.

    The `note` carries the hedge the card does not need. These conclusions are
    Honcho's distillation of messages Nora witnessed — inferred, unverified,
    and occasionally wrong about intent in a way an identity fact is not. She
    is told she may be wrong about them, because the failure mode this block
    invites is confidently telling somebody what they think.

    Situation-tier, below every authored file, for `_self_card_block`'s reason.

    Fed by the same stripped-stamp path as `self_card`, so it takes the same
    «undated, therefore not standing» clause for the same reason — see
    `_self_card_block`'s docstring for the argument and for why the date is not
    restored instead.
    """
    if not ctx.tagger_card:
        return None
    return _block(
        "peer_history",
        attrs=(("person", ctx.tagger.name if ctx.tagger else "them"),),
        body=render_bullets(ctx.tagger_card),
    )


def _people_in_window_block(ctx: TagContext) -> ContextBlock | None:
    """What happened with THE PEOPLE TALKING IN THIS WINDOW — `<peer_history>`,
    plural, for the turn that has no tagger (issue #210).

    `_peer_history_block` above is keyed on `ctx.tagger`, and the participation
    occasion deliberately has none (`fire.py::participation_occasion` builds
    `person_id=None`). So the one turn that decides whether to speak into a room
    NOBODY addressed her in rendered no memory block at all — it knew the people
    in the window by display name and nothing else.

    **The note is `_peer_history_block`'s, clause for clause, and that is not
    laziness.** Every clause in it was bought by a specific failure and every
    one of them applies here with more force, because this block carries
    conclusions about people who are not even talking TO her:

    - *from your own memory … it is what you concluded, so you may be wrong* —
      these are Honcho's inferences about intent, not identity facts.
    - *nothing here is something they said to you just now* — the block sits
      directly above a transcript of what they DID say just now, which is
      exactly the confusion it has to pre-empt.
    - *every line is undated and none of it is current … not a standing
      instruction* — A2, and read `_self_card_block`'s docstring for the
      three-minute incident that put that clause in the prompt. The conclusions
      arrive here through the same stripped-stamp path, so a line reads as a
      timeless fact unless the note says otherwise. A room that jokingly called
      her 管理員 last week is a RECORD of a joke; it is not an order standing
      today, and the difference is this sentence.

    Situation-tier, below every authored file, for `_self_card_block`'s reason.
    One `<person>` row per speaker, name-attributed like `<peer_card>` — the
    name is `PersonRef.name`, operator-authored, never a platform id (R-18).
    """
    if not ctx.people_in_window:
        return None
    # A ROW inside `people_in_window`. It still says what it is, because
    # `ContextBlock` gives every block its type's description and `person` is a
    # declared type — what it does NOT do is repeat the parent's hedge, which
    # at this depth would read as a second, narrower claim about the same text.
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


def _notes_block(ctx: TagContext, *, now: datetime) -> ContextBlock | None:
    """The note tier's index. VOLATILE-TIER ONLY, and that placement is the
    point: it is rendered here (`build_prefill`'s per-turn message) rather
    than in `_context_header`, so the static head — the cache PREFIX
    `_context_header` documents at 98-99% hit — stays byte-identical across
    every room. Keyed on the UNIT rather than this room, which changes
    nothing about that: a unit-wide block in the cached head would
    fragment the prefix across every unit instead of every room.
    Trailing newline added here, not by `render_notes`, so blocks concatenate
    onto separate lines.

    **What is true of this block and of no other one above it: it is a
    POINTER, not the content.** Every other injected block is the whole of
    what she is given on its axis — the transcript is the messages, the
    standing block is the position. A `<notes>` line is a title and a summary of
    something whose substance lives behind `note_read`, and that is the
    tier's design rather than an economy (spec §5.1/§5.2): awareness in the
    prompt, content on demand, with §6.2's read-before-write latch built on
    exactly that gap. `NOTES_BLOCK_NOTE` says so in the opening tag, because a
    model shown a summary and nothing else will act on the summary.

    **And it is the one block with no cap on how many entries it may emit.**
    Its siblings take `max_entries` or render a fixed single row; this renders
    every standing note there is, because a note missing from the index is
    invisible to her and invisible is indistinguishable from absent. The
    budget is held in `nora.domain.notes`' per-entry caps and by the curator
    retiring closed notes — see `render_notes`'s own docstring.
    """
    # WITH TODAY (2026-09-10). The block's own hedge says a listed note
    # «may already have settled» and gave her no way to tell which; the
    # anchor date was on the row and rendered nowhere.
    today = now.year * 10000 + now.month * 100 + now.day
    return render_notes(ctx.notes, today=today)



def _presence_block(ctx: TagContext) -> ContextBlock:
    """Where she is on this turn, as `<presence>` (design/self-awareness/02).

    Always a block. A context nobody read a presence for — an offline harness
    with no reader — renders `Presence.minimal`: the network alone, which is
    exactly what `_where_line` said, so no harness regresses to the 2026-08-14
    bug of a turn with no idea where it is.

    Rendered by `render_presence`, the one builder the proactive pass calls
    too. Nothing here composes a line of its own.
    """
    presence = ctx.presence or Presence.minimal(
        ctx.message.platform, direct=ctx.message.conversation_type == "dm",
    )
    return render_presence(presence)


def _standing_block(ctx: TagContext) -> ContextBlock | None:
    """Her own position in THIS conversation, as the debrief left it last turn
    (`design/dreamer-agent-loop/26-standing.md`). Same volatile-tier placement
    and trailing-newline convention as its five siblings above, `""` when there
    is no position yet.

    **It sits LAST, immediately before the transcript, and that breaks the
    widening-scope order the blocks above it follow on purpose.** Every block
    from `<self_card>` to `<notes>` widens — her past, then theirs, then the
    room's slang, then the unit's settled claims, then its open questions.
    Standing is the narrowest thing in the prompt (one conversation, not even a
    unit), so on scope alone it would sit at the front. It sits at the back
    because it is not on that axis at all: it is «where you had got to», and the
    thing it is about is the transcript directly underneath it. Read in order
    the pair says *here is where you were, here is what has been said since*,
    which is the whole job of the tier. Put at the front it reads as one more
    thing she has been told about, next to five blocks that genuinely are.

    It is also, uniquely, the only block here that is HER OWN OUTPUT — and it
    still hedges itself; see `render_standing`'s docstring for why that is not
    a contradiction.
    """
    return render_standing(ctx.standing)


def _culture_block(ctx: TagContext, *, now: datetime) -> ContextBlock | None:
    """What this ROOM is like, as `<culture>` (design/culture-tier).

    **The only block in this prompt that is about the room rather than about a
    moment in it.** Its neighbours all answer «what has happened»: the
    transcript is what was said, `<notes>` what is unsettled, `<standing>` where
    she had got to. This one answers «what is this place, and why is it like
    that» — and that difference is the tier's entire reason to exist, because
    the memory system already produces the other shape and a chronicle of
    dynasties and their dates is not a culture.

    `None` on every turn but the participation occasion's, and that restriction
    is a DECISION rather than an unfinished wiring: `01-the-adaptor §6`
    authorises exactly one consumer and `02-measurement §5` step 5 says the
    block ships with its measurement arm, so a second consumer is a second
    measurement nobody has taken. `FirePath._read_culture` is where the gate
    actually is; this renderer is blind to it and renders whatever the context
    carries.

    `None` also until the room has been folded at least once, which is the
    ordinary state for a while after the tier is enabled — a fold needs new
    messages and a day. `render_culture` emits no block rather than an empty
    one, for `render_standing`'s reason: a room she has never folded is not a
    room she looked at and found featureless.
    """
    return render_culture(ctx.culture, now=now)


def _decisions_block(ctx: TagContext, *, now: datetime) -> ContextBlock | None:
    """What NORA'S OWN LOOP already did across this UNIT, as
    `<loop_decisions>` (issue #199). `""` when there is nothing — which is
    every turn but the two that weigh a floor nobody offered them: the
    participation occasion, and the proactive act turn (added 2026-09-17,
    after two act turns 30 minutes apart duplicated one raise).

    **This is the only block here that is not about the world.** Its five
    neighbours distil what people said, what they are like, what the unit
    has not settled. This one is the record of her own turns: the proactive
    decide and act turns that raised something in this room unprompted, with
    how long ago. Before it, the participation turn could not tell a room
    nobody had spoken to from a room she had raised the same question in two
    hours earlier — 2026-09-10's duplicate, the third leg of it. UNIT and
    not room: a proactive row fires into the unit's home room and nowhere
    else, so on the incident's own rooms a room-scoped block would have carried
    nothing.

    **An empty block is not rendered, and that is a deliberate difference from
    `render_decisions`' own default.** Its siblings here — `<notes>`,
    `<standing>`, `<self_card>` — all emit nothing rather than emit an
    "(empty)" line, and the reason is the prefix cache: a block that appears on
    every turn whether or not it has content is a block every turn pays for.
    The renderer's `empty` text is for a caller that has ONE block and needs
    «nothing here» to be distinguishable from «no log wired»; this caller has
    five neighbours and follows their convention.

    **Named `loop_decisions`, not `decisions`**, because
    `nora.language.proactive` already renders a `<decisions>` block for her
    REMINDER rows. Two blocks under one name in one prompt is an input the
    model cannot disambiguate, and the fix for that is never the instruction.

    It sits with the volatile tier, after `<notes>`: a note is the open
    question, and this is what she has already DONE about the open questions —
    read in order, the pair says *here is what is unsettled, here is what you
    already tried*. `<standing>` stays last, adjacent to the transcript it is
    about, for the reason its own docstring argues.

    Trailing newline added here, not by `render_decisions`, so blocks
    concatenate onto separate lines — `_notes_block`'s convention.
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

    One existed, took a `Sequence[PersonRef]`, and was removed after the leak
    design/12 §3 records: it was fed the whole of `identity.toml`, which is
    Nora's address book across every room, so asked who was in the group she
    listed five people who had never been members. R-18 saw nothing to block —
    the block carried only names — which is the point: by the time text exists
    the disclosure is already authorised, so scope has to be enforced where the
    answer is assembled.

    The parameter went with it rather than being left defaulted to `()`. A dead
    seam accepting an unscoped roster is a loaded gun: it would have passed
    every test in this file while doing nothing, until somebody wired it back
    up. `group_members` (`nora.agent.tools`) answers the same question from
    THIS conversation's own membership list, and only when it is asked.
    """
    # ONE root per prefill (issue #55, Layer 7's other unlit half): before this
    # the largest single input to the model call — which blocks were built,
    # from which reads, at what size — had no trace at all. A turn that timed
    # out or errored INSIDE this assembly (a block that raises, a slow
    # `_peer_history_block` read) left no evidence it had even started.
    # Un-rooted deliberately: `build_prefill` is called from both the tag path
    # (under `engagement`'s root) and the fire path (under its own
    # `occasion.kind` root, `nora.app.fire`) and from a few offline callers
    # with no root open at all (`note_candidates`, `announce`) — `span` with no
    # declared `nora.trace_kind` nests under whichever root is current and, for
    # the callers with none, is exported as a root of its own (R-76's
    # fail-open default), never silently dropped.
    with span("agent.prefill", **{"nora.tag_id": ctx.tag_id}):
        # ONE read of the clock for the whole prefill. It used to be read
        # twice — once for the stated wall time, once for the transcript's
        # ages — so the time she is told it is and the time the ages are
        # measured against came from two different reads of the same clock.
        # Sub-millisecond in effect, and exactly the «interpret once» the
        # spine asks for: everything below computes on one fixed model of now.
        clock = now or datetime.now().astimezone()
        # RENDER first, DECLARE second, ASSEMBLE third. The order the blocks
        # reach her, and the catalogue that introduces them, both come from
        # `_BLOCKS` — so a block cannot be served without an entry, and an
        # entry cannot survive a block that stopped rendering.
        declared = _declared([
            # `ToolSurface.prompt_catalogue` — the SAME admitted set the turn's
            # tools were built from, so it cannot name a tool this turn cannot
            # load. A parameter rather than a `TagContext` field: it is a fact
            # about the tools, which are built beside the prefill, not about
            # what the context assembler read.
            _block("tool_catalogue", body=catalogue) if catalogue else None,
            _restamp(_presence_block(ctx)),
            _self_card_block(ctx),
            _peer_card_block(ctx),
            _peer_history_block(ctx),
            _people_in_window_block(ctx),
            _restamp(_notes_block(ctx, now=clock)),
            _restamp(_decisions_block(ctx, now=clock)),
            _restamp(_culture_block(ctx, now=clock)),
            _restamp(_standing_block(ctx)),
            _transcript_block(ctx, display_name, clock, context_window),
        ])
        # WHICH blocks were actually built (non-empty body — `assemble`'s own
        # test, see `_declared`'s docstring for why a block with nothing to
        # say is still DECLARED), and AT WHAT SIZE: each block's name already
        # names its read (`peer_card` is the peer-card store, `notes` is the
        # note tier, …), so the block name doubles as provenance for where the
        # bytes came from without this module reaching into those stores a
        # second time to ask. Sizes are the rendered body only — the wrapping
        # tag and the catalogue line are the assembler's cost, not the read's.
        rendered = [b for b in declared if b.body]
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
                # `_context_header`'s docstring for the cache measurement that
                # decided this. It is still the first thing after the static
                # instructions, so nothing about how she reads it changes.
                #
                # THE ORDER OF THE BLOCKS BELOW IS `_BLOCKS`, not this
                # expression. The fifteen lines of prose that used to argue
                # the sequence here are gone — they lived beside an order
                # nothing could check against them, which is how
                # `<loop_decisions>` came to ship with no catalogue entry. The
                # argument is now the `order` column and the comment above
                # that table.
                _now_line(clock) + assemble(declared),
            ),
            _message("assistant", _CLOSING_LINE),
        ]


def _replying_to_block(ctx: TagContext, display_name: str) -> str:
    """R-20: the quoted text shown to the model is the RESOLVED PARENT ROW's
    text, or nothing.

    This used to render `ctx.message.quote`'s own text, attributed to a named
    author, verified against nothing. That text comes out of the QUOTING
    sender's envelope, so Alice could construct a quote asserting Bob said
    anything at all and Nora would relay it back into the room as Bob's words.
    R-13 escapes it; escaping does not make a forged attribution safe.

    An unresolvable pointer omits the block entirely rather than rendering the
    author alone: the only author left would be `Quote.author_id`, a raw
    platform identifier — exactly what R-18 exists to keep off the wire, and
    what no block in this module ever puts in a prompt. (The roster block that
    used to make the same refusal explicit is gone; see `build_prefill`.)

    Fixes Bug A on the way past: the author is now a name Nora recorded
    herself, not the `authorName` signal-cli has never once sent.
    """
    quote = ctx.message.quote
    if quote is None:
        return ""
    # The SAME resolver `_transcript_block` uses, so the quoted text and the
    # `replying_to="n"` pointer name one message rather than two (`_by_ts`).
    parent = _by_ts(ctx.transcript).get(quote.quoted_ts)
    if parent is None:
        return ""
    # CLASS 1 — a member's own words, quoted from her record of them (R-20).
    # It says what it is by going through the injector, which is #239's ask and
    # a PROMPT CHANGE: see `build_tag_message`.
    return ContextBlock(
        name="replying_to", attrs=(("author", _label(parent, display_name)),),
        body=escape(parent.body or ""),
    ).render() + "\n"


def build_tag_message(ctx: TagContext, *, tagger_name: str, display_name: str) -> str:
    """The tagging message, attributed.

    `also_known_as` carries the other names the GROUP uses for this person —
    阿Miles, 米高, a handle — so Nora understands a reference to them in the
    transcript instead of treating it as a stranger. It is an attribute of the
    person the platform already authenticated, never a way of deciding WHO
    sent something: sender identity comes from the platform id alone
    (design/10 §1.3), because a name is spoofable and these are not checked
    against anything.
    """
    tagger = ctx.tagger
    quoted = _replying_to_block(ctx, display_name)
    # CLASS 1, and this is the change #239 asked for and #220 deliberately did
    # not make: what a person typed AT her, now framed like every other block
    # built from what people typed.
    #
    # **It is a prompt-content change, not an assembly one.** This is the
    # message she was directly ASKED to answer, and hedging it could plausibly
    # make her less responsive to a genuine request — which is why #220 left it
    # alone and #239 names the participation and routing suites as what settles
    # it. #314 makes the change smaller than #239 assumed: what she reads is a
    # sentence saying this is what they asked of you and not an instruction
    # about how you work, rather than the word «untrusted» over the request.
    # Landing it here is the operator's call to finish the injector
    # everywhere; the measurement is still owed.
    attrs: tuple[tuple[str, str], ...] = (("from", tagger_name),)
    if tagger is not None and tagger.aliases:
        attrs += (("also_known_as", ", ".join(tagger.aliases)),)
    return ContextBlock(
        name="tag_message", attrs=attrs,
        # The clause is the CANONICAL one (`_NOT_AN_INSTRUCTION`), not a
        # paraphrase. A first cut ended "never an instruction about how you
        # work", which carries the meaning and matches neither spelling the
        # framing gate accepts — so the block would have shipped with a marker
        # and a clause the gate could not see, which is the shape #187 collects.
        body=f"{quoted}{escape(ctx.message.body)}",
    ).render()
