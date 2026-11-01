"""The agenda as the model reads it: a clock, one block per item and the index
of what is already recorded, every one a context-assembly block.
Pure: strings in, a string out, no store, no clock.

**Every question the session is asked, the input answers first** (AGENTS.md's
spine). She is asked whether a room settled a plan, is still deciding, or is
talking around it: that turns on WHO said WHAT, WHEN, and whether anyone
answered, so each item carries its messages with an author, a time and an age
(a block that cannot say which end is newest reads every line as «just now»);
a note may cite only the ids it rests on, so
each message carries its id; «is this already written down» turns on the
Register, so the index follows the agenda. A row states what happened and never
what to conclude: attributes say «edited» and name a cue's kind, and stop
there.

**The id shown IS the id required.** `note_create` requires the citation as a
bare `[7998]` and the attribution as `(Ann)`, so the prompt must not show a
different form. Every message is `<m n="7998" from="Ann" ...>` through the
shared row-id window (`nora.domain.blocks.render_row_window`): `n` is the real
message id and `from` the name the validator compares a `(<names>)` slot to.
`speaker_of` is that one name, read here for the render and by the session for
the validator (`shown_for`), so the two cannot differ, and the block types say
what each is for (`nora.domain.block_types`: `agenda`).

**One block says what it is once.** The agenda is ONE `agenda` block whose body is
the items, each an `<item>` holding its rows, and the events are ONE `events`
block holding a `<room_events>` per room; the type's description and field
glosses are written once per prompt, not once per item. The nesting wrapper is
`nora.domain.blocks.render_group`.

**Bounded, and it says when it cut.** The Record rows spend at most
`RenderBudget.record_chars`, shared across the items by what each needs, counted
as RENDERED (a row's element, attributes and escapes included), and a message's
words are cut at `message_chars`. `_fit`, `_shares` and `RenderBudget` are a row-SELECTION
pre-step: they choose which rows an item shows and the blocks render what they
chose. When an item's rows do not fit, the OLDEST are dropped and the item says
how many and which ids (`earlier_not_shown`, `earlier_range`): a session that is
not told it was handed a slice reads the slice as the whole conversation. The one
row an allowance always keeps is shown with its words cut so the ESCAPED element
fits (`&` is five characters, `<` four, and the cut is measured on what is
rendered), never with its element cut: the element is what keeps one row one row.
The cap holds for every allowance that can hold a bare element; one smaller than
a row's own attributes keeps the element with one character of words and so
exceeds the allowance by that overhead, the only place it yields.

**Group-derived text is one line, and is escaped.** A message's words are
collapsed to one line (`nora.domain.prompt.clip`, which is `one_line` and a
cut) and escaped by the shared renderer, and every attribute value is collapsed
and escaped there: a body that contained a newline, a closing tag or a quote
can neither write a second row nor an element (R-13, R-13c; a forged row).

**Where it lives.** In `nora.language`, not `nora.domain`, because it is built
from the `nora.language` renderers, which a domain module may not import. The
types it renders (`AgendaItem`, `Room`, `Stimulus`, `Grounding` and
its lines) stay in `nora.domain`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, tzinfo

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_elements,
    render_group,
    render_row_window,
    restamp,
    speaker_name,
)
from nora.domain.cognition_ground import (
    UNNAMED_ROOM,
    Grounding,
    RecordItem,
    RollupLine,
    SocialLine,
    StayDeviation,
    record_ref,
)
from nora.domain.cognition_session import AgendaItem, Room
from nora.domain.external_files import render_file_links
from nora.domain.models import REACTION_LABEL_MAX, emoji_display
from nora.domain.prompt import ago, clip, escape, legal, one_line
from nora.domain.shared_state import SharedView
from nora.domain.social_stream import describe_read
from nora.domain.stimuli import Stimulus
from nora.language.culture_render import render_self
from nora.language.event_render import render_event_rows
from nora.language.memo_render import memo_rows
from nora.language.note_render import render_note_rows
from nora.language.profile_render import render_profile
from nora.language.shared_render import render_shared_state

#: What this prompt carries, declared as data. `render_agenda` builds its
#: blocks from this table and walks it with `assemble`, so `order` IS the order
#: the model reads: the clock, which room(s) the read tools look through, one
#: `agenda` (ONE block, the items nested in it in the agenda's own order), then
#: what is already recorded, the notes and then ONE `events` block holding each
#: agenda room's events.
#:
#: `key="turn"` throughout: this prompt is its own call pattern with no
#: established cache boundary, so a key borrowed from `prefill._BLOCKS` would not
#: describe the same thing here, and nothing routes on `key` yet either way.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    # Her own Character · Nora for the unit first, directly
    # after the doctrine (the system prompt). No block while the unit has no account.
    ("self", 5, "turn"),
    # THE SWITCH (45 §5): her Profile account instead of the prose `self`
    # when `[memory.profile] read` is on; the grounder fills one or the other,
    # never both, so the shared order cannot collide.
    ("profile", 5, "turn"),
    ("clock", 10, "turn"),
    ("reads", 20, "turn"),
    ("agenda", 30, "turn"),
    ("note_index", 40, "turn"),
    # The one `standing` block (design/notebook-memory-types/20-standing.md):
    # positions, open judgements, pending acts, delivered results — read by
    # code from the canonical stores, right after the note index.
    ("standing", 45, "turn"),
    ("events", 50, "turn"),
    # What the unit settled and keeps (DEC-306), LAST of the Register and on
    # its own budget, so no number of memos can push an agenda room's events
    # out.
    ("memos", 55, "turn"),
    ("graph", 60, "turn"),
)

#: `name -> (order, key)`, derived so no call site restates a row `_BLOCKS`
#: already gives it.
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}

#: The prompt's separator between blocks. The blocks are several lines each, so
#: a blank line is what reads as the boundary between them.
_SEP = "\n\n"


@dataclass(frozen=True, slots=True)
class RenderBudget:
    """How much of the prompt the grounding may spend. Every value is a
    STARTING guess, set in `[cognition]`."""

    #: All items' Record rows together, counted as rendered.
    record_chars: int = 8000
    #: One message's words.
    message_chars: int = 400
    #: What a single item is always allowed, however many items share the rest.
    min_item_chars: int = 900
    #: The Register index rows, notes, memos and per-room events each.
    register_lines: int = 40
    #: The Register's rows together, counted as rendered (the blocks' own
    #: description and attributes are not spent from it).
    register_chars: int = 3600
    #: The memos' OWN rows and characters (DEC-306), never drawn from
    #: `register_chars`: a memo is a title, and the open questions and the
    #: room's events are what an agenda acts on. Newest first; the rest counted.
    memo_lines: int = 20
    memo_chars: int = 1200


def room_key(room: Room) -> str:
    return f"{room.platform}:{room.conversation_id}"


def room_label(room: Room, labels: Mapping[str, str]) -> str:
    """What the prompt calls a room: its label, and never its conversation id
    (R-18; `UNNAMED_ROOM` for a room with none). The id is what a tool's `room`
    argument takes, so it stays THERE, in the tool's own schema."""
    return f"«{labels.get(room_key(room)) or UNNAMED_ROOM}»"


def speaker_of(author: str) -> str:
    """The name a message is shown under, and the name the note validator
    compares a `(<names>)` slot to: ONE function for both, so the `from` she
    reads and the sender she is held to are the same string
    (`nora.domain.note_body.ShownMessage.sender`, which the session fills from
    this).

    The collapse and the `unknown` floor are `speaker_name`'s, the one the
    sibling passes use (`row_speaker`), and the removal of characters XML cannot
    carry is the one the renderer applies to an attribute, done FIRST so a name
    that is nothing but such characters is the floor and not an empty string.
    `[`, `]` and `:` are kept: an attribute is not the `[who]: body` line they
    delimited, and a note line's `(<names>)` takes a name with them (the
    validator reads the trailing `[<ids>]` of a line, not the name's own
    brackets)."""
    return speaker_name(legal(author))


def clock_line(now_ms: int, last_session_ms: int | None, tz: tzinfo) -> str:
    """The time now in the group's own day, and how long since the last look.
    An agenda whose input cannot say WHEN reads every item as «just now»
    (AGENTS.md: a judgement that turns on time needs the time in its input)."""
    now = datetime.fromtimestamp(now_ms / 1000, tz=tz)
    stamp = f"{now:%A %-d %B %Y, %H:%M} ({getattr(tz, 'key', None) or now.tzname()})"
    # `last_session_ms` is when the unit's last session ENDED (`LastSession.
    # ended_at_ms`), not when someone last spoke: the newest message says how
    # active the room is, and the clock line says how long since SHE looked.
    since = (
        "This is your first look at this group."
        if last_session_ms is None
        else f"You last looked at this group {ago(now_ms - last_session_ms)}."
    )
    return f"It is {stamp}. {since}"


def _when(at_ms: int, now_ms: int, tz: tzinfo) -> str:
    """`14:05 (1 h 12 min ago)`, with the day when it is not today."""
    at = datetime.fromtimestamp(at_ms / 1000, tz=tz)
    today = datetime.fromtimestamp(now_ms / 1000, tz=tz).date()
    face = f"{at:%H:%M}" if at.date() == today else f"{at:%a %-d %b %H:%M}"
    return f"{face} ({ago(now_ms - at_ms)})"


def _window(times: Sequence[int], now_ms: int, tz: tzinfo) -> str:
    """When an item's messages were sent: `at 14:50, 40 min ago`, or
    `from 14:05 to 14:50, the newest 40 min ago` when they span a minute."""
    if not times:
        return ""
    newest, oldest = max(times), min(times)
    at = f"{datetime.fromtimestamp(oldest / 1000, tz=tz):%H:%M}"
    if newest - oldest < 60_000:
        return f"at {at}, {ago(now_ms - newest)}"
    to = f"{datetime.fromtimestamp(newest / 1000, tz=tz):%H:%M}"
    return f"from {at} to {to}, the newest {ago(now_ms - newest)}"


def returning(strike: int) -> str:
    """A returning item says so: without it two items of one room (the fresh
    messages and the ones an earlier session left open) read the same. The
    `returning` attribute's value; empty when the item is not returning."""
    if strike <= 0:
        return ""
    noun = "session" if strike == 1 else "sessions"
    return f"left open by {strike} earlier {noun}"


def _words(text: str, limit: int) -> str:
    """A message's words as one line cut to `limit`; none at all for a limit of
    nothing (the measure of what a row costs without them).

    A shared file link becomes its handle BEFORE the cut: cut first, a link
    split by the cut no longer parses, so half a URL and a prefix of the file
    id would reach the model, and a stub long enough to parse would carry a
    different id and a wrong code. Swapping first also keeps `_shrunk`'s search
    monotonic: the text being cut no longer depends on where it is cut."""
    return clip(render_file_links(text), limit) if limit > 0 else ""


@dataclass(frozen=True, slots=True)
class _Row:
    """One readable row of an item, before it is rendered: where it sorts, and
    how to render it. `show(None)` is the row as it is normally shown;
    `show(n)` shows its words cut to `n` characters, which is how the one row an
    allowance must keep is made to fit without cutting its element."""

    at_ms: int
    id: int
    show: Callable[[int | None], str]


def _message_row(
    rec: RecordItem, cues: Sequence[str], *, now_ms: int, tz: tzinfo, message_chars: int,
) -> _Row:
    """A person's message, an edit of one, a reaction to one or a deletion of
    one, as `<m n="<message id>" at from ...>words</m>`. `n` is the message's
    own id (`render_row_window` writes it first); a reaction's is the message
    reacted to, and its `from` and words are that message's."""
    when = _when(rec.at_ms, now_ms, tz)
    reacted = rec.kind == "reaction"
    deleted = rec.kind == "delete"
    withdrawn = deleted or (not reacted and rec.text is None)
    edited = rec.kind == "edit" or (rec.kind == "message" and rec.edited)

    def show(limit: int | None) -> str:
        cap = message_chars if limit is None else min(limit, message_chars)
        # The reaction annotation is budgeted ALONGSIDE the words: the same
        # cut shrinks it, pairs that do not fit fold into a `+N` marker, and
        # `reactions_observed` always stays — the coverage and its time are
        # the part of the state a cut may not spend.
        label_cap = (
            REACTION_LABEL_MAX if limit is None
            else min(limit, REACTION_LABEL_MAX)
        )
        words = "" if deleted or rec.text is None else _words(rec.text, cap)
        return render_row_window(
            [rec], row_id=lambda r: r.message_id,
            attrs=lambda r: (
                ("at", when), ("from", speaker_of(r.author)),
                ("reaction", emoji_display(r.emoji or "?") if reacted else None),
                ("reaction_removed", "yes" if reacted and r.removed else None),
                (
                    "reactions",
                    r.reactions.bounded_label(label_cap) if r.reactions else None,
                ),
                (
                    "reactions_observed",
                    (
                        f"{r.reactions.coverage} "
                        f"{_when(r.reactions.observed_at, now_ms, tz)}"
                    )
                    if r.reactions is not None
                    and r.reactions.observed_at is not None
                    else None,
                ),
                ("edited", "yes" if edited else None),
                ("withdrawn", "yes" if withdrawn else None),
                ("deleted", "yes" if deleted else None),
                ("cues", ", ".join(cues) if cues else None),
            ),
            body=lambda _r: words,
        )

    return _Row(rec.at_ms, rec.message_id, show)


def _own_row(rec: RecordItem, *, now_ms: int, tz: tzinfo, message_chars: int) -> _Row:
    """Nora's own send, marked as hers: `own="yes"`. It is context the room's
    messages may be answering, never a stimulus."""
    when = _when(rec.at_ms, now_ms, tz)

    def show(limit: int | None) -> str:
        cap = message_chars if limit is None else min(limit, message_chars)
        label_cap = (
            REACTION_LABEL_MAX if limit is None
            else min(limit, REACTION_LABEL_MAX)
        )
        words = "" if rec.text is None else _words(rec.text, cap)
        return render_row_window(
            [rec], row_id=lambda r: r.message_id,
            attrs=lambda r: (
                ("at", when), ("from", speaker_of(r.author)), ("own", "yes"),
                (
                    "reactions",
                    r.reactions.bounded_label(label_cap) if r.reactions else None,
                ),
                (
                    "reactions_observed",
                    (
                        f"{r.reactions.coverage} "
                        f"{_when(r.reactions.observed_at, now_ms, tz)}"
                    )
                    if r.reactions is not None
                    and r.reactions.observed_at is not None
                    else None,
                ),
                ("turn", r.turn_handle),
                ("withdrawn", "yes" if r.text is None else None),
            ),
            body=lambda _r: words,
        )

    return _Row(rec.at_ms, rec.message_id, show)


def _shrunk(row: _Row, room: int) -> str:
    """The row with its words cut so the RENDERED element, escapes included, is
    at most `room` characters; with one character of words when even the bare
    element is longer. The cut is measured on the rendered row, never on the raw
    words: `&` renders as five characters and `<` as four, so a cut by raw
    length would let a run of them spend three times its share."""
    lo, hi = 1, max(len(row.show(None)), 1)
    while lo < hi:                     # the largest cut that still fits
        mid = (lo + hi + 1) // 2
        if len(row.show(mid)) <= room:
            lo = mid
        else:
            hi = mid - 1
    while lo > 1 and len(row.show(lo)) > room:     # a trailing space can break monotony
        lo -= 1
    return row.show(lo)


def _fit(rows: Sequence[_Row], allowance: int) -> tuple[list[str], int]:
    """The NEWEST rows that fit `allowance`, rendered, in order, and how many
    older ones were left out. At least one row is always kept, its WORDS cut to
    fit when the newest is longer than its share: `record_chars` is a hard cap.
    Cost and cut are both the RENDERED length (`_shrunk`), so the cap holds for
    markup-heavy words too. The cut is in the words and never in the element, so
    a kept row is always a whole row; an allowance smaller than the element's own
    attributes keeps the element with one character of words, the one case the
    cap yields."""
    kept: list[str] = []
    spent = 0
    for row in reversed(rows):
        text = row.show(None)
        cost = len(text) + 1
        if not kept and cost > allowance:
            text = _shrunk(row, allowance - 1)
            cost = len(text) + 1
        if kept and spent + cost > allowance:
            break
        kept.append(text)
        spent += cost
    kept.reverse()
    return kept, len(rows) - len(kept)


def _shares(needs: Sequence[int], total: int, floor: int) -> list[int]:
    """Each item's allowance: the smallest need first, each given what it
    needs or an even share of what is left (never under `floor`, which itself
    shrinks to `total // items` so the shares sum to at most `total`), so a chatty
    room does not starve a quiet one and unused budget flows to the room that
    needs it."""
    order = sorted(range(len(needs)), key=lambda i: needs[i])
    out = [0] * len(needs)
    left = total
    # The floor SHRINKS when items x floor would exceed the budget: the total
    # is a hard cap, never a suggestion the floor may overrun. With
    # `floor <= total // n`, `left >= remaining * floor` holds at every step
    # (a share never exceeds `left // remaining`, or the floor, which that
    # bound covers), so the shares sum to at most `total`.
    floor = min(floor, total // len(needs)) if needs else floor
    for pos, i in enumerate(order):
        fair = max(floor, left // (len(order) - pos))
        out[i] = min(needs[i], fair)
        left -= out[i]
    return out


def _span(start_ms: int | None, end_ms: int | None, now_ms: int, tz: tzinfo) -> str:
    if start_ms is None:
        return ""
    if end_ms is None:
        return f"since {_when(start_ms, now_ms, tz)}"
    return f"from {_when(start_ms, now_ms, tz)} to {_when(end_ms, now_ms, tz)}"


def _join_names(names: Sequence[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _rollup_sentence(line: RollupLine, *, now_ms: int, tz: tzinfo) -> str:
    """A Rollup row as a fact, its ref's fields spelled out: WHO (roster name),
    WHAT kind, WHEN (the interval) and WHERE (a place, when the row names one).
    No conclusion: a row the rhythm scored anomalous is said to be that, not to
    mean something."""
    who = _join_names(line.names)
    span = _span(line.start_ms, line.end_ms, now_ms, tz)
    if line.kind == "colocation":
        return f"{who} were in the same place {span}"
    if line.kind == "arrival":
        at = f" at {line.place}" if line.place else ""
        return f"{who} arrived{at} {span}"
    if line.kind == "deviation_silence":
        return (
            f"{who}: no message in any of the group's rooms and never at home, {span} "
            "(presence and the platforms were healthy throughout)"
        )
    if line.kind == "deviation_anomaly":
        at = f" at {line.stay_place}" if line.stay_place else ""
        usual = "".join(f"; {_deviation(d)}" for d in line.deviations)
        return f"{who}: a stay the rhythm scored anomalous{at}, {span}{usual}"
    return f"{who}: {line.kind} {span}"


def _clock(minute_of_day: float | None) -> str:
    m = round(minute_of_day or 0) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _deviation(d: StayDeviation) -> str:
    """How a stay differed from what is usual for this person, as a fact with
    both sides of it: what happened, and what usually does (DEC-279)."""
    if d.kind == "duration":
        return (
            f"stayed {round((d.observed or 0) / 60_000)} min, where their stays "
            f"there usually last about {round((d.expected or 0) / 60_000)} min"
        )
    if d.kind in ("arrival", "departure"):
        verb, usual = ("arrived", "arrive") if d.kind == "arrival" else ("left", "leave")
        return (
            f"{verb} at {_clock(d.observed)}, where they usually {usual} "
            f"about {_clock(d.expected)}"
        )
    if d.kind == "new_place":
        return "a place they had not been to before"
    if d.kind == "unusual_place":
        if d.usual_place is None:
            return "at these hours they are usually somewhere else"
        return f"at these hours they are usually at {d.usual_place}"
    return d.kind


def _rollup_row(stimulus: Stimulus, line: RollupLine, *, now_ms: int, tz: tzinfo) -> _Row:
    """A Rollup row as `<rollup kind="...">the sentence</rollup>`. It has no
    message id, so it is no `<m>`: it is not something a note line cites."""
    sentence = _rollup_sentence(line, now_ms=now_ms, tz=tz)

    def show(limit: int | None) -> str:
        return render_elements(
            [line], tag="rollup", attrs=lambda r, _i: (("kind", r.kind),),
            body=lambda _r: one_line(sentence) if limit is None else _words(sentence, limit),
        )

    return _Row(stimulus.at_ms, stimulus.id, show)


def _social_row(stimulus: Stimulus, line: SocialLine, *, now_ms: int, tz: tzinfo) -> _Row:
    """A social read as `<social at="...">Ada posted a story frame</social>`:
    whose, what (story frame, post or reel, collab) and when the newest of it
    was posted. It has no message id, so it is no `<m>`."""
    sentence = f"{line.name} posted {describe_read(line.item)}"

    def show(limit: int | None) -> str:
        return render_elements(
            [line], tag="social", attrs=lambda r, _i: (("at", _when(r.at_ms, now_ms, tz)),),
            body=lambda _r: one_line(sentence) if limit is None else _words(sentence, limit),
        )

    return _Row(stimulus.at_ms, stimulus.id, show)


def _item_rows(
    item: AgendaItem, rows: Sequence[Stimulus], grounding: Grounding,
    own: Sequence[RecordItem], *, now_ms: int, tz: tzinfo, message_chars: int,
) -> tuple[list[_Row], int]:
    """The item's readable rows and how many stimuli of it could not be read:
    its messages (Record rows), Nora's own sends in its window, its Rollup
    rows and its social reads."""
    out: list[_Row] = []
    unreadable = 0
    for s in rows:
        if s.kind == "social":
            social = grounding.socials.get(s.ref)
            if social is None:
                unreadable += 1
                continue
            out.append(_social_row(s, social, now_ms=now_ms, tz=tz))
            continue
        if s.kind == "rollup":
            roll = grounding.rollups.get(s.ref)
            if roll is None:
                unreadable += 1
                continue
            out.append(_rollup_row(s, roll, now_ms=now_ms, tz=tz))
            continue
        ref = record_ref(s)
        if ref is None:
            continue
        rec = grounding.records.get(s.ref)
        if rec is None or rec.room != item.room:
            unreadable += 1
            continue
        out.append(_message_row(
            rec, s.cues, now_ms=now_ms, tz=tz, message_chars=message_chars,
        ))
    for rec in own:
        if rec.room == item.room:
            out.append(_own_row(rec, now_ms=now_ms, tz=tz, message_chars=message_chars))
    return out, unreadable


def _attrs(pairs: Sequence[tuple[str, str | None]]) -> tuple[tuple[str, str], ...]:
    """A block's attributes, the empty ones dropped: an attribute that says
    nothing is no attribute, as in the shared element renderers."""
    return tuple((k, v) for k, v in pairs if v)


def _item_attrs(
    index: int, item: AgendaItem, labels: Mapping[str, str],
) -> list[tuple[str, str | None]]:
    return [
        ("index", str(index)), ("kind", item.kind),
        ("room", room_label(item.room, labels)), ("summary", item.summary),
        ("returning", returning(item.strike)),
    ]


def _item_block(
    index: int, item: AgendaItem, stimuli: Mapping[int, Stimulus], grounding: Grounding,
    *, now_ms: int, tz: tzinfo, allowance_of: Mapping[int, int], message_chars: int,
) -> str:
    """One `<item>`: its attributes, and the rows the budget chose."""
    rows = [stimuli[sid] for sid in item.stimulus_ids if sid in stimuli]
    readable, unreadable = _item_rows(
        item, rows, grounding, grounding.own_sends.get(index, ()),
        now_ms=now_ms, tz=tz, message_chars=message_chars,
    )
    readable.sort(key=lambda r: (r.at_ms, r.id))
    kept, cut = _fit(readable, allowance_of.get(index, 0))
    attrs = _item_attrs(index, item, grounding.room_labels)
    attrs.append(("window", _window([s.at_ms for s in rows], now_ms, tz)))
    if cut:
        dropped = sorted(r.id for r in readable[:cut])
        attrs += [
            ("earlier_not_shown", str(cut)),
            ("earlier_range", f"{dropped[0]} to {dropped[-1]}"),
        ]
    if unreadable:
        attrs.append(("unreadable", str(unreadable)))
    return render_group("item", attrs, "\n".join(kept))


def _needs(
    items: Sequence[AgendaItem], stimuli: Mapping[int, Stimulus], grounding: Grounding,
    *, now_ms: int, tz: tzinfo, message_chars: int,
) -> list[int]:
    needs = []
    for index, item in enumerate(items):
        rows = [stimuli[sid] for sid in item.stimulus_ids if sid in stimuli]
        readable, _ = _item_rows(
            item, rows, grounding, grounding.own_sends.get(index, ()),
            now_ms=now_ms, tz=tz, message_chars=message_chars,
        )
        needs.append(sum(len(r.show(None)) + 1 for r in readable))
    return needs


#: A note's one line about it, cut to the length a `Note` already holds it to
#: (`nora.domain.notes.DESCRIPTION_MAX_CHARS`), so an index row is one line.
_NOTE_LINE_CHARS = 200


def _register(
    rooms: Sequence[Room], grounding: Grounding, *, now_ms: int, tz: tzinfo,
    budget: RenderBudget,
) -> list[ContextBlock]:
    """The notes, the memos, and the events of each agenda room, already
    recorded: a `note_index` block, a `memos` block (DEC-306) and ONE `events`
    block of `<room_events>` groups. At most
    `register_lines` rows of each list, and every row together within
    `register_chars` as rendered: a row that does not fit, and every row after
    it, is left out, and the block or group says how many (`not_listed`) rather
    than cutting a row."""
    spent = 0
    exhausted = False

    def listed(rows: list[str]) -> list[str]:
        nonlocal spent, exhausted
        kept: list[str] = []
        for row in rows:
            cost = len(row) + 1
            if exhausted or spent + cost > budget.register_chars:
                exhausted = True
                break
            kept.append(row)
            spent += cost
        return kept

    blocks: list[ContextBlock] = []
    if grounding.notes:
        notes = [
            replace(n, description=clip(n.description, _NOTE_LINE_CHARS))
            for n in grounding.notes
        ]
        rows = listed([
            render_note_rows([n], ids=True, extra=lambda n: (
                ("review", n.review), ("may_duplicate", n.may_duplicate),
                ("reminds", n.reminds),
            ))
            for n in notes[: budget.register_lines]
        ])
        left_out = len(notes) - len(rows)
        blocks.append(restamp(ContextBlock(
            name="note_index", body="\n".join(rows),
            attrs=_attrs([("not_listed", str(left_out) if left_out else None)]),
            when_empty="self_close",
        ), _ROW))
    groups: list[str] = []
    for room in rooms:
        events = [e for e in grounding.events if e.room == room]
        if not events:
            continue
        rows = listed([
            render_event_rows([e], tz=tz) for e in events[: budget.register_lines]
        ])
        left_out = len(events) - len(rows)
        groups.append(render_group(
            "room_events",
            [("room", room_label(room, grounding.room_labels)),
             ("not_listed", str(left_out) if left_out else None)],
            "\n".join(rows),
        ))
    if groups:
        blocks.append(restamp(ContextBlock(name="events", body="\n".join(groups)), _ROW))
    memo_lines, memos_left = memo_rows(
        grounding.memos, tz=tz, lines=budget.memo_lines, chars=budget.memo_chars,
    )
    if memo_lines or memos_left:
        blocks.append(restamp(ContextBlock(
            name="memos", body="\n".join(memo_lines),
            attrs=_attrs([("not_listed", str(memos_left) if memos_left else None)]),
            when_empty="self_close",
        ), _ROW))
    return blocks


def _item_blocks(
    items: Sequence[AgendaItem], stimuli: Mapping[int, Stimulus], grounding: Grounding,
    *, now_ms: int | None, tz: tzinfo, budget: RenderBudget,
) -> list[ContextBlock]:
    """The `agenda` block (one `<item>` per item), then the Register. Without a
    clock an item is its summary alone (the probe's hand-written item), and
    nothing is recorded."""
    if now_ms is None:
        elements = [
            render_group("item", _item_attrs(i, item, grounding.room_labels), "")
            for i, item in enumerate(items)
        ]
        return [restamp(ContextBlock(name="agenda", body="\n".join(elements)), _ROW)]
    needs = _needs(
        items, stimuli, grounding, now_ms=now_ms, tz=tz, message_chars=budget.message_chars,
    )
    allowance = dict(enumerate(_shares(needs, budget.record_chars, budget.min_item_chars)))
    elements = [
        _item_block(
            i, item, stimuli, grounding, now_ms=now_ms, tz=tz, allowance_of=allowance,
            message_chars=budget.message_chars,
        )
        for i, item in enumerate(items)
    ]
    rooms: list[Room] = []
    for item in items:
        if item.room not in rooms:
            rooms.append(item.room)
    return [
        restamp(ContextBlock(name="agenda", body="\n".join(elements)), _ROW),
        *_register(rooms, grounding, now_ms=now_ms, tz=tz, budget=budget),
    ]


def render_agenda(
    items: Sequence[AgendaItem], *, stimuli: Mapping[int, Stimulus] | None = None,
    grounding: Grounding | None = None, reads_note: str = "", now_ms: int | None = None,
    last_session_ms: int | None = None, tz: tzinfo = UTC,
    budget: RenderBudget | None = None, graph: str = "",
    shared: SharedView | None = None,
) -> str:
    """The session's prompt. `stimuli` is the claimed stimuli by id (an item
    names its own by id), `grounding` what code read for them.

    Without `now_ms` (a probe's hand-written item) there is no clock and no
    age; without `grounding` an item is its summary alone, which is exactly
    what the eval probe hands the model and nothing else does."""
    blocks: list[ContextBlock] = []
    now = None if now_ms is None else datetime.fromtimestamp(now_ms / 1000, tz=tz)
    # THE SWITCH (45 §5): her Profile account, when the grounder read one,
    # replaces the prose `<self>` at the same row. `profile_names` was
    # resolved by the grounder, which holds `Persons`; this renderer does not.
    if grounding is not None and grounding.self_profile is not None and now is not None:
        profile_block = restamp(
            render_profile(
                grounding.self_profile,
                name_of=lambda pid: grounding.profile_names.get(pid, "someone"),
                now=now,
            ),
            _ROW,
        )
        if profile_block is not None:
            blocks.append(profile_block)
    else:
        self_block = restamp(
            render_self(
                grounding.self_account if grounding else None,
                now=now,
            ),
            _ROW,
        )
        if self_block is not None:
            blocks.append(self_block)
    if now_ms is not None:
        blocks.append(restamp(ContextBlock(
            name="clock", body=escape(clock_line(now_ms, last_session_ms, tz)),
        ), _ROW))
    if reads_note:
        blocks.append(restamp(ContextBlock(name="reads", body=escape(reads_note)), _ROW))
    item_blocks = _item_blocks(
        items, stimuli or {}, grounding or Grounding(), now_ms=now_ms, tz=tz,
        budget=budget or RenderBudget(),
    )
    if shared is not None:
        # `note_index` retires when the shared view renders: its rows and
        # the shared block's Open section are the same facts shown twice.
        item_blocks = [b for b in item_blocks if b.name != "note_index"]
    blocks += item_blocks
    if shared is not None and now_ms is not None:
        shared_block = restamp(
            render_shared_state(shared, now_ms=now_ms, tz=tz), _ROW)
        if shared_block is not None:
            blocks.append(shared_block)
    if graph:
        blocks.append(restamp(ContextBlock(name="graph", body=escape(graph)), _ROW))
    return assemble(blocks, sep=_SEP)


__all__ = [
    "RenderBudget", "clock_line", "render_agenda", "returning", "room_key", "room_label",
    "speaker_of",
]
