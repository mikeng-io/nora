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

WHERE IT LIVES, AND WHY HERE. It composes five reads of five tables through the
same store classes production runs, over a bare `AsyncEngine`, and imports
nothing above `nora.infra`. That is what `nora.infra.sql` already is, and every
other candidate location costs something real: `nora.app` would put a read-only
query behind the composition root, and the eval-facing layer above the
application (which obtains the composed subject and is where an eval harness
would CALL this from) is legitimately above `nora.app` and so may reach down to
here — the reverse would not be true. A caller that holds a composed subject
passes its engine; nothing here needs to know that a subject exists.

SINCE STEP 1b (design/cognition-loop/70-migration.md §2) every in-place
table keeps `row_history`, and `nora.infra.sql.snapshot_at` restores EVERY
store as of T — cancelled reminders and a note's loop state included — into
a sandbox of its own. This module is unchanged and still what the eval
runner's cases seed from (`nora.subject.sandbox.sandbox_from_snapshot`); the
gaps below are this module's, and `snapshot_at` does not have them for any
instant after migration `0068` (before it, its backfill says what is known).

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
- **WHEN a reminder was CANCELLED.** `reminders` has no history table, and
  exactly three of its columns move after create — verified, not assumed: the
  four `sa.update(REMINDERS)` sites in `nora/infra/sql/reminders.py`
  (`claim`, `mark`, `cancel`, `sweep_firing`) touch only `state`, `fired_at`
  and `outcome`. Everything else — `text`, `due_at`, `created_at`, `kind`,
  `payload`, the requester, the room, and every recurrence/series column — is
  written once by `_insert_values` and never updated, so today's row IS the
  as-of value for all of them.

  Two of the three moving columns are recoverable and one is not, and the
  split is the same shape as a note's retirement:

    - `fired_at` (and the `outcome`/terminal `state` it accompanies) IS
      instant-gated. Every terminal write stamps it — `mark(fired_at=now)` on
      the normal path and `sweep_firing` on the swept one, whose own docstring
      says the stamp is "the only timestamp that exists" for a swept row. So a
      resolution AFTER the instant is removable: `_reminders` reports the row
      `PENDING` with both blanked, because that is what it was.
    - a CANCELLATION is NOT. `cancel()` writes `state` and nothing else, and
      the schema says so in as many words: `fired_at` is "NULL until the row
      fires, and NULL forever on one that is cancelled." A row cancelled one
      minute after the instant and one cancelled a month before are the same
      row today, and no query can separate them.

  **So a cancelled row is OMITTED rather than guessed, and the direction is
  chosen deliberately.** Reporting it `PENDING` would be the dangerous guess:
  a pending reminder is what FIRES, so a row cancelled long before the instant
  would put a turn into the reconstruction that the room never had, and the
  suite would grade that turn as behaviour. Omitting it fails the other way,
  loudly — a case anchored on that row finds no row and ERRORs, which is a
  reconstruction refusing to answer rather than answering wrongly. It is the
  same rule the peer card follows two bullets up.

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

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.culture import (
    CultureRevision,
    character_key,
    nora_character_key,
    unit_culture_key,
)
from nora.domain.models import TranscriptRow
from nora.domain.notes import Note, NoteRevision
from nora.domain.reminders import Reminder, ReminderState
from nora.domain.standing import Standing
from nora.infra.sql.audit import transcript_row, transcript_select
from nora.infra.sql.migrations_runner import (
    SourceRevisionMismatchError,
    current_revision,
    head_revision,
)
from nora.infra.sql.notes import _row_to_note, _row_to_revision
from nora.infra.sql.reminders import _row_to_reminder
from nora.infra.sql.schema import (
    CULTURE_REVISION,
    MESSAGES,
    NOTE,
    NOTE_REVISION,
    REMINDERS,
)
from nora.infra.sql.standing import SqlStandingStore

__all__ = ["Snapshot", "snapshot"]

#: Postgres SQLSTATEs for "a read named a column/table that does not exist"
#: (`undefined_column`, `undefined_table`) — driver-agnostic: both asyncpg's
#: `UndefinedColumnError`/`UndefinedTableError` and psycopg's
#: `UndefinedColumn`/`UndefinedTable` expose the SAME `.sqlstate` attribute
#: with these values (verified against a real Postgres, 2026-09-25), so this
#: needs no import of either driver package — only `exc.orig.sqlstate`,
#: through whatever DBAPI wrapper SQLAlchemy used to reach it.
_MISSING_SCHEMA_SQLSTATES = frozenset({"42703", "42P01"})


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

    #: Every reminder this room had OUTSTANDING at `before_ms`, oldest first.
    #:
    #: Each one reports the state it held THEN, which for everything in this
    #: tuple is `PENDING`: a row is only here if nothing had resolved it by the
    #: instant, and `_reminders` blanks the `fired_at`/`outcome` of a
    #: resolution that had not happened yet. A row that had already fired,
    #: failed or been swept by then is not in this tuple at all.
    #:
    #: **A CANCELLED row is MISSING, and that is the one tier gap this module
    #: cannot close** — see the module docstring. It is an omission, never a
    #: silent `PENDING`.
    reminders: tuple[Reminder, ...]

    #: The UNIT's own account as it stood at `before_ms` (`unit_culture_key`),
    #: what `<unit_culture>` renders in every room of the unit; `None` when it
    #: had none. Read as the room's account is, by `written_at`.
    unit_culture: CultureRevision | None = None

    #: Each requested person's account in this unit as it stood at
    #: `before_ms` (`character_key(unit, person_id)`), as `(person_id,
    #: account)` in the order asked, a person with none omitted. What
    #: `<character>` renders for the persons the room lists.
    characters: tuple[tuple[str, CultureRevision], ...] = ()

    #: Her own account in this unit as it stood at `before_ms`
    #: (`nora_character_key(unit)`), what `<self>` renders; `None` when it had
    #: none (design/self-growth/20).
    nora_account: CultureRevision | None = None


async def snapshot(
    engine: AsyncEngine,
    *,
    platform: str,
    conversation_id: str,
    culture_key: str,
    unit: str,
    before_ms: int,
    transcript_limit: int | None = None,
    person_ids: Sequence[str] = (),
) -> Snapshot:
    """Reconstruct what Nora knew about one room at `before_ms`.

    `culture_key` and `unit` are required and are NOT derived here — see the
    module docstring. `person_ids` are the persons the room LISTS (the caller
    resolves them from the policy; this reads no policy): their per-unit
    accounts are copied beside the room's and the unit's own, so a turn
    rendered from the snapshot carries `<character>` and `<unit_culture>` as
    production does (the same `_culture` read, so each is as of `before_ms`).
    `transcript_limit` bounds the transcript to its newest
    `limit` rows (still returned oldest-first, `RecordStore.recent`'s
    semantics); `None`, the default, is every row up to the bound, because the
    honest answer to «what had been said by then» has no natural top-N and a
    silent cut would make a short transcript ambiguous between «the room was
    quiet» and «the window clipped it».

    Five independent reads, run in SEQUENCE rather than concurrently: they are
    point-in-time reads of an append-only history, so nothing they could
    observe changes between them, and running them one after another means at
    most one of this call's connections is checked out of the pool at a time
    rather than five contending for it.

    Sequential is not the same as sharing a connection, and this does not share
    one: FIVE connections are opened and closed in turn, one per read —
    `_transcript`, `SqlStandingStore.read_as_of`, `_culture`, `_notes`,
    `_reminders`. (`_culture` runs once for the room, once for the unit and
    once per listed person, each on its own connection in turn.) (This file
    carries five `engine.connect()` sites, not four, because `_transcript`
    has a bounded and an unbounded branch; exactly one of them runs. The
    remaining connection is `read_as_of`'s own.) One connection
    for the whole reconstruction would mean threading it through every helper,
    and `read_as_of` is a store method that owns its own — a cost with nothing
    to buy, since a sequence of reads never contends with itself.

    **FAIL-CLOSED on a source behind this checkout's migration head.**
    `nora.subject.sandbox.refuse_if_source_diverges_from_head` allows a
    source behind head to run at all — most migrations add nothing these
    five reads select (an index; `0064`'s own kind) — but cannot promise
    every column a NEWER migration added is one of them. This is the net:
    `UndefinedColumnError`/`UndefinedTableError` (SQLSTATE `42703`/`42P01`,
    driver-agnostic) from any of the five reads is caught and re-raised as
    `SourceRevisionMismatchError`, naming both revisions, rather than
    surfacing as a bare "column does not exist" deep inside a case.
    """
    try:
        transcript = await _transcript(
            engine, platform=platform, conversation_id=conversation_id,
            before_ms=before_ms, limit=transcript_limit,
        )
        # Bound to a NAME rather than called inline off the constructor, and
        # that is not style: `tests/architecture/test_store_methods_have_
        # production_callers.py` resolves a caller as an attribute access on
        # a name-bound receiver, because `(await conn.execute(stmt)).all()`
        # once certified `SqlMusicSessionStore.all` as used.
        # `SqlStandingStore(engine).read_as_of(...)` is invisible to it, so
        # writing it that way leaves the method reading as «no production
        # caller» while it has one.
        standing_store = SqlStandingStore(engine)
        standing = await standing_store.read_as_of(
            platform=platform, conversation_id=conversation_id, before_ms=before_ms,
        )
        culture = await _culture(engine, culture_key=culture_key, before_ms=before_ms)
        unit_culture = await _culture(
            engine, culture_key=unit_culture_key(unit), before_ms=before_ms,
        )
        nora_account = await _culture(
            engine, culture_key=nora_character_key(unit), before_ms=before_ms,
        )
        characters: list[tuple[str, CultureRevision]] = []
        for person_id in person_ids:
            account = await _culture(
                engine, culture_key=character_key(unit, person_id), before_ms=before_ms,
            )
            if account is not None:
                characters.append((person_id, account))
        notes = await _notes(engine, unit=unit, before_ms=before_ms)
        reminders = await _reminders(
            engine, platform=platform, conversation_id=conversation_id,
            before_ms=before_ms,
        )
    except sa.exc.DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate not in _MISSING_SCHEMA_SQLSTATES:
            raise
        source_rev = current_revision(
            engine.url.render_as_string(hide_password=False)
        )
        raise SourceRevisionMismatchError(
            f"source at {source_rev!r}, branch at {head_revision()!r}: a "
            f"read hit a missing column or table (SQLSTATE {sqlstate}) — "
            "the source predates a migration this checkout has. Refresh "
            "the source (`make eval-source`) or run on a branch at the "
            "source's head."
        ) from exc
    return Snapshot(
        platform=platform,
        conversation_id=conversation_id,
        before_ms=before_ms,
        transcript=transcript,
        standing=standing,
        culture=culture,
        notes=notes,
        reminders=reminders,
        unit_culture=unit_culture,
        characters=tuple(characters),
        nora_account=nora_account,
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
    # `SqlRecordStore`'s own select and mapping, so an edit folds here as it
    # does on every live read — bounded AS OF the instant: an edit made after
    # `before_ms` had not happened yet (design/message-edits/01 §3).
    select = transcript_select(edits_before=before_ms)
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
            select.where(*where)
            .order_by(MESSAGES.c.ts.desc(), MESSAGES.c.id.desc())
            .limit(limit)
        )
        async with engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(transcript_row(r) for r in reversed(rows))

    stmt = (
        select.where(*where)
        .order_by(MESSAGES.c.ts.asc(), MESSAGES.c.id.asc())
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return tuple(transcript_row(r) for r in rows)


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


async def _reminders(
    engine: AsyncEngine, *, platform: str, conversation_id: str, before_ms: int
) -> tuple[Reminder, ...]:
    """The room's OUTSTANDING reminders at `before_ms`, oldest first.

    **Keyed on `(platform, conversation_id)`, which is the pair the table
    actually carries** — unlike the transcript, whose rows have no platform
    column at all. So this needs no argument the caller cannot already give.

    The as-of rule is ONE sentence and it is deliberately not the state
    column: **a row was pending at the instant unless a `fired_at` at or
    before the instant says otherwise.** That single test covers every shape
    the table can hold, which is why it is written as one rule rather than a
    branch per state:

      - `pending` today, created by then — pending then;
      - `firing` today with no stamp — `claim()` set that NOW, not at the
        instant, so it was pending then;
      - `done`/`failed` stamped AFTER the instant — the resolution had not
        happened, so pending then, with the stamp and outcome removed;
      - `done`/`failed` stamped at or before it — genuinely resolved by then,
        and therefore not outstanding, so it is not returned at all.

    `created_at <= before_ms` is the existence test and is separate from all
    of that: a reminder written after the instant was not in the room's queue
    then however pending it is now.

    **CANCELLED rows are excluded entirely**, and that is the module
    docstring's named limitation rather than a filter with a reason of its
    own: `cancel()` records no instant, so «cancelled» carries no when. The
    exclusion is expressed in SQL rather than by dropping rows afterwards so
    that a reader of the query sees the whole visibility rule in one place.

    One `engine.connect()`, no transaction, consistent with every other read
    here.
    """
    instant = datetime.fromtimestamp(before_ms / 1000, tz=UTC)
    stmt = (
        sa.select(REMINDERS)
        .where(
            REMINDERS.c.platform == platform,
            REMINDERS.c.conversation_id == conversation_id,
            REMINDERS.c.created_at <= instant,
            REMINDERS.c.state != ReminderState.CANCELLED.value,
            # Outstanding AT the instant: either never stamped, or stamped
            # after it. A stamp at or before it means the row had already
            # been dealt with and was not in the queue she was looking at.
            sa.or_(
                REMINDERS.c.fired_at.is_(None),
                REMINDERS.c.fired_at > instant,
            ),
        )
        .order_by(REMINDERS.c.created_at.asc(), REMINDERS.c.id.asc())
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return tuple(_as_of_reminder(_row_to_reminder(row)) for row in rows)


def _as_of_reminder(reminder: Reminder) -> Reminder:
    """A row the query kept, with a resolution that had not happened removed.

    Every reminder `_reminders` returns was outstanding at the instant — that
    is what its `WHERE` selected — so all three moving columns have a known
    as-of value: `PENDING`, and no stamp, and no outcome. Restoring them is
    the same move `_as_of_note` makes for a retirement, and for the same
    reason: the row is TODAY's, and leaving it un-restored would hand back a
    reminder the snapshot itself calls outstanding while it carries the
    timestamp and the verdict of a firing still in its future.

    All three together, never one: a `fired_at` beside `PENDING` would claim
    a row that fired and then went back to waiting, and an `outcome` with no
    stamp would read as a verdict reached at no recorded time.
    """
    if (
        reminder.state is ReminderState.PENDING
        and reminder.fired_at is None
        and reminder.outcome is None
    ):
        return reminder
    return replace(
        reminder, state=ReminderState.PENDING, fired_at=None, outcome=None,
    )


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
