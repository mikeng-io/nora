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
from typing import Any
from xml.etree import ElementTree
from xml.parsers import expat

from nora.domain.blocks import (
    BlockDeclaration,
    ContextBlock,
    assemble_declared,
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
from nora.domain.composed import compose_body
from nora.domain.context_data import TranscriptLine
from nora.domain.external_files import render_file_links
from nora.domain.models import REACTION_LABEL_MAX, emoji_display
from nora.domain.observations import SourceEvent
from nora.domain.prompt import ago, clip, escape, legal, one_line
from nora.domain.shared_state import SharedView
from nora.domain.social_stream import describe_read
from nora.domain.stimuli import Stimulus
from nora.language.context_render import (
    render_clock,
    render_members,
    render_reads,
    render_rooms,
    render_transcript,
    transcript_rows,
)
from nora.language.lesson_render import render_lessons
from nora.language.profile_render import render_profile, render_profile_people
from nora.language.register_render import render_register
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
_BLOCKS: tuple[BlockDeclaration, ...] = (
    BlockDeclaration(
        "profile", 5, "match an existing entity or create one; correct a contradiction"
    ),
    BlockDeclaration("clock", 10, "when, and how long ago"),
    BlockDeclaration("reads", 20, "which rooms the read tools look through"),
    BlockDeclaration("rooms", 21, "which rooms exist, and where a read or a reminder can go"),
    BlockDeclaration("members", 22, "who a row is from, who a statement is about"),
    BlockDeclaration(
        "transcript",
        25,
        "what the rows mean in their conversation, and whether something said "
        "in one room links to another",
    ),
    BlockDeclaration("agenda", 30, "what an item states"),
    BlockDeclaration("notes", 40, "write a note, or revise the one that exists"),
    BlockDeclaration("standing", 45, "what she owes, holds open, already did"),
    BlockDeclaration("events", 50, "propose or revise an event"),
    BlockDeclaration("memos", 55, "write a memo, or not"),
    BlockDeclaration(
        "graph", 60, "match an existing entity or create one; correct a contradiction"
    ),
    BlockDeclaration("lessons", 65, "how she should write, from past corrections"),
    BlockDeclaration(
        "documents",
        70,
        "detect documentation superseded by agenda evidence; "
        "read its current revision and revise it",
    ),
    BlockDeclaration("tool_catalogue", 80, "which tool to reach for"),
)

#: `name -> (order, key)`, derived so no call site restates a row `_BLOCKS`
#: already gives it.
_ROW = {d.name: (d.order, d.key) for d in _BLOCKS}

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
    rec: RecordItem, cues: Sequence[str], *, now_ms: int, tz: tzinfo,
    message_chars: int, target: bool = False, source_key: Sequence[str] | None = None,
) -> _Row:
    """A person's message, an edit of one, a reaction to one or a deletion of
    one, as `<m n="<message id>" at from ...>words</m>`. `n` is the message's
    own id (`render_row_window` writes it first); a reaction's is the message
    reacted to, and its `from` and words are that message's. `target="yes"`
    marks a row shown only because a source event affected it — context for
    the event, never a fresh utterance."""
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
                ("source", ":".join(source_key) if source_key else None),
                ("person", r.person_id),
                ("target", "yes" if target else None),
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
        if spent + cost > allowance:
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


def _observation_row(
    stimulus: Stimulus, event: SourceEvent, *, now_ms: int, tz: tzinfo,
) -> _Row:
    """A typed source event as `<observation kind op at>` — the ONE
    non-message family. It has no message id and NO speaker: it is something
    the source did to an object, never a line somebody said. `at` is the
    source's own time only when `time_quality` says so; `bridge_local` and
    `unknown` say which clock it is not."""
    # `at` is the source's own time only when `time_quality` says so and a
    # time exists — at_ms<=0 is "no source time", never 1970-01-01, and an
    # unknown clock shows `time="unknown"` plus `received`, never a
    # dressed-up `at`; a bridge-local clock says WHICH clock it is.
    when = (_when(event.at_ms, now_ms, tz)
            if event.at_ms > 0 and event.time_quality != "unknown" else "")

    def show(limit: int | None) -> str:
        who = event.actor or (
            "anonymous" if event.actor_status == "anonymous" else None)
        return render_elements(
            [event], tag="observation",
            attrs=lambda e, _i: (
                ("kind", e.kind), ("op", e.operation),
                ("at", when or None),
                ("actor", who),
                ("time", e.time_quality if e.time_quality != "upstream"
                        else None),
                ("received", _when(e.received_ms, now_ms, tz)
                        if e.received_ms and e.time_quality != "upstream"
                        else None),
                ("rev", e.revision or None),
                ("coverage", e.coverage or None),
                ("object", e.handle),
            ),
            body=lambda _e: (
                one_line(event.summary) if limit is None
                else _words(event.summary, limit)),
        )

    return _Row(stimulus.at_ms, stimulus.id, show)


def _item_rows(
    item: AgendaItem, rows: Sequence[Stimulus], grounding: Grounding,
    own: Sequence[RecordItem], *, now_ms: int, tz: tzinfo, message_chars: int,
) -> tuple[list[_Row], int]:
    """The item's readable rows and how many stimuli of it could not be read:
    its messages (Record rows), Nora's own sends in its window, its Rollup
    rows, its social reads and its typed source events."""
    out: list[_Row] = []
    unreadable = 0
    message_ids: set[int] = set()
    linked: list[tuple[Stimulus, SourceEvent]] = []
    for s in rows:
        if s.kind == "social":
            social = grounding.socials.get(s.ref)
            if social is None:
                unreadable += 1
                continue
            out.append(_social_row(s, social, now_ms=now_ms, tz=tz))
            continue
        if s.kind == "observation":
            event = grounding.observations.get(s.ref)
            if event is None:
                unreadable += 1
                continue
            out.append(_observation_row(s, event, now_ms=now_ms, tz=tz))
            linked.append((s, event))
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
        message_ids.add(rec.message_id)
        out.append(_message_row(
            rec, s.cues, now_ms=now_ms, tz=tz, message_chars=message_chars,
        ))
    for s, event in linked:
        # The messages the event is about — for a merged source obligation
        # the utterance it was co-delivered as (`delivered`), and the
        # recorded row the event affected (`target`): a reaction's target,
        # a pinned message, an edited row. Both read beside the event so
        # each evidence path of the one source reaches the session. A row
        # already shown (its own stimulus, or another event's) is not
        # repeated; an absent one is absent, never a guess. The AFFECTED
        # row is marked `target` so it reads as the event's context — not
        # as a fresh utterance, and never the event actor's words.
        for message_id in (
            event.delivered_message_id, event.target_message_id,
        ):
            if message_id is None:
                continue
            rec = grounding.records.get(f"messages:{message_id}")
            if (rec is None or rec.room != item.room
                    or rec.message_id in message_ids):
                continue
            message_ids.add(rec.message_id)
            out.append(_message_row(
                rec, s.cues, now_ms=now_ms, tz=tz,
                message_chars=message_chars,
                target=(message_id == event.target_message_id
                        and message_id != event.delivered_message_id),
            ))
    for rec in own:
        if rec.room == item.room:
            out.append(_own_row(rec, now_ms=now_ms, tz=tz, message_chars=message_chars))
    return out, unreadable


@dataclass(frozen=True)
class RenderedAgendaRow:
    attributes: dict[str, str]
    text: str
    rendered: str


@dataclass(frozen=True)
class RenderedAgendaItem:
    attributes: dict[str, str]
    rendered: str
    rows: tuple[RenderedAgendaRow, ...]


def parse_rendered_agenda(agenda: str | Mapping[str, Any]) -> list[RenderedAgendaItem]:
    """Read emitted item structure, retaining exact bytes and only user-role request content."""
    if isinstance(agenda, Mapping):
        items = []
        for message in agenda.get("messages", []):
            if message.get("role") != "user":
                continue
            content = message.get("content", "")
            if not isinstance(content, str):
                content = "\n".join(p.get("text", "") for p in content or [])
            items.extend(parse_rendered_agenda(content))
        return items
    payload = ("<root>" + agenda + "</root>").encode("utf-8")
    parser = expat.ParserCreate()
    stack: list[dict[str, Any]] = []
    items = []

    def start(tag: str, attributes: dict[str, str]) -> None:
        if stack:
            stack[-1]["children"] += 1
        stack.append({"tag": tag, "attributes": attributes, "start": parser.CurrentByteIndex,
                      "text": [], "rows": [], "children": 0})

    def text(value: str) -> None:
        stack[-1]["text"].append(value)

    def end(tag: str) -> None:
        node = stack.pop()
        stop = parser.CurrentByteIndex
        self_closed = (not node["children"] and not node["text"]
                       and payload[stop - 2:stop] == b"/>")
        if not self_closed:
            stop = payload.index(b">", stop) + 1
        rendered = payload[node["start"]:stop].decode("utf-8")
        if tag == "item":
            items.append(RenderedAgendaItem(node["attributes"], rendered, tuple(node["rows"])))
        elif stack and stack[-1]["tag"] == "item" and "n" in node["attributes"]:
            stack[-1]["rows"].append(RenderedAgendaRow(
                node["attributes"], "".join(node["text"]), rendered))

    parser.StartElementHandler = start
    parser.CharacterDataHandler = text
    parser.EndElementHandler = end
    parser.Parse(payload, True)
    return items


def rendered_new_sources(agenda: str) -> dict[str, Any]:
    """Fresh citable rows from the emitted agenda, after source expansion and clipping."""
    new_sources = {}
    for item in parse_rendered_agenda(agenda):
        for row in item.rows:
            n = row.attributes.get("n")
            if n is None or row.attributes.get("own") or row.attributes.get("target"):
                continue
            source = row.attributes.get("source")
            # Context message rows carry source=; fresh messages do not.
            # Other fresh record families carry their typed source key.
            if source and source.split(":", 1)[0] != item.attributes.get("kind"):
                continue
            if source and source.startswith("message:"):
                continue
            key = source.split(":", 1) if source else ["message", n]
            new_sources[n] = {"key": key, "text": row.text}
    return new_sources


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


def _reading_rows(
    readable: list[_Row], stimuli: Sequence[Stimulus], item: AgendaItem,
    memory_item: Any, *, now_ms: int, tz: tzinfo, message_chars: int,
) -> list[_Row]:
    candidates = memory_item.snapshot["messages"] if memory_item is not None else []
    keys = memory_item.snapshot["source_keys"] if memory_item is not None else {}
    candidates = {r["id"]: r for r in candidates}
    present = {r.id for r in readable}
    if memory_item is not None:
        # A social record is a citable row here, rather than a second caption.
        social_ids = {s.id for s in stimuli if s.kind == "social"
                      and any(k[0] == "social" and s.ref == ":".join(k)
                              for k in keys.values())}
        readable = [r for r in readable if r.id not in social_ids]
        present = {r.id for r in readable}
    for rid, row in candidates.items():
        if rid in present:
            continue
        from nora.domain.media import media_detail
        text = row["body"]
        if keys[str(rid)][0] == "media":
            text = media_detail(label="image", title="", description="", content=text)
        rec = RecordItem(ref=str(keys[str(rid)]), kind="message", message_id=rid,
                         room=item.room, author=row.get("author") or "unknown",
                         person_id=row.get("person_id"), at_ms=row["ts"], text=text)
        readable.append(_message_row(rec, (), now_ms=now_ms, tz=tz,
                                     message_chars=message_chars, source_key=keys[str(rid)]))
    return readable


def _item_block(
    index: int, item: AgendaItem, stimuli: Mapping[int, Stimulus], grounding: Grounding,
    *, now_ms: int, tz: tzinfo, allowance_of: Mapping[int, int], message_chars: int,
    memory_item: Any = None,
) -> str:
    """One `<item>`: its attributes, and the rows the budget chose."""
    rows = [stimuli[sid] for sid in item.stimulus_ids if sid in stimuli]
    readable, unreadable = _item_rows(
        item, rows, grounding, grounding.own_sends.get(index, ()),
        now_ms=now_ms, tz=tz, message_chars=message_chars,
    )
    readable = _reading_rows(readable, rows, item, memory_item, now_ms=now_ms,
                             tz=tz, message_chars=message_chars)
    candidates = {} if memory_item is None else {
        r["id"]: r for r in memory_item.snapshot["messages"]}
    keys = {} if memory_item is None else memory_item.snapshot["source_keys"]
    readable.sort(key=lambda r: (r.at_ms, r.id))
    kept, cut = _fit(readable, allowance_of.get(index, 0))
    if memory_item is not None:
        shown = []
        shown_keys = {}
        for row, rendered in zip(readable[cut:], kept, strict=True):
            if row.id not in candidates:
                continue
            element = ElementTree.fromstring(rendered)  # noqa: S314 - our escaped row renderer's output
            shown.append({**candidates[row.id], "body": element.text or ""})
            shown_keys[str(row.id)] = keys[str(row.id)]
        memory_item.snapshot["messages"] = shown
        memory_item.snapshot["source_keys"] = shown_keys
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
    memory_items: Mapping[int, Any] | None = None,
) -> list[int]:
    needs = []
    for index, item in enumerate(items):
        rows = [stimuli[sid] for sid in item.stimulus_ids if sid in stimuli]
        readable, _ = _item_rows(
            item, rows, grounding, grounding.own_sends.get(index, ()),
            now_ms=now_ms, tz=tz, message_chars=message_chars,
        )
        readable = _reading_rows(readable, rows, item, (memory_items or {}).get(index),
                                 now_ms=now_ms, tz=tz, message_chars=message_chars)
        needs.append(sum(len(r.show(None)) + 1 for r in readable))
    return needs


#: A note's one line about it, cut to the length a `Note` already holds it to
#: (`nora.domain.notes.DESCRIPTION_MAX_CHARS`), so an index row is one line.
_NOTE_LINE_CHARS = 200


def _item_blocks(
    items: Sequence[AgendaItem],
    stimuli: Mapping[int, Stimulus],
    grounding: Grounding,
    *,
    now_ms: int | None,
    tz: tzinfo,
    budget: RenderBudget,
    memory_items: Mapping[int, Any] | None = None,
) -> list[ContextBlock]:
    """The `agenda` block (one `<item>` per item), then the Register. Without a
    clock an item is its summary alone (the probe's hand-written item), and
    nothing is recorded."""
    if now_ms is None:
        elements = [
            render_group("item", _item_attrs(i, item, grounding.room_labels), "")
            for i, item in enumerate(items)
        ]
        return [restamp(ContextBlock(name="agenda", body=compose_body("\n".join(elements))), _ROW)]
    needs = _needs(
        items,
        stimuli,
        grounding,
        now_ms=now_ms,
        tz=tz,
        message_chars=budget.message_chars,
        memory_items=memory_items,
    )
    room_rows: dict[str, list[Any]] = {}
    for row in grounding.transcript:
        room_rows.setdefault(
            f"{row.platform}:{row.conversation_id}" if row.platform else row.room, []
        ).append(row)
    now = datetime.fromtimestamp(now_ms / 1000, tz=tz)
    room_needs = [
        sum(
            len(transcript_rows([row], display_name="Nora", now=now, source_ids=True)) + 1
            for row in rows
        )
        for rows in room_rows.values()
    ]
    shares = _shares([*needs, *room_needs], budget.record_chars, budget.min_item_chars)
    allowance = dict(enumerate(shares[: len(needs)]))
    kept_transcript = []
    dropped = 0
    for (_room, rows), share in zip(room_rows.items(), shares[len(needs) :], strict=True):
        readable = []
        for line in rows:

            def show(size: int | None, line: TranscriptLine = line) -> str:
                return transcript_rows(
                    [
                        replace(
                            line,
                            row=replace(
                                line.row,
                                body=clip(
                                    line.row.body or "",
                                    size if size is not None else budget.message_chars,
                                ),
                            ),
                        )
                    ],
                    display_name="Nora",
                    now=now,
                    source_ids=True,
                )

            readable.append(_Row(line.row.ts, line.row.id, show))
        fitted, cut = _fit(readable, share)
        kept = [
            replace(
                line,
                row=replace(
                    line.row,
                    body=ElementTree.fromstring(text).text or "",  # noqa: S314 - our escaped renderer output
                ),
            )
            for line, text in zip(rows[cut:], fitted, strict=True)
        ]
        dropped += len(rows) - len(kept)
        kept_transcript.extend(kept)
    kept_transcript.sort(key=lambda x: (x.row.ts, x.room, x.row.id))
    transcript_allowance = sum(shares[len(needs) :])
    while (
        kept_transcript
        and len(transcript_rows(kept_transcript, display_name="Nora", now=now, source_ids=True))
        > transcript_allowance
    ):
        kept_transcript.pop(0)
        dropped += 1
    transcript_block = render_transcript(
        kept_transcript,
        display_name="Nora",
        now=now,
        not_listed=dropped,
        read_limited=grounding.transcript_more,
        withheld=dict(grounding.context_withheld).get("transcript", 0),
    )
    elements = [
        _item_block(
            i,
            item,
            stimuli,
            grounding,
            now_ms=now_ms,
            tz=tz,
            allowance_of=allowance,
            message_chars=budget.message_chars,
            memory_item=(memory_items or {}).get(i),
        )
        for i, item in enumerate(items)
    ]
    rooms: list[Room] = []
    for item in items:
        if item.room not in rooms:
            rooms.append(item.room)
    return [
        restamp(ContextBlock(name="agenda", body=compose_body("\n".join(elements))), _ROW),
        *render_register(rooms, grounding, now_ms=now_ms, tz=tz, budget=budget, placement=_ROW),
        restamp(transcript_block, _ROW),
    ]


def render_agenda(
    items: Sequence[AgendaItem],
    *,
    stimuli: Mapping[int, Stimulus] | None = None,
    grounding: Grounding | None = None,
    reads_note: str = "",
    now_ms: int | None = None,
    last_session_ms: int | None = None,
    tz: tzinfo = UTC,
    budget: RenderBudget | None = None,
    graph: str | None = None,
    memory_items: Mapping[int, Any] | None = None,
    shared: SharedView | None = None,
    tool_catalogue: str = "",
) -> str:
    """The session's prompt. `stimuli` is the claimed stimuli by id (an item
    names its own by id), `grounding` what code read for them.

    Without `now_ms` (a probe's hand-written item) there is no clock and no
    age; without `grounding` an item is its summary alone, which is exactly
    what the eval probe hands the model and nothing else does."""
    blocks: list[ContextBlock] = []
    now = None if now_ms is None else datetime.fromtimestamp(now_ms / 1000, tz=tz)
    failed = (
        set(grounding.unavailable)
        if grounding is not None
        else {
            d.name
            for d in _BLOCKS
            if d.name not in {"agenda", "clock", "reads", "graph", "tool_catalogue"}
        }
    )
    if now is not None:
        blocks.append(render_clock(now=now, last_session_ms=last_session_ms))
    else:
        failed.add("clock")
    if grounding is not None and now is not None:
        views = grounding.profiles or ((grounding.self_profile,) if grounding.self_profile else ())

        def names(pid: str) -> str:
            return grounding.profile_names.get(pid, pid)

        blocks.extend(
            render_profile_people(
                [v for v in views if v.entity == "person"], name_of=names, now=now
            )
        )
        for view in views:
            if view.entity != "person":
                block = render_profile(view, name_of=names, now=now)
                if block is not None:
                    blocks.append(block)
                else:
                    blocks.append(
                        ContextBlock(
                            name="profile",
                            body=compose_body(""),
                            attrs=(("of", view.of), ("entity", view.entity)),
                            when_empty="self_close",
                        )
                    )
        for entity, subject in grounding.profile_unavailable:
            blocks.append(
                ContextBlock(
                    name="profile",
                    body=compose_body(""),
                    attrs=(("of", subject), ("entity", entity), ("status", "unavailable")),
                    when_empty="self_close",
                )
            )
        blocks.extend(render_members(view) for view in grounding.members)
        blocks.append(
            render_rooms(
                grounding.rooms, now=now, withheld=dict(grounding.context_withheld).get("rooms", 0)
            )
        )
        from nora.language.documents_render import render_documents

        blocks.extend(
            b for view in grounding.documents if (b := render_documents(view)) is not None
        )
        lesson_block = render_lessons(grounding.lessons)
        if lesson_block is not None:
            blocks.append(lesson_block)
    blocks.append(
        render_reads(
            tuple(
                (
                    room_key(r),
                    (grounding.room_labels if grounding else {}).get(room_key(r), "unnamed room"),
                )
                for r in dict.fromkeys(item.room for item in items)
            )
        )
    )
    # Real database message ids are positive. Other record keys share one
    # negative namespace, allocated once here, never by individual loaders.
    source_ids: dict[tuple[str, ...], int] = {}
    for index, memory_item in (memory_items or {}).items():
        candidates = []
        keys = {}
        rows = (
            memory_item.snapshot.get("record_rows", [])
            if now_ms is not None and 0 <= index < len(items)
            else []
        )
        for row in rows:
            key = tuple(row["source_key"])
            if key[0] == "message":
                rid = int(key[1])
            else:
                rid = source_ids.setdefault(key, -(len(source_ids) + 1))
            candidates.append({**row, "id": rid})
            keys[str(rid)] = list(key)
        memory_item.snapshot["messages"] = candidates
        memory_item.snapshot["source_keys"] = keys
    item_blocks = _item_blocks(
        items,
        stimuli or {},
        grounding or Grounding(),
        now_ms=now_ms,
        tz=tz,
        budget=budget or RenderBudget(),
        memory_items=memory_items,
    )
    blocks += item_blocks
    if shared is not None and now_ms is not None:
        shared_block = restamp(render_shared_state(shared, now_ms=now_ms, tz=tz), _ROW)
        if shared_block is not None:
            source_blocks = (
                [b for b in blocks if b.name == "standing"]
                if shared.status != "unavailable"
                else []
            )
            shared_block = replace(
                shared_block,
                body=compose_body(
                    "\n".join(
                        [shared_block.body.text, *(b.body.text for b in source_blocks)],
                    )
                ),
            )
            blocks = [b for b in blocks if b.name != "standing"]
            blocks.append(shared_block)
    if graph is None:
        failed.add("graph")
    elif graph:
        blocks.append(restamp(ContextBlock(name="graph", body=compose_body(escape(graph))), _ROW))
    if tool_catalogue:
        from nora.language.catalogue_render import render_tool_catalogue

        blocks.append(render_tool_catalogue(tool_catalogue))
    return assemble_declared(_BLOCKS, blocks, unavailable=failed, sep=_SEP)


__all__ = [
    "RenderBudget", "RenderedAgendaItem", "RenderedAgendaRow", "clock_line",
    "parse_rendered_agenda", "render_agenda", "rendered_new_sources", "returning", "room_key",
    "room_label", "speaker_of",
]
