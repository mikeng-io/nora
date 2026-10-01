"""Renders standing `Note`s into the `<notes>` block — the INDEX, not the notes.

**Why this does not reuse the room block's note.** The room-language block
said «distilled from this room's own messages», and a note is keyed on the
UNIT. (That constant lived in `nora.language.render`, deleted with the
language tier on 2026-09-07 — the argument survives it, and this is now the
only block making it.) `Note.established_in`
records the room it was born in but is a routing key, never a filter (spec §3) —
it decides where a promoted `Event` lands, not who may see the note — so a note
established in one room renders unchanged in every other room of the same
unit. Reusing that sentence here would make it FALSE in the one place the
model is being asked to trust a sentence. Everything else is its siblings'
convention: escaped, saying what it is from its own type
(`nora.domain.block_types`), and no block at all when nothing is standing.

**Why the index is UNCAPPED, and why that is not an oversight to fix later**
(spec §5.1). Every standing note contributes a line: no top-N, no elision, no
relevance scoring. A note missing from the index is invisible to her, and
invisible is indistinguishable from absent — the
`unavailable`-reported-as-`nothing_found` failure R-27 exists to prevent, and a
relevance-scored subset has the same defect with extra machinery on top. The
prompt-budget answer is paid in two other places instead: `TITLE_MAX_CHARS` /
`DESCRIPTION_MAX_CHARS` in `nora.domain.notes`, enforced in the CONSTRUCTOR so
an over-long entry is unrepresentable rather than silently truncated here; and
the curator's retirement pass (§7.4/§9), which makes the index track live
uncertainty rather than history. `test_the_index_is_uncapped_and_every_standing_note_appears`
is the guard, because "just show the top ten" is the change somebody makes
without noticing it is a decision.

**Why the closing condition is NOT on the line**, though it is the field that
says what would settle the uncertainty and was argued for on exactly that
ground. Two reasons, and the second is the one that says what would have to
change first:

1. The spec fixes the line's contents twice and identically — §5.1 «title +
   description» and §6.2 «the index carries title and description only» — and
   §6.2 builds the read-before-write latch ON that scarcity: the index tells her
   a note EXISTS, `note_read` tells her what it says. Widening the line erodes
   the latch's own justification without amending it.
2. `Note.closing_condition` carries **no length cap** — `__post_init__` requires
   it non-empty and stops there, because it was never destined for a block that
   renders on every tag. Putting it here would mean either unbounded model-
   authored text per note per turn, or a truncation in this module, and that
   truncation is precisely the silent loss `nora.domain.notes` moved into the
   constructor to be rid of. If this line should ever carry it, the cap goes on
   the domain type first; then this is a two-line change with a test to match.

What she loses by that is smaller than it looks: a retired note never renders,
so an entry's mere presence already says the question is still open — the
closing condition would tell her what would CLOSE it, which is a revise-time
question, and §6.2 requires a `note_read` before any revision anyway.

**This tier is LIVE.** `TagContext.notes` still defaults to `()` — an
unwired caller renders nothing rather than raising — but
`nora.engagement.context` populates it from the unit's standing notes,
and the note-candidates pass (#98) writes them, so this index is on the
prompt in production. The paragraph here said the opposite until 2026-09-13,
which is the ordinary cost of a "not yet" written beside a switch somebody
else later flips.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime

from ..domain.blocks import ContextBlock, render_elements
from ..domain.notes import Note, NoteRevision
from ..domain.prompt import age, escape

#
# Between them sits what the line ALSO has to say, and what it did not say until
# a message went out on it (issue #226) — WHOSE WORDS THESE ARE. The pointer
# clause was a read-before-WRITE latch and nothing more: it stopped her revising
# a body she had not read, and said nothing about speaking. So a description the
# curator wrote for triage — her own bookkeeping, model-authored, capped at 200
# characters, hedged correctly — was read aloud to the room it was about, flat
# and without the hedge, while the transcript that contradicted it sat in the
# same prompt. The three things the sentence now makes unmissable:
#
#   1. these are HER notes, in HER words, written by her for herself — not
#      something the room said and not a quotable record of what happened;
#   2. a claim about what the room actually DID is grounded in the transcript in
#      front of her, never in a line of this index — and, said outright because
#      the summary is the thing that reads as settled, the transcript WINS when
#      the two disagree. That clause is the one with a live before/after: on the
#      act suite's `a10`, whose note asserts a venue the transcript two lines up
#      moved, a version without it produced «而家淨係知<the note's venue>» — the
#      stale summary stated as current, which is the defect — in 1 of 3 spoken
#      samples, and with it 5 of 5 spoken samples named the room's venue. The
#      unchanged prompt also scored 6 of 6 on one window, so this is a rare
#      stochastic failure and the case is a REGRESSION GUARD, not a proof;
#   3. the notes are machinery, so the MACHINERY does not get named to the room
#      either (the second half of the same incident: asked what she had
#      changed, she answered by explaining that a 「note」 was still open).
#
# **Point 3 is scoped to the apparatus, and the scope is load-bearing.** An
# earlier wording said «never mention the notes themselves», which is wider
# than the defect and wider than anything measured. #226(b) was her using the
# TOOL LAYER's word — 「open note」, which `nora.agent.tools` taught her and no
# longer does; it was never about the subjects. Left unqualified the clause
# forbids an honest answer to a direct question about what she is tracking,
# which `nora/doctrine/rooms/conduct.md` already tells her to give («查完 memory、
# notes 都冇嘢» → say so outright), and it collides head-on with the skill in
# flight that tells her she holds «what a room has left unsettled and what
# would settle it»: both merge cleanly and the model is then told to describe
# and to never mention the same object on the same turn. So the ban names the
# apparatus — this list, the tools, the word — and the sentence after it says
# outright that the subject and what she is still holding open are hers to say.
#
# **And a PERMISSION, which is not padding.** A first version said only what she
# may not do with a note — never read one out, never mention them — and nothing
# about raising the subject, which is the entire purpose of the tier. The clause
# «raise what one of them is about whenever it would help» is there so the three
# prohibitions cannot be read as «leave the notes alone». It is not free to
# drop: the act suite's `a01` is the case that measures whether she still
# speaks, and a version of this work that stacked prohibitions across this block,
# the fire header and the tool schemas read 2-3 of 15 there. (What that does NOT
# establish is which clause did it — the same baseline prompt read 5/15, 8/15,
# 10/15 and 12/15 across four windows of the same session, and this block ALONE
# read 11/15 and 14/15. The band is too wide to attribute; the permission stays
# because it costs one clause and the failure it guards against is the tier's
# whole point.)
#
# Deliberately NOT a further clause about the closing condition or the note body:
# the line's contents are fixed at title + description, for the reasons the
# module docstring argues at length.


def render_note_rows(
    notes: Sequence[Note],
    *,
    today: int | None = None,
    extra: Callable[[Note], Sequence[tuple[str, str | None]]] = lambda _: (),
) -> str:
    """The index's ROWS — one element per note, and nothing else (#292).

    **The flat line this replaces was forgeable in two ways, both reproduced on
    `08d369b1` before it changed.** `- {title} — {description} (about {day})`
    used ` — ` as a field separator that a model-authored 60-character title may
    itself contain, and it escaped without collapsing, so a description holding
    a newline rendered as TWO index entries. One note became two; a title with
    an em dash had no determinable end.

    Neither was cosmetic. Since #228 the note-candidates create gate resolves
    the TITLE the model names back against this index
    (`domain.notes.note_handle`) and `ReplyGuard`'s R-55 latch keys on the same
    string, so an unclear title boundary is a mis-resolution rather than a
    legibility cost. `speaker_label` was written for exactly this class on the
    transcript and never reached here. `render_elements` closes both by
    construction: `escape_attr` closes the attribute quote, which content cannot
    write, and the description becomes CONTENT, where a newline is just a
    newline — the same move, and the same argument, as `<m>` (R-13c).

    **NO FILTER AND NO ENVELOPE, and that is the correction.** A first cut of
    this took `name`/`header`/`note` so it could return a finished
    `ContextBlock` for either caller, and filtered `is_standing` itself. Both
    were wrong:

    * The filter ran TWICE — here and in `proactive`, which needs the same
      sequence to build the `n -> note_id` map that `remind` and `note_wake`
      resolve a write against. Two derivations of one number, agreeing only
      because the predicate happened to be identical and idempotent. The
      previous code had one: the handle was passed IN. Now the caller filters
      once and hands the result here, so the numbering has a single source.
    * The envelope belongs to whoever serves the block. `header` had no
      non-default caller on day one and `<open_notes>` silently inherited the
      tag path's `NOTES_HEADER` through it, which is the fourth-and-fifth-knob
      problem arriving immediately.

    **The row's contents are still title + description + the day**, per spec
    §5.1/§6.2, and deliberately still NOT the closing condition — the module
    docstring argues that at length and nothing here reopens it. What changed is
    the grammar, not the ballot.

    `extra` is the one knob that is genuinely about a note rather than about the
    block: the proactive pass's `revisit` and `raised`. Additive, so a caller
    that passes none renders byte-identically to the tag path.

    `n` is POSITIONAL, and a caller that needs it to mean anything else is in
    the wrong place: it is a closed 1..N set, so a reference to a note outside
    the index is refusable rather than a lookup that might hit another row.
    """
    def _attrs(n: Note, i: int) -> tuple[tuple[str, str | None], ...]:
        return (("n", str(i)), ("title", n.title),
                ("about", _about(n, today)), *extra(n))

    return render_elements(
        notes, tag="note", attrs=_attrs, body=lambda n: n.description,
    )


def standing_only(notes: Sequence[Note]) -> tuple[Note, ...]:
    """The notes an index may show, in order — ONE definition of it.

    A retired note is never rendered: it would sit in the index as a live
    uncertainty, and beside its own replacement when there is one. This is a
    function rather than a comprehension repeated at each call site because the
    proactive pass builds its `n -> note_id` map over exactly this sequence and
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
    """The one-line `<today>` block: a DAY, `2026-10-01 Thu`, no time and no zone.

    Built three ways before this (note-merge, note-candidates and the curator
    each wrote `ContextBlock(name="today", body=escape(...))` and the curator
    its own fallback), so a change to the format was three edits. The caller
    passes the day and stamps its own `order` and `key` with `restamp` — where
    a block sits is the prompt's, not the block's. Note-merge declares no table
    and takes the defaults.

    `when` is a `date` for every real day; a `str` is shown as written, which
    is the curator's floor for a stored `YYYYMMDD` that is not a date (month 13)
    — a day it cannot name is still printed, because the model reads every
    `anchor` against this line.

    **A CLOCK THIS PROCESS OWNS, not anything the room said**, and escaped
    anyway: it is a block body, and `assemble`'s contract is that a body arrives
    escaped.

    `<today>`, NOT `<now>`: the proactive pass's `<now>` is a MOMENT with a time
    and a zone, a different block with a different description (#314).
    """
    text = f"{when:%Y-%m-%d %a}" if isinstance(when, date) else when
    return ContextBlock(name="today", body=escape(text))


def _about(note: Note, today: int | None) -> str | None:
    """The day the note is about, as a distance when there is a clock to read it
    against. `None` — no anchor, or no clock — DROPS the attribute rather than
    rendering an empty one, which `render_elements` does for `_when`'s reason."""
    if note.anchor_date is None:
        return None
    if today is None:
        return str(note.anchor_date)
    return _days_from(today, note.anchor_date)


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


#: `form` on an `<open_note>` whose body is not in the notebook's sections
#: (design 42 §8.6): the input says what it is showing, so a legacy body is
#: labelled as legacy rather than offered as the format (§6).
FORM_LEGACY = (
    "not in the notebook's sections: written before they existed, or added to "
    "without them"
)
#: `form` on an `<open_note>` rendered without its body, because the pass's
#: budget bound or its head could not be read (§8.6).
FORM_NOT_SHOWN = "body not shown in this pass"


def open_note_parts(
    note: Note, head: NoteRevision | None, *, room: str, now: datetime, today: date,
    handle: str | None = None, changed: str | None = None,
    excerpt: int | None = None, form: str | None = None, body: bool = True,
) -> tuple[tuple[tuple[str, str], ...], str]:
    """One open note's attributes and body: what it is about, what is
    undecided, what would settle it, and its body (design 42 §8.6).

    ONE line renderer for the two passes that read a note's body, each
    building its OWN block type from it: note-merge's `open_note`,
    note-candidates' `candidate_open_note`. The type is not shared because a
    type's `fields` render on every block of it, and note-candidates' `form`
    must not change merge's prompt (a deviation from §8.6, recorded in design
    42). note-merge sets `handle` (its `id`), `changed` and `excerpt`;
    note-candidates sets none of them, because it names a note by title and
    restates what it reads, so the body is rendered WHOLE. `form` says a body
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
