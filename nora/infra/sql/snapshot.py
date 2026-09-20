"""Everything Nora knew about one room at one real historical moment.

WHAT THIS IS FOR. An eval case built from invented dialogue measures her
against a room that never existed. A case built from a REAL `(platform,
conversation_id, timestamp)` measures her against something that happened —
but only if the state she actually held at that instant can be reconstructed,
and until `standing_revision` (migration `0059`) landed it could not: three of
the four inputs were already point-in-time recoverable and the fourth was
overwritten in place on every fold. One unrecoverable input makes the whole
reconstruction unrecoverable, so this module could not have existed before it.

READ-ONLY, AND THAT IS STRUCTURAL RATHER THAN A PROMISE. There is no `INSERT`,
`UPDATE`, `DELETE` or `begin()` in this file — every read takes
`engine.connect()`. A reconstruction that wrote would change the thing it is
reconstructing, and a second run over the same instant would answer differently
from the first.

WHERE IT LIVES, AND WHY HERE. It composes four reads of four tables through the
same store classes production runs, over a bare `AsyncEngine`, and imports
nothing above `nora.infra`. That is what `nora.infra.sql` already is, and every
other candidate location costs something real: `nora.app` would put a read-only
query behind the composition root, and the eval-facing layer above the
application (which obtains the composed subject and is where an eval harness
would CALL this from) is legitimately above `nora.app` and so may reach down to
here — the reverse would not be true. A caller that holds a composed subject
passes its engine; nothing here needs to know that a subject exists.

WHAT IT CANNOT FREEZE, stated rather than left to be discovered:

- **Honcho-backed state** — the peer card, the representation, anything the
  dialectic answers. It is live and mutable by design and has no revision
  chain to read backwards; the operator's decision is that it stays that way.
  A snapshot that quietly returned TODAY's peer card beside a year-old
  transcript would be the worst of both, so it returns none at all.
- **A note's loop state.** `note` has no history table — only `note_revision`
  does — so anything `UPDATE`d on the row after creation reads CURRENT here
  unless this module gates it. **SEVEN columns move**, from the three
  `sa.update(NOTE)` sites in `nora/infra/sql/notes.py`: `head_revision_id`
  (the compare-and-swap on append), `strikes`, `revisit_at` and
  `last_wake_revision_id` (the revisit loop), and `retired_at`, `replaced_by`
  and `retire_reason` (`supersede`). An earlier draft of this paragraph
  enumerated only the first four and called the rest write-once; it was wrong,
  and the three it missed are the three that matter most, because a note the
  snapshot claims was OPEN would have carried the reason and timestamp of a
  retirement that had not happened yet.

  The split is therefore between four that are LEFT current and three that are
  INSTANT-GATED:

    - `head_revision_id`, `strikes`, `revisit_at`, `last_wake_revision_id` read
      TODAY's values and must not be read as as-of. Nothing reconstructs from
      them: the body is the as-of revision, returned beside the note precisely
      so nobody has to resolve `head_revision_id` and silently get today's.
    - `retired_at`, `replaced_by` and `retire_reason` ARE gated — `_notes`
      blanks all three on a note whose `retired_at` is after `before_ms`, since
      such a note was still open at the instant. All three together, never one
      of them: `Note.__post_init__` refuses a `replaced_by` without a
      `retired_at`, and a `retire_reason` beside `retired_at=None` would read
      as a note that had been retired for no recorded reason.

  The columns that decide what the notebook SHOWED — title, description,
  uncertainty, closing condition, anchor, `created_at` — are genuinely written
  once at create and never updated, so the current row IS the as-of value for
  them. (`event_id` and `reminder_id` are declared but nothing in the repo
  writes them; they are `None` on every row and carry no instant.)

- **What a photo showed.** `_transcript` returns `messages.body` as stored, and
  since issue #116 that carries a media HANDLE — `[image a3f2c1]` — where the
  live turn carried a description of what the photo actually was. A
  reconstruction therefore shows bare handles in every row that had media,
  which is strictly less than the model saw at that instant. The join that
  resolves them (`nora.domain.media_join.MediaJoin`) needs a store, a screen
  and a metrics sink, all of which sit ABOVE `nora.infra` — reaching it from
  here would invert the layering this module was placed to respect. So the
  limitation is real, is named here, and is registered in
  `tests/architecture/test_media_join_reaches_every_transcript.py` rather than
  left for a reader to discover from a prompt that reads thinner than it
  should. Closing it means building the reconstruction's transcript one layer
  up, where the join already lives.
- **Anything before its tier's history existed.** `0059_standing_revision`
  deliberately did not backfill, so a room's standing chain begins at its first
  fold after that migration and `standing` reads `None` for any instant before
  it. That is the same `None` a room that had never been folded gives, and the
  two are not distinguishable from here.

THE TWO KEYS THIS FUNCTION CANNOT DERIVE, and will not guess. A room's culture
key and its notes' unit both come from `policy.toml`, not from the database:
the culture tier is Dreamer-written so it keys the conversation's `primary_id`
(`GLOSSARY.md`'s table, DEC-181), and the note tier keys the social `unit`
DEC-105 assigns the room. Neither is a function of `(platform,
conversation_id)` — they are equal to it today only by accident of data, which
`GLOSSARY.md` says in as many words — so both are REQUIRED arguments. A default
would be the exact failure the culture tier's own migration warns about: a key
that matches zero rows returns an empty culture and an empty notebook, raises
nothing, and reads identically to a room that genuinely had neither.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.culture import CultureRevision
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.notes import Note, NoteRevision
from nora.domain.standing import Standing
from nora.infra.sql.notes import _row_to_note, _row_to_revision
from nora.infra.sql.schema import (
    CULTURE_REVISION,
    MESSAGES,
    NOTE,
    NOTE_REVISION,
)
from nora.infra.sql.standing import SqlStandingStore

__all__ = ["Snapshot", "snapshot"]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One room, one instant, and the four things she held there.

    The three key fields are carried BACK as well as in, because a snapshot
    that does not say what it is a snapshot of is a bag of rows: two of them
    taken minutes apart are indistinguishable once they are out of the call
    that produced them, and the whole point of this type is that a case built
    from it can name the moment it was built from.
    """

    platform: str
    conversation_id: str
    #: The instant reconstructed, epoch ms. Every field below is «at or before»
    #: this — inclusive, because a fold or a message landing exactly here is
    #: part of what she knew here.
    before_ms: int

    #: The room's messages up to `before_ms`, OLDEST FIRST — `RecordStore.
    #: recent`'s order, because that is the order every renderer already
    #: expects and a reversed transcript reads to a model as a room talking
    #: backwards.
    transcript: tuple[TranscriptRow, ...]

    #: The position she held, or `None` if no fold had happened here yet. The
    #: cold start, the same meaning `last_folded_row_id is null` carries.
    standing: Standing | None

    #: The culture account current at `before_ms`, or `None`.
    culture: CultureRevision | None

    #: Each note visible in this room's unit then, with the body it carried
    #: then. Oldest-first by `created_at`, matching `SqlNoteStore.standing` —
    #: the notebook renders in that order on every tag, and re-ordering it
    #: reads to the model as the notebook having changed when nothing did.
    #:
    #: A pair rather than a `Note` alone because the note's `head_revision_id`
    #: points at TODAY's head; the second element is the as-of body, and
    #: pairing them is what stops a caller resolving the head and getting the
    #: wrong one without noticing.
    #:
    #: Every `Note` here reports `retired_at`/`replaced_by`/`retire_reason` as
    #: `None`, and that is a reconstruction rather than a coincidence: a note
    #: retired at or before the instant is not in this tuple at all, and one
    #: retired AFTER it has its retirement blanked, because it was open then.
    #: The four other mutable columns are TODAY's — see the module docstring.
    notes: tuple[tuple[Note, NoteRevision], ...]


async def snapshot(
    engine: AsyncEngine,
    *,
    platform: str,
    conversation_id: str,
    culture_key: str,
    unit: str,
    before_ms: int,
    transcript_limit: int | None = None,
) -> Snapshot:
    """Reconstruct what Nora knew about one room at `before_ms`.

    `culture_key` and `unit` are required and are NOT derived here — see the
    module docstring. `transcript_limit` bounds the transcript to its newest
    `limit` rows (still returned oldest-first, `RecordStore.recent`'s
    semantics); `None`, the default, is every row up to the bound, because the
    honest answer to «what had been said by then» has no natural top-N and a
    silent cut would make a short transcript ambiguous between «the room was
    quiet» and «the window clipped it».

    Four independent reads, run in SEQUENCE rather than concurrently: they are
    point-in-time reads of an append-only history, so nothing they could
    observe changes between them, and running them one after another means at
    most one of this call's connections is checked out of the pool at a time
    rather than four contending for it.

    Sequential is not the same as sharing a connection, and this does not share
    one: FOUR connections are opened and closed in turn, one per read —
    `_transcript`, `SqlStandingStore.read_as_of`, `_culture`, `_notes`. (This
    file carries four `engine.connect()` sites, not three, because
    `_transcript` has a bounded and an unbounded branch; exactly one of them
    runs. The fourth connection is `read_as_of`'s own.) One connection
    for the whole reconstruction would mean threading it through every helper,
    and `read_as_of` is a store method that owns its own — a cost with nothing
    to buy, since a sequence of reads never contends with itself.
    """
    transcript = await _transcript(
        engine, platform=platform, conversation_id=conversation_id,
        before_ms=before_ms, limit=transcript_limit,
    )
    # Bound to a NAME rather than called inline off the constructor, and that
    # is not style: `tests/architecture/test_store_methods_have_production_
    # callers.py` resolves a caller as an attribute access on a name-bound
    # receiver, because `(await conn.execute(stmt)).all()` once certified
    # `SqlMusicSessionStore.all` as used. `SqlStandingStore(engine).read_as_of(
    # ...)` is invisible to it, so writing it that way leaves the method
    # reading as «no production caller» while it has one.
    standing_store = SqlStandingStore(engine)
    standing = await standing_store.read_as_of(
        platform=platform, conversation_id=conversation_id, before_ms=before_ms,
    )
    culture = await _culture(engine, culture_key=culture_key, before_ms=before_ms)
    notes = await _notes(engine, unit=unit, before_ms=before_ms)
    return Snapshot(
        platform=platform,
        conversation_id=conversation_id,
        before_ms=before_ms,
        transcript=transcript,
        standing=standing,
        culture=culture,
        notes=notes,
    )


def _transcript_row(r: Any) -> TranscriptRow:
    """`SqlRecordStore._row`'s mapping, over the same eleven columns.

    Spelled out here rather than borrowed because that one is a method on a
    store that is CONSTRUCTED with a metrics sink and a bounds site, which it
    uses to report a cut window — this read does not cut, so reaching it would
    mean building an instrument with nothing to report just to get at a
    mapping. The note tier's two mappings, by contrast, are module-level
    functions over nothing but a row, so those are imported rather than
    re-typed. The columns are selected in the same order in both places, which
    is what makes this one comparable to its original at a glance.
    """
    return TranscriptRow(
        id=r.id,
        sender_id=r.sender_id,
        sender_name=r.sender_name,
        ts=r.ts,
        body=r.body,
        is_nora=bool(r.is_nora),
        delivery_status=DeliveryStatus(r.delivery_status),
        raw_json=r.raw_json,
        quoted_ts=r.quoted_ts,
        media_json=r.media_json,
        person_id=r.person_id,
    )


async def _transcript(
    engine: AsyncEngine,
    *,
    platform: str,
    conversation_id: str,
    before_ms: int,
    limit: int | None,
) -> tuple[TranscriptRow, ...]:
    """Every row of this room at or before `before_ms`, oldest first.

    **Filtered on PLATFORM as well as `conversation_id`, unlike
    `SqlRecordStore.recent`.** The store's reads are reached from a turn that
    already resolved one room, so the id alone is enough there; this is reached
    from a caller naming a room by the key `standing` and every lock in this
    repo use, and answering it with a Discord room's messages because a Signal
    room shares an id would be a reconstruction of somewhere else.

    **`is_nora` rows are INCLUDED.** The Dreamer's `since` excludes them because
    it derives evidence and must not read her own words back as the room's; this
    is not evidence, it is the transcript, and she was in it. A reconstruction
    that dropped her half would show a room talking to nobody.
    """
    columns = (
        MESSAGES.c.id, MESSAGES.c.sender_id, MESSAGES.c.sender_name,
        MESSAGES.c.ts, MESSAGES.c.body, MESSAGES.c.is_nora,
        MESSAGES.c.delivery_status, MESSAGES.c.raw_json,
        MESSAGES.c.quoted_ts, MESSAGES.c.media_json, MESSAGES.c.person_id,
    )
    where = (
        MESSAGES.c.platform == platform,
        MESSAGES.c.conversation_id == conversation_id,
        MESSAGES.c.ts <= before_ms,
    )
    if limit is not None:
        if limit <= 0:
            return ()
        # Newest-first + LIMIT, then reversed — `recent`'s shape, and for its
        # reason: the rows to keep are the NEWEST `limit`, so cutting after an
        # ascending read would keep the oldest and drop exactly the part of the
        # room that was still live at `before_ms`.
        stmt = (
            sa.select(*columns).where(*where)
            .order_by(MESSAGES.c.ts.desc(), MESSAGES.c.id.desc())
            .limit(limit)
        )
        async with engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(_transcript_row(r) for r in reversed(rows))

    stmt = (
        sa.select(*columns).where(*where)
        .order_by(MESSAGES.c.ts.asc(), MESSAGES.c.id.asc())
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return tuple(_transcript_row(r) for r in rows)


async def _culture(
    engine: AsyncEngine, *, culture_key: str, before_ms: int
) -> CultureRevision | None:
    """The account current at `before_ms`, or `None` if none had been written.

    **Read by `written_at`, NOT by walking back from `culture.head_revision_id`.**
    `SqlCultureStore.head`/`chain` walk the parent links, and they are right to:
    a chain read is asking what the sequence IS, and parent links are the
    sequence. This is asking a different question — which account was current at
    an instant — and the head is today's. Walking back from it would land on the
    same row only when the chain happens to be ordered by time, which is exactly
    what `chain`'s own docstring says must not be assumed.

    `written_at DESC, id DESC` matches `idx_culture_revision_key_written`, so
    the tie between two accounts sharing a millisecond breaks the same way on
    every run.
    """
    stmt = (
        sa.select(CULTURE_REVISION)
        .where(
            CULTURE_REVISION.c.culture_key == culture_key,
            CULTURE_REVISION.c.written_at <= before_ms,
        )
        .order_by(
            CULTURE_REVISION.c.written_at.desc(), CULTURE_REVISION.c.id.desc()
        )
        .limit(1)
    )
    async with engine.connect() as conn:
        row = (await conn.execute(stmt)).one_or_none()
    if row is None:
        return None
    return CultureRevision(
        id=row.id,
        culture_key=row.culture_key,
        body=row.body,
        written_at=row.written_at,
        established_by=row.established_by,
        parent_revision_id=row.parent_revision_id,
        instruction_sha256=row.instruction_sha256,
        model=row.model,
    )


async def _notes(
    engine: AsyncEngine, *, unit: str, before_ms: int
) -> tuple[tuple[Note, NoteRevision], ...]:
    """The unit's notebook as it stood at `before_ms`.

    **Visibility is reconstructed, not taken from today's row.**
    `SqlNoteStore.standing` asks `retired_at is null`, which is today's answer;
    the as-of question is three conditions, and each of them changes the answer
    for real rows:

      - `created_at <= before_ms` — a note written after the instant was not in
        the notebook then, however standing it is now;
      - `retired_at is null or retired_at > before_ms` — a note retired since
        WAS in the notebook then, and dropping it is the commonest way a
        reconstruction quietly under-reports what she was looking at;
      - the note had at least one revision by then, which is what the
        `continue` below enforces.

    **The first and third overlap completely, and neither can be falsified on
    its own** — measured, not assumed. `SqlNoteStore.create` writes the note and
    its first revision in ONE transaction with ONE `now_ms`, so a note created
    after the instant has no revision at or before it either: delete
    `created_at <= before_ms` and the revision filter still excludes it, delete
    the revision filter and `created_at` still does. Only removing BOTH turns
    `test_a_note_created_after_the_instant_is_not_in_the_notebook` red, and the
    test says so rather than claiming a pin it does not have.

    Both are kept anyway, and the reason is which QUESTION each answers.
    `created_at` says «the note did not exist»; the revision filter says «no
    body of it is known here». They are the same set today because of how
    `create` happens to write, which is a fact about another module — and the
    `continue` is the one that must not be dropped, because a note with no
    as-of body has no body to show and inventing one (today's, or an empty
    string) is worse than omitting it.

    **The BODY is the as-of revision**, the newest with `written_at <=
    before_ms`, never `head_revision_id` — which points at today's head and is
    the single easiest way to build a snapshot that looks right and shows a
    body written after the moment it claims to reconstruct.

    **AND THE RETIREMENT IS BLANKED, which the visibility predicate alone does
    not do.** Keeping a since-retired note is only half the reconstruction: the
    row it is built from is TODAY's, so the note came back carrying the
    `retired_at`, `replaced_by` and `retire_reason` of a retirement that had not
    happened at `before_ms` — a note the snapshot itself says was open,
    reporting the timestamp and the reason it was closed. `_as_of_note` below
    nulls all three when `retired_at > before_ms`, which is exactly the
    condition under which this query kept the row.

    All three together and never one of them: `Note.__post_init__` refuses a
    `replaced_by` with no `retired_at`, and a `retire_reason` left beside
    `retired_at=None` would read as a note retired for a recorded reason at no
    recorded time. The other four mutable columns (`head_revision_id`,
    `strikes`, `revisit_at`, `last_wake_revision_id`) are deliberately NOT
    gated — see the module docstring; there is nothing to gate them against,
    because `note` has no history and their as-of values are simply not
    recoverable, whereas a retirement's is: its absence.

    One query per note rather than a window function: the notebook is uncapped
    by design (`SqlNoteStore.standing` says why) but is a handful of rows per
    unit in practice, and this is an offline reconstruction with no turn
    waiting on it. If a unit's notebook ever grows enough for that to matter,
    the fix is a `DISTINCT ON (note_id)` over the revisions — stated so the
    next reader does not have to rediscover which shape it wants.
    """
    visible = (
        sa.select(NOTE)
        .where(
            NOTE.c.unit == unit,
            NOTE.c.created_at <= before_ms,
            sa.or_(
                NOTE.c.retired_at.is_(None),
                NOTE.c.retired_at > before_ms,
            ),
        )
        .order_by(NOTE.c.created_at.asc(), NOTE.c.id.asc())
    )
    out: list[tuple[Note, NoteRevision]] = []
    async with engine.connect() as conn:
        note_rows = (await conn.execute(visible)).all()
        for note_row in note_rows:
            revision_row = (await conn.execute(
                sa.select(
                    NOTE_REVISION.c.id.label("rev_id"),
                    NOTE_REVISION.c.note_id.label("rev_note_id"),
                    NOTE_REVISION.c.body,
                    NOTE_REVISION.c.parent_revision_id,
                    NOTE_REVISION.c.written_at,
                    NOTE_REVISION.c.established_by,
                    NOTE_REVISION.c.instruction_sha256,
                    NOTE_REVISION.c.model,
                )
                .where(
                    NOTE_REVISION.c.note_id == note_row.id,
                    NOTE_REVISION.c.written_at <= before_ms,
                )
                .order_by(
                    NOTE_REVISION.c.written_at.desc(), NOTE_REVISION.c.id.desc()
                )
                .limit(1)
            )).one_or_none()
            if revision_row is None:
                continue
            out.append((
                _as_of_note(note_row, before_ms=before_ms),
                _row_to_revision(revision_row),
            ))
    return tuple(out)


def _as_of_note(note_row: Any, *, before_ms: int) -> Note:
    """`_row_to_note`, with a retirement that had not happened yet removed.

    `note` has no history table, so every column of this row is TODAY's. Three
    of them — `retired_at`, `replaced_by`, `retire_reason` — are recoverable
    anyway, because their as-of value on a note the caller's own visibility
    predicate kept is known: the note was OPEN at `before_ms`, so all three
    were null. That is the one piece of a note's loop state a reconstruction
    can honestly restore, and leaving it un-restored was the defect this
    function exists to close.

    A note retired at or before `before_ms` never reaches here — `_notes`
    filters it out — so the branch below is not a second visibility decision
    duplicating that one; it is the same fact read for what it implies about
    three columns.
    """
    note = _row_to_note(note_row)
    if note.retired_at is None or note.retired_at <= before_ms:
        return note
    return replace(
        note, retired_at=None, replaced_by=None, retire_reason=None,
    )
