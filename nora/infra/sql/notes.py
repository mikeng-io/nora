"""`SqlNoteStore` — the note tier's store, over SQLAlchemy Core and the four
tables migration `0026_note_tier` lands. Its contract is exercised by
`tests/contract/test_note_store_contract.py`.

**The three behaviours that are load-bearing rather than incidental**, and what
breaks if any of them is reversed:

1. **`create` is one transaction.** The `note` row's `head_revision_id` names
   the revision written beside it, and `read`/`search` both JOIN on that id. A
   note whose head was committed before (or without) its revision is a title in
   the injected index with no body behind it — reachable by the model, and
   unreadable. There is deliberately no FK from `note.head_revision_id` to
   `note_revision.id` (the two tables reference each other, so one direction has
   to give), which is exactly why the atomicity has to be enforced here.

2. **`append_revision` is a compare-and-swap, expressed as the `UPDATE`'s own
   `WHERE`** — never a read, a comparison in Python, and then a write. Between
   those two statements is the window the CAS exists to close: a background
   merge or retire of the note mid-turn, or a parallel turn in the other room of the same
   unit. `UPDATE note SET head_revision_id = :new WHERE id = :id AND
   unit = :ws AND head_revision_id = :parent` is atomic in Postgres, so a
   `rowcount` of 0 IS the refusal, decided by the database rather than by a
   racing reader. On 0 the transaction rolls back and `StaleNoteHeadError` is
   raised: nothing is written on top, ever.

3. **Every read carries `WHERE unit = :unit`.** Unreachable, not
   merely unrendered. `read` on a note in another unit returns `None`,
   exactly as if it did not exist — a caller must not be able to distinguish
   "no such note" from "not yours", because the distinction alone confirms the
   note exists. `append_revision` refuses across the boundary for the same
   reason, and reports `observed_head = None` rather than the real head.

**Search joins the head.** The trigram index cannot be maintained on the head
revision only, as a partial index: which revision is the head is a fact about the
`note` row, not a predicate over `note_revision`, so there is nothing for
`postgresql_where` to test. `idx_note_revision_body_trgm` therefore covers superseded bodies
too, and `search`'s `note.head_revision_id = note_revision.id` join is what
keeps them out of the result. A search that surfaced a superseded body would
return something true-in-the-past as though it were current — the same class of
error the partial `idx_fact_standing` avoids by never rendering a superseded
fact.

**`ilike`, not the trigram similarity operator `%`.** `gin_trgm_ops` carries
amops for both, so `idx_note_revision_body_trgm` serves either — but they answer
different questions. `%` is whole-string similarity against
`pg_trgm.similarity_threshold`: «did anyone mention a hotel?» against a
three-sentence body shares a handful of trigrams out of dozens, scores far below
0.3, and returns nothing — a `nothing_found` for a body that plainly says
`hotel`. Substring containment is the question `note_search` is actually
asked, so `ilike '%…%'` is the operator, with the caller's query escaped.

**What the index does** (per `EXPLAIN (ANALYZE)`):

- The GIN index is reachable through a **parameterised** `ilike '%' || $1 ||
  '%'`, not only a literal pattern; SQLAlchemy emits exactly that.
- For a query of **three or more characters** it narrows properly.
- For a query of **two characters** — `酒店`, the realistic Cantonese case —
  pg_trgm cannot extract a complete trigram, so the bitmap index scan returns
  *every* row and the recheck discards all but the matches. The answer is still
  correct; the index does no work. The alternative is *wrong*: a
  `to_tsvector('english', …)` does not segment Chinese at all, so the same query
  returns nothing while reporting success.
- At this tier's size the planner may choose a sequential scan anyway. The
  property correctness depends on is `ilike`'s substring semantics, which hold
  with or without the index; the index is a performance affordance, and nothing
  here reads a null result as evidence of absence because of it.

**Entity resolution and merge are not in this store.** `create` does not resolve
`anchor_entity_ids` against the `fact_entity` registry or write `note_entity`
rows, and there is no `merge_groups`. `note.anchor_entity_ids` is the writer's
own free text; `note_entity` and `fact_entity` remain as tables with no reader.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.lineage import Lineage, note_revision_lineage, room_key
from nora.domain.note_body import cited_ids_in, rollup_keys_in
from nora.domain.notes import (
    MERGE_FORWARD_HOPS,
    Note,
    NoteRevision,
    NoteSupersedeReason,
    RetiredNoteError,
    StaleNoteHeadError,
    note_handle,
    proactive_row_forwarded_is_moved,
    tag_path_progressed_since_wake,
)
from nora.domain.reminders import (
    CANCELLED_DUPLICATE_NOTE,
    CANCELLED_NOTE_MOVED,
    OPEN_STATES,
    PROACTIVE_KIND,
    ReminderState,
)
from nora.infra.sql import memory_index
from nora.infra.sql.event_evidence import rollup_lineage
from nora.infra.sql.schema import (
    MESSAGES,
    NOTE,
    NOTE_REVISION,
    REMINDERS,
)
from nora.infra.sql.tag_traces import body_turn_lineage

log = logging.getLogger("nora.infra.sql.notes")

# PostgreSQL SQLSTATE 23505, unique_violation — `nora.infra.sql.reminders`'s
# own constant, duplicated rather than imported: `_repoint_stmt`'s caller
# needs to tell "the row I'm updating collided with another open row" apart
# from every other `IntegrityError` a plain `UPDATE` could raise, and that is
# the one property this store cares about, matched the same way the sibling
# store already does.
_PG_UNIQUE_VIOLATION = "23505"


def _row_to_note(r: Row[Any]) -> Note:
    return Note(
        id=r.id, unit=r.unit, title=r.title, description=r.description,
        uncertainty=r.uncertainty, closing_condition=r.closing_condition,
        established_in=r.established_in, created_at=r.created_at,
        head_revision_id=r.head_revision_id, anchor_date=r.anchor_date,
        anchor_entity_ids=tuple(r.anchor_entity_ids), retired_at=r.retired_at,
        replaced_by=r.replaced_by, retire_reason=r.retire_reason,
        revisit_at=r.revisit_at, strikes=r.strikes,
        last_wake_revision_id=r.last_wake_revision_id, event_id=r.event_id,
        reminder_id=r.reminder_id,
        lineage=Lineage.from_json(r.lineage, origin=r.established_in),
        exclude_persons=tuple(r.exclude_persons or ()),
    )


#: `messages.id` is int4: a cited number past it names no row, and binding it
#: would be a driver error rather than the ordinary "not on record".
_MESSAGE_ID_MAX = 2**31 - 1


async def _revision_lineage(
    conn: AsyncConnection, *, body: str, origin: str, first: bool, unit: str,
    evidence_room: str | None = None,
) -> tuple[Lineage, int | None]:
    """A revision's lineage and `as_of`, from the messages its body cites
    (`nora.domain.lineage.note_revision_lineage`: derived by code from the
    citation chain, never by the model). `as_of` is the newest cited message's
    time, or `None` for the caller to date at the write."""
    cited = cited_ids_in(body)
    rooms: dict[int, str] = {}
    newest: int | None = None
    lookup = [i for i in cited if 0 < i <= _MESSAGE_ID_MAX]
    if lookup:
        rows = (await conn.execute(
            sa.select(
                MESSAGES.c.id, MESSAGES.c.platform, MESSAGES.c.conversation_id,
                MESSAGES.c.ts,
            ).where(MESSAGES.c.id.in_(lookup), MESSAGES.c.hidden_at.is_(None))
        )).all()
        rooms = {r.id: room_key(r.platform, r.conversation_id) for r in rows}
        newest = max((r.ts for r in rows), default=None)
    origin_key = await _room_key_of(conn, origin)
    # A Rollup row the body cites brings its own lineage (`rollup_lineage`,
    # shared with the event store: one resolution of a key, in `unit`).
    rollups = {
        key: await rollup_lineage(conn, key=key, unit=unit)
        for key in sorted(rollup_keys_in(body))
    }
    lineage = note_revision_lineage(
        cited=cited, rooms=rooms, origin=origin, origin_key=origin_key,
        first=first, evidence_room=evidence_room, rollup_lineages=rollups,
    )
    return lineage.union(await body_turn_lineage(conn, body=body, unit=unit)), newest


async def _room_key_of(conn: AsyncConnection, conversation_id: str) -> str | None:
    """`conversation_id`'s room key, from the platform its Record was written
    under, or `None` when nothing it said is on record. A note carries no
    platform (`established_in` is a bare id); the Record does. The newest row
    decides, for the one conversation id two platforms could in principle
    share."""
    platform = (await conn.execute(
        sa.select(MESSAGES.c.platform)
        .where(
            MESSAGES.c.conversation_id == conversation_id,
            MESSAGES.c.hidden_at.is_(None),
        )
        .order_by(MESSAGES.c.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    return None if platform is None else room_key(platform, conversation_id)


async def _head_lineage(
    conn: AsyncConnection, *, unit: str, note_id: str,
) -> tuple[str, Lineage, int | None] | None:
    """A note's origin room, lineage and `as_of`, locked for this transaction:
    what a revision's lineage is unioned into. `None` when there is no such
    note in `unit` (the compare-and-swap that follows refuses it)."""
    row = (await conn.execute(
        sa.select(NOTE.c.established_in, NOTE.c.lineage, NOTE.c.as_of)
        .where(NOTE.c.id == note_id, NOTE.c.unit == unit)
        .with_for_update()
    )).one_or_none()
    if row is None:
        return None
    return (
        row.established_in,
        Lineage.from_json(row.lineage, origin=row.established_in),
        row.as_of,
    )


def _newest(*values: int | None) -> int | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _row_to_revision(r: Row[Any]) -> NoteRevision:
    return NoteRevision(
        id=r.rev_id, note_id=r.rev_note_id, body=r.body,
        parent_revision_id=r.parent_revision_id, written_at=r.written_at,
        established_by=r.established_by,
        instruction_sha256=r.instruction_sha256, model=r.model,
    )


#: `note.id` and `note_revision.id` are `pg.UUID`, so a string that is not
#: UUID-shaped reaches asyncpg as a driver type error rather than as the
#: ordinary "no such row" outcome. A malformed id is a bad tool argument, not a
#: broken store, and a store that raises on one turns it into a dead turn.
def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


#: The two proactive rows `merge_into`'s `_repoint_proactive_rows` is
#: choosing between — `id`, `state`, `due_at`, `created_at`, keyed to the one
#: note id each names. A `Row[Any]`, not `Reminder`: this reads only the four
#: columns the choice and the cancel need, never the payload's own body or a
#: full decode `_row_to_reminder`-shaped (that decoder lives in
#: `nora.infra.sql.reminders`, and a note store importing it would import a
#: sibling's private helper for four columns it can select itself).
def _keep_open_row(
    a: Row[Any], b: Row[Any],
) -> tuple[Row[Any], Row[Any]]:
    """Which of two open proactive rows on notes a fold is about to make ONE
    note survives, and which is cancelled — `(keep, drop)`.

    A `firing` row is never given up for a `pending` one — see
    `_repoint_proactive_rows`'s docstring for why that is the opposite of
    migration 0064's own boot-time tie-break rather than a second copy of it.
    Otherwise the earliest `due_at` wins: it is the decision that would have
    spoken first, and every later copy only repeats it. `created_at` then
    `id` break a tie so the choice is deterministic rather than whichever
    order Postgres happened to hand the `SELECT` back in.

    **`drop` can itself be `firing`** — the one shape this function cannot
    avoid handing back — when BOTH rows were firing (`a_firing == b_firing
    == True`; the tie then falls through to the `due_at` branch like any
    other tie). The caller does not cancel a firing `drop`; see
    `_repoint_proactive_rows`.
    """
    a_firing = a.state == ReminderState.FIRING.value
    b_firing = b.state == ReminderState.FIRING.value
    if a_firing != b_firing:
        return (a, b) if a_firing else (b, a)
    key = (a.due_at, a.created_at, a.id), (b.due_at, b.created_at, b.id)
    return (a, b) if key[0] <= key[1] else (b, a)


def _repoint_stmt(
    reminder_id: Any, state: str, survivor_id: str, revision_id: str,
) -> Any:
    """`payload = payload || {"note_id": ..., "revision_id": ...}` — a JSONB
    merge, not a replace, so anything else the payload carries (`note_title`,
    `intent`) survives the re-point untouched.

    **Both keys move together.** Moving `note_id` alone leaves `revision_id` naming a
    revision of the DUPLICATE — a note that no longer exists in the
    survivor's chain. At fire time `nora.app.proactive_screen.
    ProactiveScreen.refusal` (and `remind`'s own `_open_rows_on`) now reads
    this row as an ordinary row ON the survivor (its `note_id` says so), not
    a forwarded one, so it runs `tag_path_progressed_since_wake` against the
    SURVIVOR's chain with a `last_wake_revision_id` that chain does not
    contain. That walk never finds it, falls all the way to the note's root
    revision, and returns `True` the moment it meets ANY tag-path-established
    revision — which is nearly every note's own creation — cancelling
    `cancelled_note_moved` a row that was never actually stale. `revision_id`
    here is always the FOLD's own new revision (`merge_into`'s
    `revision.id`), which — because the caller only reaches this statement
    once `_repoint_proactive_rows` has classified the row live — is
    genuinely the survivor's head at the moment this statement runs, so the
    very next check against it starts from `False`.

    Guarded on `state` too, matching this module's house style for a write
    inside a transaction that already holds the row's lock
    (`with_for_update`). The CALLER checks the returned `rowcount` and raises
    if it is not exactly 1 — under that lock a mismatch should be
    unreachable before this transaction commits, so a 0 here is a bug
    surfacing loudly rather than a row silently left un-repointed."""
    return (
        sa.update(REMINDERS)
        .where(REMINDERS.c.id == reminder_id, REMINDERS.c.state == state)
        .values(
            payload=REMINDERS.c.payload.op("||")(
                sa.func.jsonb_build_object(
                    "note_id", survivor_id, "revision_id", revision_id,
                )
            ),
        )
    )


def _cancel_stmt(
    reminder_id: Any, state: str, outcome: str, fired_at: datetime,
) -> Any:
    """The shared shape of both cancellations `_repoint_proactive_rows`
    writes — collision loss (`cancelled_duplicate_note`)
    and staleness (`cancelled_note_moved`) differ only in `outcome`. Same
    `state`-guarded, rowcount-checked-by-the-caller contract as `_repoint_stmt`."""
    return (
        sa.update(REMINDERS)
        .where(REMINDERS.c.id == reminder_id, REMINDERS.c.state == state)
        .values(
            state=ReminderState.CANCELLED.value, outcome=outcome,
            fired_at=fired_at,
        )
    )


def _require_row_changed(result: Any, *, what: str) -> None:
    """`rowcount` checked, not assumed — the "state guard fails loudly" claim
    `_repoint_stmt`'s docstring makes is only true if a CALLER actually reads
    the result it returns. Every write `_repoint_proactive_rows` and
    `_repoint_row` issue is against a row this same transaction
    already `SELECT ... FOR UPDATE`'d, so under READ COMMITTED nothing else
    can have changed its `state` before this statement runs — a 0 here means
    that invariant broke, not that the row was merely busy, and a silent
    no-op would leave a reminder in whatever state it already had while the
    caller believed it had been cancelled or re-pointed."""
    if not result.rowcount:
        raise RuntimeError(
            f"SqlNoteStore.merge_into could not {what}: the row's state "
            f"changed after this transaction's own SELECT ... FOR UPDATE, "
            f"which should be unreachable before commit"
        )


class SqlNoteStore:
    """`__init__(self, engine: AsyncEngine)` and nothing else BY DEFAULT. No
    config, no clock, no metrics. `now_ms` arrives on `create`/`append_revision`, not on this
    constructor, so a contract-suite fixture in
    another session can build this class from a bare `AsyncEngine` alone and
    never has to guess what else it needs.

    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(
        self,
        *,
        unit: str,
        title: str,
        description: str,
        uncertainty: str,
        closing_condition: str,
        established_in: str,
        body: str,
        established_by: str,
        now_ms: int,
        anchor_date: int | None = None,
        anchor_entity_ids: tuple[str, ...] = (),
        # Passed through to the FIRST revision, and `None` from the tag path.
        # `note_create` has no instruction digest; the Cognition session's
        # writes may. See `0036_note_revision_provenance` for why the columns
        # are nullable.
        instruction_sha256: str | None = None,
        model: str | None = None,
    ) -> Note:
        """The note row and its first revision, in ONE transaction (see the
        module docstring's (1)).

        The `Note` and `NoteRevision` dataclasses are constructed BEFORE either
        insert, so `nora.domain.notes`' own `__post_init__` refusals — no
        anchor, an over-long index entry, a blank closing condition — raise
        before anything reaches the database. The table's CHECKs say the same
        things and are the real guarantee; building the objects first is what
        makes the two implementations of this store refuse *identically* rather
        than one raising `ValueError` and the other `IntegrityError`.
        """
        note_id = str(uuid4())
        revision_id = str(uuid4())
        note = Note(
            id=note_id, unit=unit, title=title, description=description,
            uncertainty=uncertainty, closing_condition=closing_condition,
            established_in=established_in, created_at=now_ms,
            head_revision_id=revision_id, anchor_date=anchor_date,
            anchor_entity_ids=tuple(anchor_entity_ids),
        )
        revision = NoteRevision(
            id=revision_id, note_id=note_id, body=body, parent_revision_id=None,
            written_at=now_ms, established_by=established_by,
            instruction_sha256=instruction_sha256, model=model,
        )
        async with self._engine.begin() as conn:
            await self._create_on_conn(conn, note=note, revision=revision)
        return note

    @staticmethod
    async def _create_on_conn(
        conn: AsyncConnection, *, note: Note, revision: NoteRevision,
    ) -> None:
        """The two inserts, on a connection the CALLER owns — the `create`
        sibling of `_append_on_conn`, extracted for the same reason: which
        transaction a write belongs to is the caller's decision.

        Lineage and `as_of` (migration `0073_lineage`) are computed here from
        the body's citations: the first revision rests on the room the note
        was raised in and on every room it cites."""
        lineage, cited_at = await _revision_lineage(
            conn, body=revision.body, origin=note.established_in, first=True,
            unit=note.unit,
        )
        as_of = cited_at if cited_at is not None else revision.written_at
        derived = {
            "lineage": lineage.to_json(), "as_of": as_of,
            "exclude_persons": list(note.exclude_persons),
        }
        await conn.execute(sa.insert(NOTE).values(
            id=note.id, unit=note.unit, title=note.title,
            description=note.description, uncertainty=note.uncertainty,
            closing_condition=note.closing_condition,
            anchor_date=note.anchor_date,
            anchor_entity_ids=list(note.anchor_entity_ids),
            established_in=note.established_in, created_at=note.created_at,
            head_revision_id=note.head_revision_id,
            # `create()`'s own `Note` never sets this (`revisit_at` defaults
            # `None`). Written unconditionally rather than only when not-None.
            revisit_at=note.revisit_at,
            **derived,
        ))
        await conn.execute(sa.insert(NOTE_REVISION).values(
            id=revision.id, note_id=revision.note_id, body=revision.body,
            parent_revision_id=None, written_at=revision.written_at,
            established_by=revision.established_by,
            instruction_sha256=revision.instruction_sha256,
            model=revision.model,
            citations=list(revision.citations),
            **derived,
        ))
        await memory_index.project(conn, "note", note.id)

    async def standing(self, *, unit: str) -> tuple[Note, ...]:
        """Every note that has not been retired — the read the injected index
        renders from, and the one note-tier read on the turn's critical
        path. `retired_at is null` matches `idx_note_standing`'s own predicate,
        so this is an index scan rather than a filter over history.

        **Uncapped on purpose**: there is no `limit`. A note silently
        missing from the index is invisible to her, and invisible is
        indistinguishable from absent. The pressure valve is `note_set_status`
        retiring closed notes, not a top-N here.

        Ordered oldest-first, and deterministic (`created_at`, then `id` to
        break a tie between two notes created in the same millisecond). The
        block is rendered on every tag; an unordered read makes the same
        notebook render in a different order turn to turn, which reads to the
        model as the notebook having changed when nothing did. Oldest-first
        specifically, so a new note appends to the end and the prefix stays put.
        """
        stmt = (
            sa.select(NOTE)
            .where(
                NOTE.c.unit == unit,
                NOTE.c.retired_at.is_(None),
            )
            .order_by(NOTE.c.created_at.asc(), NOTE.c.id.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(_row_to_note(r) for r in rows)

    async def successor(self, *, unit: str, note_id: str) -> Note | None:
        """The OPEN note a `retired-merged` note was folded into, following
        `replaced_by` — or `None` when the note is open itself, retired for
        any other reason, absent, or the chain does not end on an open note
        within `MERGE_FORWARD_HOPS`.

        Only `retired-merged` forwards. `retired-closed`/`retired-expired`
        mean the question is over, and a `replaced_by` on one of those is provenance, not a promise
        that the successor carries the same question.
        """
        current = note_id
        async with self._engine.connect() as conn:
            for _ in range(MERGE_FORWARD_HOPS):
                if not _is_uuid(current):
                    return None
                row = (await conn.execute(
                    sa.select(NOTE).where(
                        NOTE.c.id == current, NOTE.c.unit == unit,
                    )
                )).one_or_none()
                if row is None:
                    return None
                if row.retired_at is None:
                    return _row_to_note(row) if current != note_id else None
                if (row.retire_reason != NoteSupersedeReason.RETIRED_MERGED
                        or row.replaced_by is None):
                    return None
                current = str(row.replaced_by)
        return None

    async def merged_successor_by_title(
        self, *, unit: str, title: str,
    ) -> Note | None:
        """The open note a `retired-merged` note with this title forwards to,
        or `None` — including when two merged notes of that title forward to
        DIFFERENT open notes, because picking one is the guess every resolver
        in this tier refuses.

        Matched through `note_handle` in Python rather than in SQL: Postgres
        `lower` is not `note_handle`'s `casefold`, and the resolvers this
        serves compare with `note_handle`. The set is the unit's merged notes
        only, which stays small because merges are rare.
        """
        wanted = note_handle(title)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(NOTE.c.id, NOTE.c.title).where(
                    NOTE.c.unit == unit,
                    NOTE.c.retire_reason == NoteSupersedeReason.RETIRED_MERGED,
                )
            )).all()
        found: dict[str, Note] = {}
        for r in rows:
            if note_handle(r.title) != wanted:
                continue
            survivor = await self.successor(unit=unit, note_id=str(r.id))
            if survivor is not None:
                found[survivor.id] = survivor
        return next(iter(found.values())) if len(found) == 1 else None

    async def read(
        self, *, unit: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None:
        """The note and its HEAD revision, or `None`.

        Retired notes are readable — retirement is a status change, not a delete,
        and history that cannot be read back cannot re-derive a lossy
        step that went wrong. `standing` is where the retired ones disappear.

        A note in another unit answers `None`, identically to one that does
        not exist (module docstring (3)). So does a `note_id` that is not even
        UUID-shaped.
        """
        if not _is_uuid(note_id):
            return None
        async with self._engine.connect() as conn:
            row = (await conn.execute(self._head_join().where(
                NOTE.c.id == note_id, NOTE.c.unit == unit,
            ))).one_or_none()
        if row is None:
            return None
        return _row_to_note(row), _row_to_revision(row)

    async def append_revision(
        self,
        *,
        unit: str,
        note_id: str,
        body: str,
        parent_revision_id: str,
        established_by: str,
        now_ms: int,
        instruction_sha256: str | None = None,
        model: str | None = None,
        evidence_room: str | None = None,
    ) -> NoteRevision:
        """The compare-and-swap append. See the module docstring's (2): the
        comparison IS the `UPDATE`'s `WHERE`, so a `rowcount` of 0 is the
        database refusing, not a reader losing a race it did not know it was in.

        Raises `StaleNoteHeadError` when the head is not `parent_revision_id` —
        including when the note does not exist, is in another unit, or was
        named by a malformed id, all of which report `observed_head = None`
        because from this caller's side they are the same situation with the
        same remedy. The transaction is rolled back by the `raise`, so the
        refusal writes nothing: no orphan `note_revision` row, no moved head.

        `parent_revision_id` is required and not nullable, unlike
        `NoteRevision.parent_revision_id` itself: `None` is the FIRST revision
        and nothing else, and the first revision is `create`'s job. A caller
        able to pass `None` here could append a second parentless revision and
        the CAS would have nothing to compare.
        """
        revision_id = str(uuid4())
        revision = NoteRevision(
            id=revision_id, note_id=note_id, body=body,
            parent_revision_id=parent_revision_id, written_at=now_ms,
            established_by=established_by,
            instruction_sha256=instruction_sha256, model=model,
        )
        if not _is_uuid(note_id) or not _is_uuid(parent_revision_id):
            raise StaleNoteHeadError(
                f"note {note_id!r} is not a note id this store could look up — "
                f"read it again",
                observed_head=None,
            )
        async with self._engine.begin() as conn:
            await self._append_on_conn(
                conn, unit=unit, revision=revision, evidence_room=evidence_room,
            )
        return revision

    @staticmethod
    async def _append_on_conn(
        conn: AsyncConnection, *, unit: str, revision: NoteRevision,
        evidence_room: str | None = None,
    ) -> None:
        """The CAS and the insert, on a connection the CALLER owns.

        A static method taking `conn` rather than a method opening its own,
        because "which transaction is this in" must be the caller's decision
        (`merge_into` writes two revisions in one).
        """
        note_id = revision.note_id
        parent_revision_id = revision.parent_revision_id
        revision_id = revision.id
        # LINEAGE (migration `0073_lineage`): this revision rests on the rooms
        # its body cites and, from the tag path, the room it was written in;
        # the note rests on the union. Read under the row lock the CAS below
        # then takes, so the union is over the head it replaces.
        head = await _head_lineage(conn, unit=unit, note_id=note_id)
        derived: dict[str, Any] = {}
        head_values: dict[str, Any] = {"head_revision_id": revision_id}
        if head is not None:
            origin, held, held_as_of = head
            lineage, cited_at = await _revision_lineage(
                conn, body=revision.body, origin=origin, first=False,
                evidence_room=evidence_room, unit=unit,
            )
            as_of = cited_at if cited_at is not None else revision.written_at
            derived = {"lineage": lineage.to_json(), "as_of": as_of}
            head_values |= {
                "lineage": held.union(lineage).to_json(),
                "as_of": _newest(held_as_of, as_of),
            }
        swapped = await conn.execute(
            sa.update(NOTE)
            .where(
                NOTE.c.id == note_id,
                NOTE.c.unit == unit,
                NOTE.c.head_revision_id == parent_revision_id,
                # AN APPEND REQUIRES AN OPEN NOTE (guard 1).
                # `supersede` sets `retired_at` and never touches the head, so
                # without this a writer that read a note and then lost a race
                # to a retirement appended successfully into a note nothing
                # reads again.
                NOTE.c.retired_at.is_(None),
            )
            .values(**head_values)
        )
        if swapped.rowcount == 0:
            # Read the head back only to build the message — the refusal was
            # already decided, above, by the UPDATE that matched no row.
            # This SELECT is inside the same transaction, so what it reports
            # is what the failed CAS actually saw.
            seen = (await conn.execute(
                sa.select(
                    NOTE.c.head_revision_id, NOTE.c.retired_at,
                    NOTE.c.retire_reason, NOTE.c.replaced_by,
                ).where(NOTE.c.id == note_id, NOTE.c.unit == unit)
            )).one_or_none()
            observed = seen.head_revision_id if seen is not None else None
            if seen is not None and seen.retired_at is not None:
                raise RetiredNoteError(
                    f"note {note_id} was retired ({seen.retire_reason}) — "
                    f"read the notebook again",
                    observed_head=observed,
                    retire_reason=seen.retire_reason,
                    replaced_by=(
                        str(seen.replaced_by)
                        if seen.replaced_by is not None else None
                    ),
                )
            raise StaleNoteHeadError(
                f"note {note_id} changed under you: this revision was "
                f"written against {parent_revision_id}, the head is "
                f"{observed} — read it again"
                if observed is not None
                else f"note {note_id} is not in unit {unit!r} — "
                     f"read it again",
                observed_head=observed,
            )
        await conn.execute(sa.insert(NOTE_REVISION).values(
            id=revision.id, note_id=revision.note_id, body=revision.body,
            parent_revision_id=revision.parent_revision_id,
            written_at=revision.written_at,
            established_by=revision.established_by,
            instruction_sha256=revision.instruction_sha256,
            model=revision.model,
            citations=list(revision.citations),
            **derived,
        ))
        await memory_index.project(conn, "note", note_id)

    async def search(
        self, *, unit: str, query: str, limit: int,
    ) -> tuple[tuple[Note, str], ...]:
        """Substring search over HEAD bodies, unit-scoped, standing only.
        Returns `(note, matching_body)` pairs — the note so the caller can name
        and read it, the body so a hit is legible without a second round trip.

        The `note.head_revision_id = note_revision.id` join is the head-only
        guarantee (module docstring); `retired_at is null` is the same argument
        one step further out, since a retired note is absent from the injected
        index and a hit on one hands her a note whose title she cannot see and
        whose question is already closed.

        A blank query returns `()` rather than the whole notebook: `ilike '%%'`
        matches every body, and «everything» is not a search result. A
        non-positive `limit` clamps to `()` rather than reaching the database:
        Postgres errors outright on a negative `LIMIT`, and the in-memory
        implementation must not answer `list[:-1]` (all but the last) to the same
        input.

        `autoescape=True` on `icontains` escapes `%` and `_` in the caller's
        query. Without it a query of `%` returns every note in the unit and
        `100%` returns every note containing `100`, because the caller's text
        would be `ilike`'s own wildcard syntax. The query is data.
        """
        if limit <= 0 or not query.strip():
            return ()
        stmt = (
            self._head_join()
            .where(
                NOTE.c.unit == unit,
                NOTE.c.retired_at.is_(None),
                NOTE_REVISION.c.body.icontains(query, autoescape=True),
            )
            .order_by(NOTE.c.created_at.asc(), NOTE.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple((_row_to_note(r), r.body) for r in rows)

    # ---- the wake pass ----------------------------------------------

    async def revision_chain(
        self, *, unit: str, note_id: str,
    ) -> tuple[NoteRevision, ...]:
        """Every revision of one note, oldest first — the chain
        `tag_path_progressed_since_wake` walks, exposed as a READ so a caller
        outside the store can ask the same question about a revision it
        recorded earlier (the heartbeat cancels a proactive row when
        the tag path revised the note after the row was linked to it).

        `()` for a note that is absent, in another unit, or not even
        UUID-shaped — the same «not reachable from here» answer `read` gives,
        never a partial chain. Retired notes ARE readable, for `read`'s
        reason: retirement is a status change, not a delete.
        """
        if not _is_uuid(note_id):
            return ()
        async with self._engine.connect() as conn:
            owner = (await conn.execute(
                sa.select(NOTE.c.id).where(
                    NOTE.c.id == note_id, NOTE.c.unit == unit,
                )
            )).one_or_none()
            if owner is None:
                return ()
            rows = (await conn.execute(
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
                .where(NOTE_REVISION.c.note_id == note_id)
                .order_by(NOTE_REVISION.c.written_at.asc(), NOTE_REVISION.c.id.asc())
            )).all()
        return tuple(_row_to_revision(r) for r in rows)

    # ---- read-back, for the shared contract suite --------------------------
    #
    # The contract suite's read-back; not a verb the runtime may call.

    async def revision_by_id(self, *, revision_id: str) -> NoteRevision | None:
        """One revision by id, or `None`. **Not unit-scoped, and that is
        deliberate rather than an omission** — a revision is reached through the
        note that owns it. Scoping here too would mean joining `note` on every
        call to answer a question the caller then asks again.
        """
        if not _is_uuid(revision_id):
            return None
        stmt = sa.select(
            NOTE_REVISION.c.id.label("rev_id"),
            NOTE_REVISION.c.note_id.label("rev_note_id"),
            NOTE_REVISION.c.body,
            NOTE_REVISION.c.parent_revision_id,
            NOTE_REVISION.c.written_at,
            NOTE_REVISION.c.established_by,
            NOTE_REVISION.c.instruction_sha256,
            NOTE_REVISION.c.model,
        ).where(NOTE_REVISION.c.id == revision_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_revision(row)

    # ---- retirement ------------------------------------------------------------

    async def supersede(
        self,
        *,
        key: str,
        target: str,
        replaced_by: str | None,
        because: str,
        source: str | None,
        now_ms: int,
        expected_head: str | None = None,
    ) -> int:
        """Retirement, which is a status change and
        never a delete. Returns rows affected.

        **`expected_head` is guard 3**, and it is not on the
        port. A caller that judged a note on one head passes it, and the
        retirement is then a compare-and-swap like an append: a note whose
        head moved since — a `note_merge` fold that just appended a
        duplicate's question to it, or a tag-path revision — answers 0 and
        stays open. Without it a re-read-then-retire is
        check-then-act across two transactions, and a merge landing between
        the two would retire the survivor with the question it had just absorbed.
        `None` is head-blind for a caller that judged
        nothing about the body.

        **A null `replaced_by` is LEGAL here, and that is the whole reason the
        port is permissive.** A note need not have a successor: an anchor date
        passed and the dinner happened, and it is replaced by nothing.
        Refusing here would force the caller to mint a placeholder id to
        satisfy a signature — the "invent a value to satisfy a schema" move
        this codebase forbids everywhere else, and `replaced_by` is typed
        `str | None` so that it never has to.

        **What IS refused is a successor with no retirement.** `note`'s
        `note_replacement_implies_retirement_check` is a ONE-DIRECTIONAL
        implication — a replacement implies a retirement, never the converse —
        and this verb always sets `retired_at`, so the illegal state is
        unreachable through it. The double must refuse it too, by reaching
        around its own types, or the two implementations are not
        interchangeable and the contract suite cannot see the difference.

        `because` is stored in `retire_reason` and is validated against
        `NoteSupersedeReason.ALL`. A free-text reason would make the retirement
        vocabulary unqueryable — «how many notes were retired this
        week, and why» is the reading that justifies keeping the reason, and
        it cannot be answered against prose.

        A `target` that names no standing note in this unit reports **0
        rather than raising**, including a malformed id: a background pass that
        raises on a row it does not recognise takes the whole tick down with it.
        A malformed `replaced_by`, by contrast, RAISES, and the split is the
        point: that is a caller bug, and reporting 0 would make it
        indistinguishable from "already retired" — an ERROR surfacing as
        `nothing_found`.
        """
        if source is not None:
            # REFUSED, not accepted-and-dropped, and the shared suite has a
            # test for exactly this distinction. `note` has no column for a
            # supersession's citation because what closed an open question is
            # its revision history, not a URL — so a caller passing one is
            # asking for something this tier cannot do, and answering `ok`
            # while discarding it is `unavailable` wearing `ok`'s clothes at
            # the storage boundary. A tier that cannot record a field says so.
            raise ValueError(
                f"SqlNoteStore.supersede cannot record a supersession source "
                f"({source!r}): `note` has no column for one. What closed a "
                f"note is its revision history, not a citation — the fact tier "
                f"needs `source` because a contradicting source IS the warrant "
                f"there, and the port carries it for that tier, not this one."
            )
        if because not in NoteSupersedeReason.ALL:
            raise ValueError(
                f"SqlNoteStore.supersede got because={because!r}; the note "
                f"tier's retirement vocabulary is {NoteSupersedeReason.ALL} — "
                f"free text would make 'how many were retired, and why' "
                f"unanswerable"
            )
        if replaced_by is not None and not _is_uuid(replaced_by):
            raise ValueError(
                f"SqlNoteStore.supersede got a malformed replaced_by "
                f"{replaced_by!r}: not a UUID. A caller bug, not an absent row "
                f"— reporting 0 here would be indistinguishable from the target "
                f"not existing, the ERROR-as-nothing_found shape R-27 forbids."
            )
        if not _is_uuid(target):
            return 0
        if expected_head is not None and not _is_uuid(expected_head):
            return 0
        conditions = [
            NOTE.c.id == target,
            NOTE.c.unit == key,
            NOTE.c.retired_at.is_(None),
        ]
        if expected_head is not None:
            conditions.append(NOTE.c.head_revision_id == expected_head)
        stmt = (
            sa.update(NOTE)
            .where(*conditions)
            .values(
                retired_at=now_ms, replaced_by=replaced_by, retire_reason=because,
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
            if result.rowcount:
                await memory_index.project(conn, "note", target)
        return int(result.rowcount)

    async def merge_into(
        self,
        *,
        unit: str,
        survivor_id: str,
        survivor_head: str,
        duplicate_id: str,
        duplicate_head: str,
        body: str,
        anchor_date: int | None,
        revisit_at: int,
        reset_strikes: bool,
        established_by: str,
        now_ms: int,
        instruction_sha256: str | None = None,
        model: str | None = None,
        on_reminder_outcome: Callable[[str], None] | None = None,
    ) -> NoteRevision:
        """Fold one duplicate into its survivor, in ONE transaction (guard 2). A fold is two writes
        that must land together.

        1. The survivor gets `body` as a new head revision (parent
           `survivor_head`), takes `anchor_date` and `revisit_at`, and has its
           strikes reset when `reset_strikes`. Compare-and-swap on its head
           AND `retired_at IS NULL`.
        2. The duplicate is retired `retired-merged` with `replaced_by` the
           survivor. Compare-and-swap on ITS head and `retired_at IS NULL`.
        3. Either note's open proactive reminder is resolved onto the
           survivor's single slot — see `_repoint_proactive_rows` below. The
           index keys on the RAW note id a row's `payload` carries, so
           two notes that each held an open row never collided AT the index:
           the fold is what makes them the same note, and nothing upstream of
           this method re-checks the row once that happens. Without this step
           both rows go on forwarding to the survivor through
           `nora.app.proactive_screen`'s `successor()` chase and both fire —
           reachable whenever the Cognition session's `note_merge` calls this
           method. A re-pointed row also gets `revision_id` moved onto THIS
           fold's own new revision — see `_repoint_stmt`'s docstring for why.
           Both notes' rows are classified first, by the
           same shared check the fire-time screen uses
           (`proactive_row_forwarded_is_moved` for the duplicate's, the
           ordinary walk for the survivor's): a stale one is cancelled
           `cancelled_note_moved` and never chosen as the keeper. The order
           and why are in `_repoint_proactive_rows`'s docstring.

        `on_reminder_outcome`, when given, is called once per reminder this
        step touches, naming what happened (`_repoint_proactive_rows`'s
        docstring lists the vocabulary) — `nora.language.note_merge` passes a
        closure that increments `nora_note_merge_proactive_total{outcome}`.
        This store still takes no `Metrics` itself (module docstring: "no
        config, no clock, no metrics"); a plain callback is what lets the
        caller own that observation without this store importing
        `nora.obs.metrics` or `nora.language`.

        Either of the first two swaps matching no row raises
        `StaleNoteHeadError` and the transaction rolls back, so nothing is
        half-folded: no appended revision on a survivor whose duplicate is
        still open, no retired duplicate whose question reached nothing, and
        — because step 3 runs after both and inside the same `begin()` — no
        reminder touched either. The duplicate's row and its whole chain are
        kept, so clearing `retired_at`/`replaced_by` restores it exactly;
        a cancelled reminder is not
        restored by that alone, matching supersede's own retirement (a status
        change, never a delete, but not self-inverting on the row it cancels).

        `body` is built by the caller (`nora.domain.notes.merged_body`) and
        constructed as a `NoteRevision` BEFORE the transaction opens, so an
        over-long or blank body refuses identically on both implementations.

        **Reaches `REMINDERS` directly rather than going through
        `note_merge.py`'s call site.** Both tables are `nora.infra.sql`
        siblings — no layering rule separates them (`nora.language`, where
        the call site lives, sits ABOVE `nora.infra` and has no access to
        this method's connection) — and the one property step 3 cannot give
        up is running inside the SAME transaction as steps 1 and 2: a
        collision between two now-open rows can only be prevented by writing
        the cancel before the re-point WITHIN one transaction (the unique
        index is not deferrable, so it is checked at each statement, not at
        commit). A call site one transaction later would either re-derive
        that ordering non-atomically or accept a window where a merge landed
        and both rows were still live.
        """
        if survivor_id == duplicate_id:
            raise ValueError("a note cannot be folded into itself")
        revision = NoteRevision(
            id=str(uuid4()), note_id=survivor_id, body=body,
            parent_revision_id=survivor_head, written_at=now_ms,
            established_by=established_by,
            instruction_sha256=instruction_sha256, model=model,
        )
        if not all(_is_uuid(v) for v in (
            survivor_id, survivor_head, duplicate_id, duplicate_head,
        )):
            raise StaleNoteHeadError(
                "a note id this store could not look up — read it again",
                observed_head=None,
            )
        survivor_values: dict[str, Any] = {
            "head_revision_id": revision.id,
            "anchor_date": anchor_date,
            "revisit_at": revisit_at,
        }
        if reset_strikes:
            survivor_values["strikes"] = 0
        async with self._engine.begin() as conn:
            # LINEAGE (migration `0073_lineage`): the survivor now rests on
            # everything either note rested on, and on what the folded body
            # cites.
            derived: dict[str, Any] = {}
            survivor = await _head_lineage(conn, unit=unit, note_id=survivor_id)
            duplicate = await _head_lineage(conn, unit=unit, note_id=duplicate_id)
            if survivor is not None:
                origin, held, held_as_of = survivor
                lineage, cited_at = await _revision_lineage(
                    conn, body=revision.body, origin=origin, first=False, unit=unit,
                )
                as_of = cited_at if cited_at is not None else revision.written_at
                derived = {"lineage": lineage.to_json(), "as_of": as_of}
                union = held.union(lineage)
                newest = _newest(held_as_of, as_of)
                if duplicate is not None:
                    union = union.union(duplicate[1])
                    newest = _newest(newest, duplicate[2])
                survivor_values |= {"lineage": union.to_json(), "as_of": newest}
            swapped = await conn.execute(
                sa.update(NOTE)
                .where(
                    NOTE.c.id == survivor_id,
                    NOTE.c.unit == unit,
                    NOTE.c.head_revision_id == survivor_head,
                    NOTE.c.retired_at.is_(None),
                )
                .values(**survivor_values)
            )
            if swapped.rowcount == 0:
                raise StaleNoteHeadError(
                    f"note {survivor_id} moved or retired before the fold — "
                    f"read it again",
                    observed_head=None,
                )
            await conn.execute(sa.insert(NOTE_REVISION).values(
                id=revision.id, note_id=revision.note_id, body=revision.body,
                parent_revision_id=revision.parent_revision_id,
                written_at=revision.written_at,
                established_by=revision.established_by,
                instruction_sha256=revision.instruction_sha256,
                model=revision.model,
                citations=list(revision.citations),
                **derived,
            ))
            retired = await conn.execute(
                sa.update(NOTE)
                .where(
                    NOTE.c.id == duplicate_id,
                    NOTE.c.unit == unit,
                    NOTE.c.head_revision_id == duplicate_head,
                    NOTE.c.retired_at.is_(None),
                )
                .values(
                    retired_at=now_ms, replaced_by=survivor_id,
                    retire_reason=NoteSupersedeReason.RETIRED_MERGED,
                )
            )
            if retired.rowcount == 0:
                # Raising inside `begin()` rolls back the survivor's swap and
                # the revision above: the fold is all or nothing.
                raise StaleNoteHeadError(
                    f"note {duplicate_id} moved or retired before the fold — "
                    f"read it again",
                    observed_head=None,
                )
            await self._repoint_proactive_rows(
                conn, survivor_id=survivor_id, duplicate_id=duplicate_id,
                duplicate_head=duplicate_head, revision_id=revision.id,
                now_ms=now_ms, on_reminder_outcome=on_reminder_outcome,
            )
            # The survivor's new head, and the duplicate retired into it (its
            # `supersedes` edge from the survivor).
            await memory_index.project(conn, "note", survivor_id)
            await memory_index.project(conn, "note", duplicate_id)
        return revision

    @staticmethod
    async def _repoint_proactive_rows(
        conn: AsyncConnection, *, survivor_id: str, duplicate_id: str,
        duplicate_head: str, revision_id: str, now_ms: int,
        on_reminder_outcome: Callable[[str], None] | None,
    ) -> None:
        """The merge's reminders half inside the same
        transaction as `merge_into`'s two swaps above. **Order is
        load-bearing** (choosing a keeper BEFORE checking staleness could
        cancel the wrong row, or classify the duplicate's row as live from
        only half the check a forwarded row gets):

        (a) The duplicate's row (if any), UNLESS it is `firing`, is
            classified `moved`/`live` by `proactive_row_forwarded_is_moved`
            — the SAME two-step check (the duplicate's own chain, then the
            survivor's) `ProactiveScreen._follow_survivor` runs at fire
            time on an un-re-pointed forwarded row, so a row this method
            resolves and a row it leaves for the next tick agree. A
            `firing` row is never classified at all — see (below) — it is
            already being delivered.
        (b) `moved` is CANCELLED `cancelled_note_moved` here and now,
            eliminated as a keeper candidate. Nothing else runs: if the
            survivor also held an open row, it is left EXACTLY as it was —
            it was never stale, never part of a real collision, and its own
            real fire time will evaluate it correctly on its own terms.
        (b2) The SURVIVOR's own row (if any), UNLESS it is `firing`, is
            classified by the ordinary walk a live note's row gets at fire
            (`tag_path_progressed_since_wake` from the survivor's head, now
            the fold's revision, back to the row's own `revision_id`). A
            stale one is CANCELLED `cancelled_note_moved` and drops out of
            (c) — otherwise a stale survivor row due first wins the
            tie-break, the live duplicate row is cancelled, and nothing
            fires.
        (c) Among what is left standing (the duplicate's row if it was
            `firing` or classified `live`, and the survivor's row if it
            has one), if BOTH exist, exactly one is KEPT — a `firing` row
            is never given up for a merely `pending` one, otherwise the
            earliest `due_at` wins, `created_at` then `id` breaking a tie
            (`_keep_open_row`). **0064's own boot-time tie-break is the
            OPPOSITE** (pending survives firing) and that is not a
            contradiction — 0064 runs before the recovery sweep
            (`sweep_firing`) fails every row a dead process left `firing`;
            here, mid-transaction on a live note, a `firing` row is not
            orphaned, it is a row the heartbeat holds right now, and
            cancelling it would be the double-send `sweep_firing`'s own
            docstring exists to prevent. **Unless the LOSER is also
            `firing`** (both rows were firing; the tie fell to `due_at`) —
            then BOTH are left ENTIRELY alone: re-pointing the keeper while
            the loser still holds `survivor_id` open (never cancelled)
            would collide with the loser at the re-point statement itself.
            The loser (when not `firing`) is CANCELLED `cancelled_
            duplicate_note`, migration 0064's own outcome for the identical
            condition, found at boot instead of made live by a fold.
        (d) The keeper, if it is the duplicate's row, is RE-POINTED —
            `note_id` AND `revision_id`, both onto the fold's own new
            revision (`_repoint_stmt`'s docstring says why `revision_id`
            has to move too). If the keeper is already the survivor's own
            row, nothing more to do.
        (e) **Cancel before re-point, never the reverse**, in (c)/(d):
            `uq_reminders_proactive_open_note` is a plain unique index, not
            `DEFERRABLE INITIALLY DEFERRED`, so Postgres checks it at the
            end of EACH statement. Re-pointing the duplicate's row onto the
            survivor's id while the survivor's own row is still open would
            collide inside this very transaction; freeing that slot first
            is what makes the second statement legal.

        Outcomes an `on_reminder_outcome` callback receives, one call per
        event: `moved` (b), `skipped_firing_collision` (c's both-firing
        exemption), `repointed` (d, no collision), `collision_repointed`
        (d, after a collision), `collision_kept_survivor` (c resolved in
        the survivor's favour, no re-point needed).
        """
        rows = (await conn.execute(
            sa.select(
                REMINDERS.c.id, REMINDERS.c.state, REMINDERS.c.due_at,
                REMINDERS.c.created_at,
                REMINDERS.c.payload["note_id"].astext.label("note_id"),
                REMINDERS.c.payload["revision_id"].astext.label("revision_id"),
            )
            .where(
                REMINDERS.c.kind == PROACTIVE_KIND,
                REMINDERS.c.state.in_(OPEN_STATES),
                REMINDERS.c.payload["note_id"].astext.in_(
                    (survivor_id, duplicate_id)
                ),
            )
            .with_for_update()
        )).all()
        by_note = {r.note_id: r for r in rows}
        d_row = by_note.get(duplicate_id)
        if d_row is None:
            return
        fired_at = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
        if d_row.state != ReminderState.FIRING.value:
            moved = proactive_row_forwarded_is_moved(
                duplicate_head=duplicate_head,
                duplicate_chain=await SqlNoteStore._chain_by_id(conn, duplicate_id),
                revision_id=d_row.revision_id,
                created_at_ms=int(d_row.created_at.timestamp() * 1000),
                survivor_chain=await SqlNoteStore._chain_pairs(conn, survivor_id),
            )
            if moved:
                result = await conn.execute(_cancel_stmt(
                    d_row.id, d_row.state, CANCELLED_NOTE_MOVED, fired_at,
                ))
                _require_row_changed(
                    result, what=f"cancel reminder {d_row.id} (cancelled_note_moved)",
                )
                log.info(
                    "merge_into: reminder %s cancelled cancelled_note_moved "
                    "— the duplicate's or the survivor's tag path wrote "
                    "after the row was linked", d_row.id,
                )
                if on_reminder_outcome is not None:
                    on_reminder_outcome("moved")
                return
        s_row = by_note.get(survivor_id)
        if s_row is not None and s_row.state != ReminderState.FIRING.value and (
            tag_path_progressed_since_wake(
                head_revision_id=revision_id,
                last_wake_revision_id=s_row.revision_id,
                chain=await SqlNoteStore._chain_by_id(conn, survivor_id),
            )
        ):
            # The SURVIVOR's own row is stale — the ordinary fire-time walk
            # on a live note would cancel it `moved`. Classify it before the
            # keeper is chosen, or a stale survivor row can win the
            # tie-break over a live duplicate row. The
            # walk starts at `revision_id`, the fold's own revision, now the
            # survivor's head; the fold itself is not a tag-path write.
            result = await conn.execute(_cancel_stmt(
                s_row.id, s_row.state, CANCELLED_NOTE_MOVED, fired_at,
            ))
            _require_row_changed(
                result, what=f"cancel reminder {s_row.id} (cancelled_note_moved)",
            )
            log.info(
                "merge_into: reminder %s cancelled cancelled_note_moved — "
                "the survivor's tag path wrote after its own row was linked",
                s_row.id,
            )
            if on_reminder_outcome is not None:
                on_reminder_outcome("moved")
            s_row = None
        if s_row is None:
            await SqlNoteStore._repoint_row(
                conn, row=d_row, survivor_id=survivor_id,
                revision_id=revision_id, on_reminder_outcome=on_reminder_outcome,
                outcome="repointed",
            )
            return
        keep, drop = _keep_open_row(d_row, s_row)
        if drop.state == ReminderState.FIRING.value:
            # BOTH rows are firing (the only way `_keep_open_row` hands back
            # a firing `drop`, since a live-or-firing `d_row` never lost
            # this point above). See (c) in the docstring for why the
            # keeper is left untouched too, not just the loser.
            log.info(
                "merge_into: reminders %s and %s both stay firing, "
                "untouched — both were in flight; the heartbeat's own "
                "mark() resolves each", drop.id, keep.id,
            )
            if on_reminder_outcome is not None:
                on_reminder_outcome("skipped_firing_collision")
            return
        result = await conn.execute(_cancel_stmt(
            drop.id, drop.state, CANCELLED_DUPLICATE_NOTE, fired_at,
        ))
        _require_row_changed(
            result, what=f"cancel reminder {drop.id} (cancelled_duplicate_note)",
        )
        log.info(
            "merge_into: reminder %s cancelled cancelled_duplicate_note "
            "— note %s already had an open reminder", drop.id, survivor_id,
        )
        if keep.id == d_row.id:
            await SqlNoteStore._repoint_row(
                conn, row=keep, survivor_id=survivor_id,
                revision_id=revision_id, on_reminder_outcome=on_reminder_outcome,
                outcome="collision_repointed",
            )
        elif on_reminder_outcome is not None:
            on_reminder_outcome("collision_kept_survivor")

    @staticmethod
    async def _chain_by_id(
        conn: AsyncConnection, note_id: str,
    ) -> dict[str, tuple[str | None, str]]:
        """One note's whole chain as `tag_path_progressed_since_wake`/
        `proactive_row_forwarded_is_moved` want it: `id -> (parent, established_by)`."""
        rows = (await conn.execute(
            sa.select(
                NOTE_REVISION.c.id, NOTE_REVISION.c.parent_revision_id,
                NOTE_REVISION.c.established_by,
            ).where(NOTE_REVISION.c.note_id == note_id)
        )).all()
        return {r.id: (r.parent_revision_id, r.established_by) for r in rows}

    @staticmethod
    async def _chain_pairs(
        conn: AsyncConnection, note_id: str,
    ) -> list[tuple[int, str]]:
        """One note's whole chain as `proactive_row_forwarded_is_moved`'s
        `survivor_chain` wants it: `(written_at, established_by)` pairs —
        no ancestry needed for that half of the check, a scan by time.
        Includes the fold's own new revision when `note_id` is the
        survivor and it has already been inserted: harmless, since its
        `established_by` is `dream:note-merge:...` (the deleted pass's) or the
        Cognition session's, never tag-path."""
        rows = (await conn.execute(
            sa.select(NOTE_REVISION.c.written_at, NOTE_REVISION.c.established_by)
            .where(NOTE_REVISION.c.note_id == note_id)
        )).all()
        return [(r.written_at, r.established_by) for r in rows]

    @staticmethod
    async def _repoint_row(
        conn: AsyncConnection, *, row: Row[Any], survivor_id: str,
        revision_id: str, on_reminder_outcome: Callable[[str], None] | None,
        outcome: str,
    ) -> None:
        """The re-point statement alone — `row` has already been classified
        `live` (or is `firing`, never classified) by the caller. Translates
        a concurrent collision into `StaleNoteHeadError` and checks
        `rowcount`; see `_repoint_stmt`'s docstring for why both keys move."""
        try:
            result = await conn.execute(
                _repoint_stmt(row.id, row.state, survivor_id, revision_id)
            )
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != _PG_UNIQUE_VIOLATION:
                raise
            # A `remind` call landed on the survivor between this
            # transaction's own `SELECT ... FOR UPDATE` above (which locks
            # only rows that existed then) and this UPDATE — the one window
            # `FOR UPDATE` cannot close, since it takes no lock on a row
            # that does not exist yet. `StaleNoteHeadError` is what
            # `nora.language.note_merge._fold` already catches to record
            # `stale`, so this reaches the SAME refusal path as a head that
            # moved, rather than an unhandled `IntegrityError` taking the
            # pass down.
            raise StaleNoteHeadError(
                f"note {survivor_id} gained a new open proactive reminder "
                f"while this fold was re-pointing one onto it — read it "
                f"again",
                observed_head=None,
            ) from exc
        _require_row_changed(result, what=f"re-point reminder {row.id}")
        log.info(
            "merge_into: reminder %s re-pointed to survivor %s (revision %s)",
            row.id, survivor_id, revision_id,
        )
        if on_reminder_outcome is not None:
            on_reminder_outcome(outcome)

    # ---- the create path -----------------------------------

    @staticmethod
    def _head_join() -> sa.Select[Any]:
        """`note` joined to its HEAD revision and nothing else — the one place
        `note.head_revision_id = note_revision.id` is written, shared by `read`
        and `search` so the two cannot drift into disagreeing about which
        revision is current. The revision's columns are aliased (`rev_id`,
        `rev_note_id`) because `id` and `note_id` collide with `note`'s own.
        """
        return sa.select(
            NOTE,
            NOTE_REVISION.c.id.label("rev_id"),
            NOTE_REVISION.c.note_id.label("rev_note_id"),
            NOTE_REVISION.c.body,
            NOTE_REVISION.c.parent_revision_id,
            NOTE_REVISION.c.written_at,
            NOTE_REVISION.c.established_by,
            # Projected even though nothing renders them: `_row_to_revision`
            # builds the whole domain object from this row, and a mapper that
            # silently omits a field returns an object whose provenance reads as
            # absent for every caller rather than only for the rows that have
            # none. `0036` made these nullable precisely so `None` MEANS
            # something, which stops being true if the query can produce it too.
            NOTE_REVISION.c.instruction_sha256,
            NOTE_REVISION.c.model,
        ).join_from(
            NOTE, NOTE_REVISION, NOTE.c.head_revision_id == NOTE_REVISION.c.id,
        )
