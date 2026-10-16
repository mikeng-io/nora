"""Renders standing `Note`s into the `<notes>` block — the INDEX, not the notes.

**Why this does not reuse the room block's note.** A room-language block would
say «distilled from this room's own messages», but a note is keyed on the UNIT.
`Note.established_in` records the room it was born in but is a routing key,
never a filter: it decides where a promoted `Event` lands, not who may see the
note, so a note established in one room renders unchanged in every other room of
the same unit. Reusing that sentence here would make it FALSE in the one place
the model is asked to trust a sentence. Everything else is its siblings'
convention: escaped, saying what it is from its own type
(`nora.domain.block_types`), and no block at all when nothing is standing.

**Why the index is UNCAPPED.** Every standing note contributes a line: no top-N,
no elision, no relevance scoring. A note missing from the index is invisible to
her, and invisible is indistinguishable from absent — the
`unavailable`-reported-as-`nothing_found` failure R-27 exists to prevent, and a
relevance-scored subset has the same defect with extra machinery on top. The
prompt-budget answer is paid in two other places: `TITLE_MAX_CHARS` /
`DESCRIPTION_MAX_CHARS` in `nora.domain.notes`, enforced in the CONSTRUCTOR so
an over-long entry is unrepresentable rather than silently truncated here; and
retirement (`note_retire`), which makes the index track live uncertainty rather
than history. `test_the_index_is_uncapped_and_every_standing_note_appears` is
the guard, because "just show the top ten" is a decision, not a tweak.

**Why the closing condition is NOT on the line**, though it is the field that
says what would settle the uncertainty:

1. The index carries title and description only, and the read-before-write
   latch is built ON that scarcity: the index tells her a note EXISTS,
   `note_read` tells her what it says. Widening the line erodes the latch's own
   justification.
2. `Note.closing_condition` carries **no length cap** — `__post_init__` requires
   it non-empty and stops there. Putting it here would mean either unbounded
   model-authored text per note per turn, or a truncation in this module, and
   that truncation is precisely the silent loss `nora.domain.notes` moved into
   the constructor to be rid of. If this line should ever carry it, the cap goes
   on the domain type first.

A retired note never renders, so an entry's mere presence already says the
question is still open; the closing condition would say what would CLOSE it, a
revise-time question, and a `note_read` is required before any revision anyway.

**This tier is LIVE.** `TagContext.notes` defaults to `()` — an unwired caller
renders nothing rather than raising — but `nora.engagement.context` populates it
from the unit's standing notes, and the Cognition session writes them, so this
index is on the prompt in production.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Protocol, TypeVar

from ..domain.blocks import ContextBlock, render_elements
from ..domain.notes import Note, NoteRevision
from ..domain.prompt import age, escape

#
# Between them sits what the line ALSO has to say — WHOSE WORDS THESE ARE. The
# pointer clause is a read-before-WRITE latch and nothing more: it stops her
# revising a body she has not read, and says nothing about speaking. A
# description written for triage (her own bookkeeping, model-authored, hedged)
# read aloud to the room it is about would be flat and without the hedge, while
# the transcript that contradicts it sits in the same prompt. The sentence makes
# three things unmissable:
#
#   1. these are HER notes, in HER words, written by her for herself — not
#      something the room said and not a quotable record of what happened;
#   2. a claim about what the room actually DID is grounded in the transcript in
#      front of her, never in a line of this index — and, said outright because
#      the summary is the thing that reads as settled, the transcript WINS when
#      the two disagree. Without that clause the stale summary is stated as
#      current (the act suite's `a10` is its regression guard: a rare
#      stochastic failure, so the case guards rather than proves);
#   3. the notes are machinery, so the MACHINERY does not get named to the room
#      either (asked what she had changed, she must not answer by explaining
#      that a 「note」 is still open).
#
# **Point 3 is scoped to the apparatus, and the scope is load-bearing.** It is
# about the TOOL LAYER's word — 「open note」 — not the subjects. Unqualified, the
# clause would forbid an honest answer to a direct question about what she is
# tracking, which `nora/doctrine/rooms/conduct.md` tells her to give («查完
# memory、notes 都冇嘢» → say so outright), and would collide with the skill
# that tells her she holds «what a room has left unsettled and what would settle
# it»: the model would be told to describe and to never mention the same object
# on the same turn. So the ban names the apparatus — this list, the tools, the
# word — and the sentence after it says outright that the subject and what she
# is still holding open are hers to say.
#
# **And a PERMISSION, which is not padding.** Prohibitions alone (never read one
# out, never mention them) say nothing about raising the subject, which is the
# entire purpose of the tier. The clause «raise what one of them is about
# whenever it would help» keeps the three prohibitions from being read as «leave
# the notes alone». It is not free to drop: the act suite's `a01` measures
# whether she still speaks, and stacked prohibitions across this block, the fire
# header and the tool schemas suppress it.
#
# Deliberately NOT a further clause about the closing condition or the note body:
# the line's contents are fixed at title + description, for the reasons the
# module docstring argues at length.


class IndexedNote(Protocol):
    """What an index row needs of a note: the three fields every index shows.
    `Note` is one; so is the Cognition session's `NoteLine`, which carries these
    three and nothing else (index lines, never bodies)."""

    @property
    def id(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def description(self) -> str: ...


N = TypeVar("N", bound=IndexedNote)


def render_note_rows(
    notes: Sequence[N],
    *,
    today: int | None = None,
    extra: Callable[[N], Sequence[tuple[str, str | None]]] = lambda _: (),
    ids: bool = False,
) -> str:
    """The index's ROWS — one element per note, and nothing else.

    **Rows are elements, not flat lines.** A flat `- {title} — {description}`
    line is forgeable: ` — ` is a field separator a model-authored 60-character
    title may itself contain, and escaping without collapsing lets a description
    holding a newline render as TWO index entries. `render_elements` closes both
    by construction: `escape_attr` closes the attribute quote, which content
    cannot write, and the description becomes CONTENT, where a newline is just a
    newline — the same move, and the same argument, as `<m>` (R-13c). The
    title's boundary matters beyond legibility: `domain.notes.note_handle` and
    `ReplyGuard`'s R-55 latch key on the same string, so an unclear boundary is a
    mis-resolution.

    **NO FILTER AND NO ENVELOPE.** The caller filters `is_standing` once and
    hands the result here, so the numbering has a single source: `proactive`
    needs the same sequence to build the `n -> note_id` map that `remind` and
    `note_wake` resolve a write against, and two derivations of one number agree
    only by accident. The envelope (name, header, description) belongs to
    whoever serves the block.

    **The row's contents are title + description + the day**, and deliberately
    NOT the closing condition (the module docstring says why).

    `extra` is the one knob that is genuinely about a note rather than about the
    block: additional attributes per note. Additive, so a caller that passes none
    renders byte-identically to the tag path.

    `n` is POSITIONAL by default: a closed 1..N set, so a reference to a note
    outside the index is refusable rather than a lookup that might hit another
    row, and the tag turn remaps it (`resolve_positions`).

    **`ids=True` is the one opt-in, and it is for a pass whose model writes a
    verb that takes the note's own id** (the Cognition session's `note_update`):
    `n` is then the note's real `id`, so what the model reads is what the verb
    requires. It is a flag and not a second renderer because the row is the same
    row; the default keeps the tag path's bytes (pinned by
    `tests/unit/test_tag_notes_block_is_unchanged_by_step3b.py`). A row's `about`
    needs the note's anchor day, which only a `Note` carries, so an index of
    bare lines (`NoteLine`) renders no `about`.
    """
    def _attrs(n: N, i: int) -> tuple[tuple[str, str | None], ...]:
        anchor = n.anchor_date if isinstance(n, Note) else None
        return (("n", n.id if ids else str(i)), ("title", n.title),
                ("about", _about(anchor, today)), *extra(n))

    return render_elements(
        notes, tag="note", attrs=_attrs, body=lambda n: n.description,
    )


def standing_only(notes: Sequence[Note]) -> tuple[Note, ...]:
    """The notes an index may show, in order — ONE definition of it.

    A retired note is never rendered: it would sit in the index as a live
    uncertainty, and beside its own replacement when there is one. This is a
    function rather than a comprehension repeated at each call site because a
    caller building an `n -> note_id` map must use exactly this sequence and
    a second spelling of the predicate is a second answer waiting to happen.
    """
    return tuple(n for n in notes if n.is_standing)


def render_notes(
    notes: Sequence[Note], *, today: int | None = None,
) -> ContextBlock | None:
    """The tag path's `<notes>` block, or `None` when nothing is standing —
    either no notes at all, or none surviving `standing_only`."""
    standing = standing_only(notes)
    if not standing:
        return None
    return ContextBlock(
        name="notes", body=render_note_rows(standing, today=today),
    )


def today_block(when: date | str) -> ContextBlock:
    """The one-line `<today>` block: a DAY (`%Y-%m-%d %a`), no time and no zone.

    One definition of the format for every caller. The caller passes the day and
    stamps its own `order` and `key` with `restamp` — where a block sits is the
    prompt's, not the block's. A caller declaring no table takes the defaults.

    `when` is a `date` for every real day; a `str` is shown as written, for a
    stored `YYYYMMDD` that is not a date (month 13) — a day it cannot name is
    still printed, because the model reads every `anchor` against this line.

    **A CLOCK THIS PROCESS OWNS, not anything the room said**, and escaped
    anyway: it is a block body, and `assemble`'s contract is that a body arrives
    escaped.

    `<today>`, NOT `<now>`: a `<now>` block is a MOMENT with a time and a zone,
    a different block with a different description.
    """
    text = f"{when:%Y-%m-%d %a}" if isinstance(when, date) else when
    return ContextBlock(name="today", body=escape(text))


def _about(anchor_date: int | None, today: int | None) -> str | None:
    """The day the note is about, as a distance when there is a clock to read it
    against. `None` — no anchor, or no clock — DROPS the attribute rather than
    rendering an empty one, which `render_elements` does for `_when`'s reason."""
    if anchor_date is None:
        return None
    if today is None:
        return str(anchor_date)
    return _days_from(today, anchor_date)


def _as_date(yyyymmdd: int) -> date | None:
    try:
        return date(yyyymmdd // 10000, (yyyymmdd // 100) % 100, yyyymmdd % 100)
    except ValueError:
        return None


def _days_from(today: int, anchor: int) -> str:
    a, t = _as_date(anchor), _as_date(today)
    if a is None or t is None:
        return str(anchor)
    delta = (a - t).days
    stamp = f"{a:%a %-d %b}"
    return (
        f"{stamp}, today" if delta == 0
        else f"{stamp}, tomorrow" if delta == 1
        else f"{stamp}, yesterday" if delta == -1
        else f"{stamp}, in {delta} days" if delta > 0
        else f"{stamp}, {-delta} days ago"
    )


# ---------------------------------------------------------------------------
# `<open_note>`: one open note WITH its body, for the passes that read bodies
# ---------------------------------------------------------------------------


def anchor_attrs(anchor_date: int | None, today: date) -> tuple[tuple[str, str], ...]:
    """`anchor` and `anchor_is` for an open note: the day, and that day read
    against `today`. A value that is not a real day renders as written."""
    if anchor_date is None:
        return ()
    day = _as_date(anchor_date)
    if day is None:
        return (("anchor", str(anchor_date)),)
    delta = (day - today).days
    reading = (
        "today" if delta == 0
        else "tomorrow" if delta == 1
        else "yesterday" if delta == -1
        else f"in {delta} days" if delta > 0
        else f"{-delta} days ago"
    )
    return (("anchor", f"{day:%Y-%m-%d %a}"), ("anchor_is", reading))


#: `form` on an `<open_note>` whose body is not in the notebook's sections:
#: the input says what it is showing, so a legacy body is labelled as legacy
#: rather than offered as the format.
FORM_LEGACY = (
    "not in the notebook's sections: written before they existed, or added to "
    "without them"
)
#: `form` on an `<open_note>` rendered without its body, because the pass's
#: budget bound or its head could not be read.
FORM_NOT_SHOWN = "body not shown in this pass"


def open_note_parts(
    note: Note, head: NoteRevision | None, *, room: str, now: datetime, today: date,
    handle: str | None = None, changed: str | None = None,
    excerpt: int | None = None, form: str | None = None, body: bool = True,
) -> tuple[tuple[tuple[str, str], ...], str]:
    """One open note's attributes and body: what it is about, what is
    undecided, what would settle it, and its body.

    ONE line renderer for any pass that reads a note's body; each caller builds
    its OWN block type from it, because a type's `fields` render on every block
    of it. `handle` (the `id` attribute), `changed` and `excerpt` are optional;
    with no `excerpt` the body is rendered WHOLE. `form` says a body
    is not in the notebook's sections, or was left out of this pass for size
    (`body=False`). Every value is escaped (R-13): titles and bodies are
    model-authored from what the group typed.
    """
    attrs: tuple[tuple[str, str], ...] = (
        *((("id", handle),) if handle is not None else ()),
        ("title", note.title),
        ("room", room),
        ("written", age(note.created_at, now)),
        *((("revised", age(head.written_at, now)),) if head is not None else ()),
        *anchor_attrs(note.anchor_date, today),
    )
    if changed is not None:
        attrs += (("changed", changed),)
    if form is not None:
        attrs += (("form", form),)
    text = head.body.strip() if head is not None else ""
    if not body or head is None:
        shown_body = ""
    elif excerpt is not None and len(text) > excerpt:
        shown_body = (
            f"\nbody (its first {excerpt} characters; it runs longer):\n"
            f"{escape(text[:excerpt])}"
        )
    else:
        shown_body = f"\nbody:\n{escape(text)}"
    return attrs, (
        f"about: {escape(note.description)}\n"
        f"undecided: {escape(note.uncertainty)}\n"
        f"settled when: {escape(note.closing_condition)}"
        f"{shown_body}"
    )
