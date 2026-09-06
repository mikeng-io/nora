"""`SqlNoteStore` — the note tier's store, over SQLAlchemy Core and the four
tables migration `0026_note_tier` lands
(docs/superpowers/specs/2026-08-21-note-tier-design.md).

Still no writer above it: `nora.agent` has no `note_create`/`note_update`
tools yet and `nora.dreamer` has no note adaptor, so this class exists today to
be exercised by `tests/contract/test_note_store_contract.py`, and to be wired
into both later without changing shape.

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
   those two statements is the window the CAS exists to close: the curator
   merging the note mid-turn, or a parallel turn in the other room of the same
   workspace. `UPDATE note SET head_revision_id = :new WHERE id = :id AND
   workspace = :ws AND head_revision_id = :parent` is atomic in Postgres, so a
   `rowcount` of 0 IS the refusal, decided by the database rather than by a
   racing reader. On 0 the transaction rolls back and `StaleNoteHeadError` is
   raised: nothing is written on top, ever.

3. **Every read carries `WHERE workspace = :workspace`.** Unreachable, not
   merely unrendered. `read` on a note in another workspace returns `None`,
   exactly as if it did not exist — a caller must not be able to distinguish
   "no such note" from "not yours", because the distinction alone confirms the
   note exists. `append_revision` refuses across the boundary for the same
   reason, and reports `observed_head = None` rather than the real head.

**Search joins the head, and that join is the property §5.3 actually wanted.**
The design says the trigram index is "maintained on the head revision only". It
cannot be, as a partial index: which revision is the head is a fact about the
`note` row, not a predicate over `note_revision`, so there is nothing for
`postgresql_where` to test (migration 0026's own docstring records this
deviation). `idx_note_revision_body_trgm` therefore covers superseded bodies
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
`hotel` (measured: `body % 'venue2999'` matched 0 of 3000 rows that all contain
the string). Substring containment is the question `note_search` is actually
asked, so `ilike '%…%'` is the operator, with the caller's query escaped.

**What the index actually does, read off `EXPLAIN (ANALYZE)` on PostgreSQL 18.4,
3000 notes, 2026-08-21** — rather than assumed, since an index nobody measured is
a claim, not a plan:

- The GIN index IS reachable through a **parameterised** `ilike '%' || $1 ||
  '%'`, not only a literal pattern. SQLAlchemy emits exactly that, so this
  store's own query is the one that was measured, not a hand-written stand-in.
- For a query of **three or more characters** it narrows properly: the bitmap
  index scan returned 1 row of 3000 and touched 1 heap block.
- For a query of **two characters** — `酒店`, and this is the realistic Cantonese
  case — pg_trgm cannot extract a complete trigram, so the bitmap index scan
  returns *every* row (3000) and the recheck discards 2999. The answer is still
  correct; the index simply does no work. That is a knowable limitation of
  trigrams, and it is the price of the alternative being *wrong*: a
  `to_tsvector('english', …)` does not segment Chinese at all, so the same query
  returns nothing while reporting success (§5.3).
- At this tier's real size the planner **chooses a sequential scan anyway** — a
  few thousand short bodies is 62 pages, cheaper to read than any index. The
  index earns its keep later, and costs nothing now. So the property this store
  actually depends on for correctness is `ilike`'s substring semantics, which
  hold with or without the index; the index is a performance affordance, and
  nothing here reads a null result as evidence of absence because of it.

**Entity resolution's DETERMINISTIC half now runs here (issue #100), mirroring
`SqlFactStore._resolve_subjects`'s shape rather than its verb.** `anchor_
entity_ids` is still written verbatim to `note.anchor_entity_ids` — that column
answers "what did the writer type" and stays free text on purpose. Separately,
every distinct string in it is resolved against `fact_entity`'s canonical
names and aliases — EXACT match after normalisation, via `SqlEntityStore.
resolve`, never `resolve_or_create`: this tier only ever LINKS a name to an
entity that already exists (the fact tier is what creates one), and never
mints one of its own. A match writes a `note_entity` row, in the SAME
transaction as the note/revision it belongs to; no match leaves the string
unresolved — a candidate for
`nora.language.note_entity_adaptor.NoteEntityResolutionAdaptor`'s judgement
pass, or simply unresolved, which is the existing fail-closed default and not a
regression. `entities` is optional and injected (`SqlEntityStore | None`,
`None` by default) for the same reason `SqlFactStore.__init__` states: a
contract-suite fixture built from a bare `AsyncEngine` still works, and pays
for it by leaving every note's entities unresolved.

Both writers resolve: `create` (the tag path's `note_create`) and `propose`'s
`NoteCreationProposal` branch (the debrief and the create-candidate gate,
issue #98). `propose`'s `NoteRevisionProposal` branch does not — a revision
never changes `anchor_entity_ids` (there is no column for it to write), and
`NoteRevisionProposal.entity_ids` is not resolved here; it is unused by this
store today, exactly as it arrived.

**`merge_groups` reads `note_entity`, and now something is actually writing
it.** Merge eligibility is `(anchor_date, resolved entity set)`, and the
resolved half comes from `note_entity` — never from `anchor_entity_ids`, which
is the model's own free text. Before this, `note_entity` had no writer anywhere
and every group was refused; `nora_note_curator_groups_refused_total` is the
number that says how much of that refusal is left. Keying on the raw column
instead would make merge fire, and fire wrongly in both directions: three
spellings of one wedding are three keys, while two unrelated notes that both
say 「wedding」 are one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.notes import (
    CANDIDATE_CREATED,
    CANDIDATE_PENDING,
    CANDIDATE_REFUSED,
    Note,
    NoteCreateCandidate,
    NoteCreationProposal,
    NoteRevision,
    NoteRevisionProposal,
    NoteSupersedeReason,
    StaleNoteHeadError,
    WakeOutcome,
    group_on_anchor_key,
    tag_path_progressed_since_wake,
)
from nora.domain.ports import Proposal
from nora.infra.sql.entities import SqlEntityStore
from nora.infra.sql.schema import (
    NOTE,
    NOTE_CANDIDATE,
    NOTE_ENTITY,
    NOTE_REVISION,
)


def _row_to_note(r: Row[Any]) -> Note:
    return Note(
        id=r.id, workspace=r.workspace, title=r.title, description=r.description,
        uncertainty=r.uncertainty, closing_condition=r.closing_condition,
        established_in=r.established_in, created_at=r.created_at,
        head_revision_id=r.head_revision_id, anchor_date=r.anchor_date,
        anchor_entity_ids=tuple(r.anchor_entity_ids), retired_at=r.retired_at,
        replaced_by=r.replaced_by, retire_reason=r.retire_reason,
        revisit_at=r.revisit_at, strikes=r.strikes,
        last_wake_revision_id=r.last_wake_revision_id, event_id=r.event_id,
        reminder_id=r.reminder_id,
    )


def _row_to_create_candidate(r: Row[Any]) -> NoteCreateCandidate:
    return NoteCreateCandidate(
        id=r.id, workspace=r.workspace, title=r.title, description=r.description,
        body=r.body, closing_condition=r.closing_condition,
        anchor_date=r.anchor_date, anchor_entity_ids=tuple(r.anchor_entity_ids),
        message_ids=tuple(r.message_ids), status=r.status, attempts=r.attempts,
        first_seen_at=r.first_seen_at, last_seen_at=r.last_seen_at,
    )


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
#: broken store, and a store that raises on one turns it into a dead turn —
#: `SqlFactStore.fact_by_id` makes the same call for the same reason.
def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


class SqlNoteStore:
    """`__init__(self, engine: AsyncEngine)` and nothing else BY DEFAULT. No
    config, no clock, no metrics. `now_ms` arrives on `create`/`append_revision`/
    `record_miss`, not on this constructor, so a contract-suite fixture in
    another session can build this class from a bare `AsyncEngine` alone and
    never has to guess what else it needs. `SqlFactStore`'s docstring states the
    same affordance for the same reason, and the two stores' fixtures are
    deliberately interchangeable in shape.

    **`entities` is the one addition (issue #100), and it is optional for the
    same reason `SqlFactStore.entities` is**: a contract-suite fixture still
    builds this from a bare engine, and what that costs is that such a fixture
    writes notes whose anchor entities never resolve — the fail-closed default
    this tier already shipped with, not a new one. When it IS supplied, every
    `create`/`propose` resolves `anchor_entity_ids` against it and writes
    `note_entity` in the same transaction as the note it belongs to; see
    `_resolve_names`.
    """

    def __init__(
        self, engine: AsyncEngine, *, entities: SqlEntityStore | None = None,
    ) -> None:
        self._engine = engine
        self._entities = entities

    async def _resolve_names(
        self, *, workspace: str, names: Sequence[str],
    ) -> dict[str, str]:
        """The deterministic half of entity resolution (21 §5, issue #100):
        normalise, exact-match against `fact_entity`'s canonical name and
        aliases. Returns the subset of `names` that resolved, mapped to the
        entity id each one names.

        **`resolve`, never `resolve_or_create`.** The fact tier's sink creates
        an entity because a fact's subject IS one end of the edge being
        written (§4: entity creation is a side effect of writing an edge, and
        the FACT is the edge). A note's anchor entity is not an edge the note
        tier is asserting — it is a reference to something the fact tier
        already asserts exists. Minting one here would make writing a note
        able to grow the shared substrate from free text nothing has
        adjudicated, which is exactly the "unadjudicated noise must not mint
        nodes" rule one layer over.

        A blank `self._entities` (no collaborator injected) or a blank/
        whitespace-only name resolves to nothing, cheaply, before any lookup —
        the fail-closed default this whole tier already had.
        """
        if self._entities is None:
            return {}
        resolved: dict[str, str] = {}
        for name in dict.fromkeys(names):
            if not name or not name.strip():
                continue
            entity_id = await self._entities.resolve(workspace=workspace, name=name)
            if entity_id is not None:
                resolved[name] = entity_id
        return resolved

    @staticmethod
    async def _attach_note_entities(
        conn: AsyncConnection, pairs: Sequence[tuple[str, str]],
    ) -> int:
        """Write `(note_id, entity_id)` rows into `note_entity`, on the
        CALLER's connection so it lands in the same transaction as the note or
        revision that resolved them (21 §4's atomicity argument, one join
        table over: a `note_entity` row naming a note the transaction rolled
        back would be exactly the dangling edge that argument forbids).

        `ON CONFLICT DO NOTHING` on the table's own composite primary key
        (`note_id`, `entity_id`) — a name resolving to an entity the note is
        already linked to (a revision re-asserting the same anchor, or two
        proposals in one batch naming the same pair) is not a second link,
        it is the same one seen twice.
        """
        rows = list(dict.fromkeys(pairs))
        if not rows:
            return 0
        result = await conn.execute(
            pg_insert(NOTE_ENTITY)
            .values([{"note_id": n, "entity_id": e} for n, e in rows])
            .on_conflict_do_nothing()
        )
        return result.rowcount

    async def create(
        self,
        *,
        workspace: str,
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
        # `note_create` has no `Proposal` and no instruction digest; the
        # debrief, arriving through `DreamSink.propose`, has both. See
        # `0036_note_revision_provenance` for why the columns are nullable.
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
            id=note_id, workspace=workspace, title=title, description=description,
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
        # RESOLVED BEFORE THE TRANSACTION OPENS: `_resolve_names` reads
        # `fact_entity` on its own connection, and the fact-tier sink's own
        # doctrine is that a resolution decided against a name is a READ, not
        # part of the write it feeds — the write half is what needs the
        # transaction, not the lookup that decides its arguments.
        resolved = await self._resolve_names(
            workspace=workspace, names=anchor_entity_ids,
        )
        async with self._engine.begin() as conn:
            await self._create_on_conn(conn, note=note, revision=revision)
            await self._attach_note_entities(
                conn, [(note_id, eid) for eid in dict.fromkeys(resolved.values())],
            )
        return note

    @staticmethod
    def _new_note(
        *, workspace: str, payload: NoteCreationProposal, proposal: Proposal,
        now_ms: int,
    ) -> tuple[Note, NoteRevision]:
        """A `NoteCreationProposal` as the two domain objects it becomes.

        Built BEFORE any insert, like `create` above and for the same reason:
        `__post_init__`'s refusals raise here rather than surfacing as an
        `IntegrityError` from inside a batch, which is what makes the two
        implementations refuse identically.
        """
        note_id = str(uuid4())
        revision_id = str(uuid4())
        return (
            Note(
                id=note_id, workspace=workspace, title=payload.title,
                description=payload.description,
                uncertainty=payload.uncertainty,
                closing_condition=payload.closing_condition,
                established_in=payload.established_in, created_at=now_ms,
                head_revision_id=revision_id, anchor_date=payload.anchor_date,
                anchor_entity_ids=tuple(payload.anchor_entity_ids),
                # §5.3 resolution (c): a gate-created note's first wake is a
                # normal scheduled one when the create path supplied a
                # `revisit_at` (`nora.language.note_candidates`); `None` for
                # the tag path's own creates, which do not go through this
                # proposal type at all.
                revisit_at=payload.revisit_at,
            ),
            NoteRevision(
                id=revision_id, note_id=note_id, body=payload.body,
                parent_revision_id=None, written_at=now_ms,
                established_by=proposal.established_by,
                instruction_sha256=proposal.instruction_sha256,
                model=proposal.model,
            ),
        )

    @staticmethod
    async def _create_on_conn(
        conn: AsyncConnection, *, note: Note, revision: NoteRevision,
    ) -> None:
        """The two inserts, on a connection the CALLER owns — the `create`
        sibling of `_append_on_conn`, extracted for the same reason: which
        transaction a write belongs to is the caller's decision, and `propose`
        needs its whole batch in one."""
        await conn.execute(sa.insert(NOTE).values(
            id=note.id, workspace=note.workspace, title=note.title,
            description=note.description, uncertainty=note.uncertainty,
            closing_condition=note.closing_condition,
            anchor_date=note.anchor_date,
            anchor_entity_ids=list(note.anchor_entity_ids),
            established_in=note.established_in, created_at=note.created_at,
            head_revision_id=note.head_revision_id,
            # `create()`'s own `Note` never sets this (`revisit_at` defaults
            # `None`); `propose()`'s `_new_note` does, for a gate-created note
            # (§5.3 resolution (c)). Written unconditionally rather than only
            # when not-None, so both callers share ONE insert.
            revisit_at=note.revisit_at,
        ))
        await conn.execute(sa.insert(NOTE_REVISION).values(
            id=revision.id, note_id=revision.note_id, body=revision.body,
            parent_revision_id=None, written_at=revision.written_at,
            established_by=revision.established_by,
            instruction_sha256=revision.instruction_sha256,
            model=revision.model,
        ))

    async def standing(self, *, workspace: str) -> tuple[Note, ...]:
        """Every note that has not been retired — the read the injected index
        (§5.1) renders from, and the one note-tier read on the turn's critical
        path. `retired_at is null` matches `idx_note_standing`'s own predicate,
        so this is an index scan rather than a filter over history.

        **Uncapped on purpose** (§5.1): there is no `limit`. A note silently
        missing from the index is invisible to her, and invisible is
        indistinguishable from absent. The pressure valve is the curator
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
            .where(NOTE.c.workspace == workspace, NOTE.c.retired_at.is_(None))
            .order_by(NOTE.c.created_at.asc(), NOTE.c.id.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(_row_to_note(r) for r in rows)

    async def read(
        self, *, workspace: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None:
        """The note and its HEAD revision, or `None`.

        Retired notes are readable — retirement is a status change, not a delete
        (§7.4), and history that cannot be read back cannot re-derive a lossy
        step that went wrong. `standing` is where the retired ones disappear.

        A note in another workspace answers `None`, identically to one that does
        not exist (module docstring (3)). So does a `note_id` that is not even
        UUID-shaped.
        """
        if not _is_uuid(note_id):
            return None
        async with self._engine.connect() as conn:
            row = (await conn.execute(self._head_join().where(
                NOTE.c.id == note_id, NOTE.c.workspace == workspace,
            ))).one_or_none()
        if row is None:
            return None
        return _row_to_note(row), _row_to_revision(row)

    async def append_revision(
        self,
        *,
        workspace: str,
        note_id: str,
        body: str,
        parent_revision_id: str,
        established_by: str,
        now_ms: int,
        instruction_sha256: str | None = None,
        model: str | None = None,
    ) -> NoteRevision:
        """§6.3's compare-and-swap. See the module docstring's (2): the
        comparison IS the `UPDATE`'s `WHERE`, so a `rowcount` of 0 is the
        database refusing, not a reader losing a race it did not know it was in.

        Raises `StaleNoteHeadError` when the head is not `parent_revision_id` —
        including when the note does not exist, is in another workspace, or was
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
                conn, workspace=workspace, revision=revision,
            )
        return revision

    @staticmethod
    async def _append_on_conn(
        conn: AsyncConnection, *, workspace: str, revision: NoteRevision,
    ) -> None:
        """The CAS and the insert, on a connection the CALLER owns.

        Extracted so `propose` can run a whole batch in ONE transaction. It is a
        static method taking `conn` rather than a method opening its own,
        because "which transaction is this in" must be the caller's decision:
        `append_revision` is one write and owns its own; `propose` is a batch
        whose partial application is worse than its failure, and it cannot get
        that if each write commits independently.
        """
        note_id = revision.note_id
        parent_revision_id = revision.parent_revision_id
        revision_id = revision.id
        swapped = await conn.execute(
            sa.update(NOTE)
            .where(
                NOTE.c.id == note_id,
                NOTE.c.workspace == workspace,
                NOTE.c.head_revision_id == parent_revision_id,
            )
            .values(head_revision_id=revision_id)
        )
        if swapped.rowcount == 0:
            # Read the head back only to build the message — the refusal was
            # already decided, above, by the UPDATE that matched no row.
            # This SELECT is inside the same transaction, so what it reports
            # is what the failed CAS actually saw.
            observed = (await conn.execute(
                sa.select(NOTE.c.head_revision_id).where(
                    NOTE.c.id == note_id, NOTE.c.workspace == workspace,
                )
            )).scalar_one_or_none()
            raise StaleNoteHeadError(
                f"note {note_id} changed under you: this revision was "
                f"written against {parent_revision_id}, the head is "
                f"{observed} — read it again"
                if observed is not None
                else f"note {note_id} is not in workspace {workspace!r} — "
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
        ))

    async def search(
        self, *, workspace: str, query: str, limit: int,
    ) -> tuple[tuple[Note, str], ...]:
        """Substring search over HEAD bodies, workspace-scoped, standing only.
        Returns `(note, matching_body)` pairs — the note so the caller can name
        and read it, the body so a hit is legible without a second round trip.

        The `note.head_revision_id = note_revision.id` join is the head-only
        guarantee (module docstring); `retired_at is null` is the same argument
        one step further out, since a retired note is absent from the injected
        index and a hit on one hands her a note whose title she cannot see and
        whose question is already closed.

        A blank query returns `()` rather than the whole notebook: `ilike '%%'`
        matches every body, and «everything» is not a search result. A
        non-positive `limit` clamps to `()` the way `SqlFactStore.standing_facts`
        does — Postgres errors outright on a negative `LIMIT`, and the in-memory
        implementation must not answer `list[:-1]` (all but the last) to the same
        input.

        `autoescape=True` on `icontains` escapes `%` and `_` in the caller's
        query. Without it a query of `%` returns every note in the workspace and
        `100%` returns every note containing `100`, because the caller's text
        would be `ilike`'s own wildcard syntax. The query is data.
        """
        if limit <= 0 or not query.strip():
            return ()
        stmt = (
            self._head_join()
            .where(
                NOTE.c.workspace == workspace,
                NOTE.c.retired_at.is_(None),
                NOTE_REVISION.c.body.icontains(query, autoescape=True),
            )
            .order_by(NOTE.c.created_at.asc(), NOTE.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple((_row_to_note(r), r.body) for r in rows)

    # ---- the wake pass (§9.6) ----------------------------------------------

    async def record_wake(
        self, *, workspace: str, note_id: str, next_revisit_at: int,
        now_ms: int,
    ) -> WakeOutcome | None:
        """Record that the revisitor woke on this note. Returns what happened,
        or `None` if the note is not reachable from `workspace`.

        `revisit_at`, `strikes` and `last_wake_revision_id` have existed since
        `0026` with a CHECK and **no writer anywhere**, so the revisitor's
        `revisit_at` trigger was structurally unreachable in production: every
        note that reached it was due by ANCHOR DATE alone, and an undated note
        was never revisited at all. This is that writer.

        **The comparison IS the decision, so it happens HERE and not in the
        caller — and that is why there is no CAS.** A compare-and-swap would
        have the caller assert a head it read earlier, which is exactly the
        stale judgement this is meant to avoid: between the revisitor's read
        and its write, the tag path may have revised the note, and that
        revision is not an obstacle to record — it is the PROGRESS the reset
        exists to detect. So this reads the head inside the transaction and
        branches on it. `merge` and `propose` take a parent because they WRITE
        content and must not overwrite; this writes only bookkeeping about a
        head it just read.

        §9.6's two arms, and the qualifier that makes the breaker real:

        - **THE TAG PATH WROTE SOMETHING SINCE THE LAST WAKE** — `strikes = 0`,
          `last_wake_revision_id = head`, and `revisit_at` advances. Somebody
          engaged with the uncertainty, which is progress. It is the SPAN since
          the watermark, not the author of the current head: a room revision
          followed by a background one is progress, and asking who wrote the
          head answered «no» to it.
        - **anything else** — `strikes += 1` and **`revisit_at` is NOT
          advanced**, so the note stays due and the count climbs toward the
          ceiling the caller enforces.

        The qualifier is not decoration. If a background pass's own writes
        counted as movement, a note the revisitor keeps rewriting would reset
        its own strike forever — a self-referential reset is not a reset, and
        the spec says the mechanism "reads correct without the qualifier and
        loops forever with it missing".

        **Not on `DreamSink`.** That port has two verbs and deliberately no
        third; this is a store method the revisitor calls directly, the same
        placement `merge` took and for the same reason.
        """
        if not _is_uuid(note_id):
            return None
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                sa.select(
                    NOTE.c.head_revision_id,
                    NOTE.c.strikes,
                    NOTE.c.last_wake_revision_id,
                )
                .where(
                    NOTE.c.id == note_id,
                    NOTE.c.workspace == workspace,
                    NOTE.c.retired_at.is_(None),
                )
                # The row is write-locked for the rest of the transaction, so
                # the branch below cannot be decided against a head another
                # writer moves before the UPDATE lands.
                .with_for_update()
            )).one_or_none()
            if row is None:
                return None

            # THE SPAN, NOT THE HEAD. This joined `note_revision` on the head
            # and asked who wrote THAT — so a room revision followed by a
            # background one (a merge, a debrief append) read as no progress
            # and left the watermark behind, forever. See
            # `tag_path_progressed_since_wake`.
            #
            # A note's revision chain is short by construction (a body is
            # appended to, not rewritten per turn), so it is walked in code
            # rather than pushed down as a recursive CTE: one shape both stores
            # run, which is what the contract suite compares.
            chain_rows = (await conn.execute(
                sa.select(
                    NOTE_REVISION.c.id,
                    NOTE_REVISION.c.parent_revision_id,
                    NOTE_REVISION.c.established_by,
                ).where(NOTE_REVISION.c.note_id == note_id)
            )).all()
            progressed = tag_path_progressed_since_wake(
                head_revision_id=row.head_revision_id,
                last_wake_revision_id=row.last_wake_revision_id,
                chain={
                    r.id: (r.parent_revision_id, r.established_by)
                    for r in chain_rows
                },
            )
            if progressed:
                values: dict[str, Any] = {
                    "strikes": 0,
                    "last_wake_revision_id": row.head_revision_id,
                    "revisit_at": next_revisit_at,
                }
                strikes = 0
            else:
                # `strikes + 1` computed by the DATABASE, not read-then-write:
                # two ticks racing on one note would otherwise both read N and
                # both write N+1, losing a strike — and losing strikes is
                # exactly how a breaker becomes decorative.
                values = {"strikes": NOTE.c.strikes + 1}
                strikes = int(row.strikes) + 1
            await conn.execute(
                sa.update(NOTE).where(NOTE.c.id == note_id).values(**values)
            )
        return WakeOutcome(strikes=strikes, reset=progressed)

    # ---- merge: the one cross-note write, in one transaction ---------------

    async def merge(
        self,
        *,
        workspace: str,
        survivor: str,
        survivor_parent: str,
        merged_body: str,
        retire: Sequence[str],
        established_by: str,
        instruction_sha256: str | None,
        model: str | None,
        now_ms: int,
    ) -> int:
        """Append `merged_body` to `survivor` AND retire `retire`, atomically.
        Returns notes retired.

        **THIS VERB EXISTS BECAUSE OF A REVIEW FINDING, and the finding was
        that my own justification did not hold.** `propose` was made atomic on
        the argument that «a merge appends the merged body to the survivor,
        then retires the originals, and a half-applied merge is the one outcome
        worse than not running». True — and `supersede` is a SEPARATE verb
        opening its own transaction, so the named scenario stayed fully
        reachable: survivor carrying merged text beside a duplicate nobody
        retired, caller told nothing happened.

        **The alternative was to keep two transactions and argue it is safe**,
        and that argument can actually be made: `merge_groups` reads LIVE rows
        rather than a watermark, so an unretired duplicate is
        still a duplicate next tick and the pass would retry. The ordering is
        also deliberately fail-safe — appending first means a crash duplicates
        content, where retiring first would LOSE it.

        **It was rejected because it is reasoning a future reader has to
        reconstruct.** Both halves are true today and both are properties of
        code somewhere else: the selection read staying derived rather than
        cursored, and the order of two calls in a caller this store does not
        own. A transaction is checkable by looking at one function. The
        retry argument also has a real cost even when it works — re-merging a
        survivor that already holds the content grows the body every tick, and
        §5.2 has no upper bound on body size.

        **Not on `DreamSink`.** That port has two verbs and deliberately no
        third; this is a store method the note curator's `commit` calls
        directly, which is where a tier's own write SEQUENCE belongs
        (`FactAdaptor.commit` does propose-then-supersede-then-retire for the
        same reason).

        The CAS on the survivor is unchanged and unweakened: if the tag path
        revised it while the curator was thinking, the whole merge is refused
        and nothing is retired.
        """
        # THE CAS DECIDES FIRST, and the ORDER of these two refusals is a
        # contract property rather than a style choice.
        #
        # This built the `NoteRevision` up here, before any of it — so an
        # over-cap merged body raised `ValueError` out of the constructor even
        # when the survivor had already moved, while the in-memory double
        # checked the head first and raised `StaleNoteHeadError`. One input,
        # two exception types, and `NoteCuratorAdaptor.commit` branches on
        # exactly that into `merge_refused` («NOT an error, the group is still
        # a group») and `merge_failed` («a fault»). The two stores disagreed
        # about which of two OPPOSITE readings an operator was shown.
        if not _is_uuid(survivor) or not _is_uuid(survivor_parent):
            raise StaleNoteHeadError(
                f"survivor {survivor!r} is not a note id this store could look "
                f"up — read it again",
                observed_head=None,
            )
        # DE-DUPLICATED: `rowcount` from `WHERE id IN (...)` counts each row
        # once, so a caller passing the same id twice gets 1 from Postgres
        # and would have got 2 from a double counting its list. That count
        # is `commit`'s return and becomes `nora_dream_written_total`.
        targets = list(dict.fromkeys(
            n for n in retire if _is_uuid(n) and n != survivor
        ))
        async with self._engine.begin() as conn:
            # The head is read and LOCKED before the revision is constructed,
            # so a lost CAS is reported as a lost CAS whatever else is wrong
            # with the body. `_append_on_conn` re-asserts it in its UPDATE; the
            # row is locked for the rest of this transaction, so that assertion
            # cannot now fail — it is the insert's own guard, kept because the
            # method is shared with `propose`.
            observed = (await conn.execute(
                sa.select(NOTE.c.head_revision_id).where(
                    NOTE.c.id == survivor, NOTE.c.workspace == workspace,
                ).with_for_update()
            )).scalar_one_or_none()
            if observed != survivor_parent:
                raise StaleNoteHeadError(
                    f"note {survivor} changed under you: this merge was "
                    f"written against {survivor_parent}, the head is "
                    f"{observed} — read it again"
                    if observed is not None
                    else f"note {survivor} is not in workspace {workspace!r} "
                         f"— read it again",
                    observed_head=observed,
                )
            revision = NoteRevision(
                id=str(uuid4()), note_id=survivor, body=merged_body,
                parent_revision_id=survivor_parent, written_at=now_ms,
                established_by=established_by,
                instruction_sha256=instruction_sha256, model=model,
            )
            # The append FIRST, so a lost CAS refuses before anything is
            # retired. Inside one transaction the order cannot matter for
            # atomicity — it matters for which failure the caller sees, and «I
            # could not revise the survivor» is the honest one.
            await self._append_on_conn(
                conn, workspace=workspace, revision=revision,
            )
            if not targets:
                return 0
            result = await conn.execute(
                sa.update(NOTE)
                .where(
                    NOTE.c.id.in_(targets),
                    NOTE.c.workspace == workspace,
                    NOTE.c.retired_at.is_(None),
                )
                .values(
                    retired_at=now_ms, replaced_by=survivor,
                    retire_reason=NoteSupersedeReason.MERGED,
                )
            )
            return int(result.rowcount)

    # ---- the curator's selection reads (§9.2) ------------------------------
    #
    # Both cost ZERO TOKENS. That is the payoff of anchoring merge on
    # `(anchor_date, entity_set)` rather than on topical similarity (§7.1):
    # candidate detection is a KEY COMPARISON, not a judgement, so **the model
    # is never asked whether two notes should merge.** It is asked only what a
    # body SAYS, which is the one thing a key comparison cannot read.
    #
    # "SQL-only" until 2026-08-28, and the grouping now happens in
    # `nora.domain.notes.group_on_anchor_key` — see `merge_groups` for why
    # leaving SQL made it STRICTER rather than weaker. The property that
    # mattered was never that it ran in the database; it was that it runs in
    # CODE and not in a prompt.

    async def merge_groups(
        self, *, workspace: str, limit: int,
    ) -> tuple[
        tuple[tuple[tuple[Note, str], ...], ...],
        tuple[tuple[Note, ...], ...],
    ]:
        """Standing notes that share an anchor, grouped, most-collided first,
        **each with its head body**.

        **The bodies are the whole point and this returned notes alone until
        2026-08-28.** The curator's merge asks the model to write one body that
        keeps everything every note in the group knew — and it was being shown
        titles, uncertainties and closing conditions, never the content. A
        merged body synthesised from two titles, written over a survivor that
        had accumulated real detail, is content loss produced BY the merge
        verb: «Ada can't do Fridays; Sai Kung ruled out» would survive only
        inside a retired row's revision history, with nothing in the index
        knowing it had existed.

        `anchored_before` beside this one already returned `(Note, body)`. The
        divergence was invisible because the renderer dropped bodies for groups
        too, so the two halves agreed — the same shape as a docstring and a
        test agreeing with each other and not with the requirement.

        **The anchor is `(anchor_date, anchor_entity_ids)` and both halves
        matter.** Two weddings on different dates are two notes; two notes
        about the same date and the same people are one occasion written twice.
        Topical similarity would merge the first pair — same vocabulary, same
        shape, same kind of uncertainty — and that merge is unrecoverable in
        the direction that matters, because two notes wrongly merged have
        already lost which revision belonged to which occasion.

        Notes with NO date anchor are excluded even when their entity sets
        match. An entity-only anchor is not an occasion — «Grant» is not a
        thing that happens once — and `note_entity` is empty in production
        anyway, so an entity-only group today would be a group of notes
        anchored to `{}`, which is every dateless note in the workspace.

        **The entity half comes from `note_entity`, not from
        `note.anchor_entity_ids`.** That column holds whatever free text the
        model handed `note_create`, unresolved and unvalidated, and a key built
        from it is wrong in both directions at once — see
        `group_on_anchor_key`. The join is a third query rather than a widened
        first one because it is needed only for the grouping pass and only for
        notes that have an anchor date.

        `limit` bounds GROUPS, not notes: the caller's budget is bodies read
        per tick, and a group is the unit it reads.
        """
        if limit <= 0:
            return ((), ())
        # GROUPED IN PYTHON ON `Note.anchor_key`, not in SQL on the raw column.
        #
        # `GROUP BY anchor_entity_ids` is order- AND duplicate-sensitive in
        # Postgres: `{ada,ben}` and `{ben,ada}` are different groups, so two
        # notes about the same occasion are missed for an artifact of the order
        # somebody happened to list the people. `anchor_key` exists precisely to
        # say that — «entities are sorted and de-duplicated so the key is a
        # property of WHICH entities rather than of the order» — and until
        # 2026-08-28 it had ZERO call sites while both implementations
        # reimplemented the comparison on the raw array. A property with no
        # caller is a claim, not a rule.
        #
        # The cost is reading the workspace's standing notes rather than pushing
        # the grouping down. That is bounded by the same thing `standing`
        # already is — the index is uncapped by design (§5.1) — and correctness
        # on the key is worth more than a GROUP BY here.
        # TWO QUERIES, and the split is the budget. Grouping needs the ANCHOR
        # of every standing note; only the notes in a surviving group need
        # their BODY. Fetching bodies for the grouping pass would read the
        # whole workspace's prose every tick to answer a question about dates
        # and entity sets — and `nora_note_curator_bodies_read` would report
        # the handful it showed the model while the store had read all of them.
        # The gauge is the tick's budget instrument; a query that makes it lie
        # is worse than an extra round trip.
        anchors_stmt = (
            sa.select(NOTE)
            .where(
                NOTE.c.workspace == workspace,
                NOTE.c.retired_at.is_(None),
                NOTE.c.anchor_date.is_not(None),
            )
            .order_by(NOTE.c.created_at.asc(), NOTE.c.id.asc())
        )
        async with self._engine.connect() as conn:
            anchors = (await conn.execute(anchors_stmt)).all()
            # THE RESOLVED ENTITIES, read from the registry rather than from
            # the model's own words. `note_entity` has no writer anywhere in
            # the system yet, so this map is empty in production and every
            # group is refused — which is 27 §2's decision («populating
            # note_entity is the curator's real precondition, not its flag»)
            # showing up as behaviour instead of as prose.
            resolved: dict[str, list[str]] = {}
            if anchors:
                ent_rows = (await conn.execute(
                    sa.select(NOTE_ENTITY.c.note_id, NOTE_ENTITY.c.entity_id)
                    .where(NOTE_ENTITY.c.note_id.in_([r.id for r in anchors]))
                )).all()
                for er in ent_rows:
                    resolved.setdefault(er.note_id, []).append(er.entity_id)
            # `""` as a placeholder body: `group_on_anchor_key` reads only the
            # anchor, and the bodies are filled in below for the groups that
            # survive. Typed as the same pair so one grouping function serves
            # both callers rather than gaining a bodiless variant.
            eligible, refused = group_on_anchor_key(
                [(_row_to_note(r), "") for r in anchors],
                resolved_entities=resolved,
                limit=limit,
            )
            wanted = [n.id for g in eligible for n, _ in g]
            bodies: dict[str, str] = {}
            if wanted:
                rows = (await conn.execute(
                    self._head_join().where(NOTE.c.id.in_(wanted))
                )).all()
                bodies = {r.id: r.body for r in rows}
        return (
            tuple(
                tuple((n, bodies.get(n.id, "")) for n, _ in g)
                for g in eligible
            ),
            refused,
        )

    async def anchored_before(
        self, *, workspace: str, day: int, limit: int,
    ) -> tuple[tuple[Note, str], ...]:
        """Standing notes whose anchor date is strictly before `day`, oldest
        first, with their head bodies.

        **Retirement is TIME-driven, not evidence-driven**, which is why this
        read exists and why the curator's third fire condition matters more
        here than it does for language: a note whose anchor has passed needs
        retiring even if nobody has touched the notebook in a month. Expiry is
        this tier's decay.

        The body comes back with the note because the model has to read it —
        an anchor passing makes a note ELIGIBLE for retirement, never retired.
        «We moved it to the 16th» leaves the question open, now about the 16th,
        and only the body says so. SQL decides who is asked; the model decides
        what the body means; code writes.

        `day` is a YYYYMMDD calendar day, matching `anchor_date`'s own shape —
        not epoch ms. Passing a timestamp here compares an integer in the
        billions against one in the tens of millions and quietly matches
        everything.
        """
        if limit <= 0:
            return ()
        stmt = (
            self._head_join()
            .where(
                NOTE.c.workspace == workspace,
                NOTE.c.retired_at.is_(None),
                NOTE.c.anchor_date.is_not(None),
                NOTE.c.anchor_date < day,
            )
            .order_by(NOTE.c.anchor_date.asc(), NOTE.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple((_row_to_note(r), r.body) for r in rows)

    async def due_for_revisit(
        self, *, workspace: str, now_ms: int, limit: int,
    ) -> tuple[tuple[Note, str], ...]:
        """Standing notes whose `revisit_at` has arrived, oldest `revisit_at`
        first, with their head bodies — the note-curator merge's `due` read
        (design 34 §4.1/§5.3), now that `record_wake` and the create path
        (§5.3 resolution (c)) are the writers `revisit_at` never had.

        **Deliberately NOT also "or the anchor has passed"**, unlike 34 §4.1's
        prose description of the union. `anchored_before` already selects
        every note whose anchor is behind `day`, unconditionally — restating
        that condition here would make this read redundant with it rather
        than complementary, for no gain: the caller unions the two results
        and de-duplicates by note id exactly as it already excludes a
        merge-group member from the expired render. This read's OWN
        contribution is the notes `anchored_before` cannot see at all — a
        dateless note (no anchor to pass) and an anchored note whose anchor
        has not yet arrived but whose early-close check has (34 §5.3's lead
        window).

        `IS NOT NULL` is load-bearing: a note nothing has ever woken (no
        create-path `revisit_at`, no prior wake, and no anchor for
        `anchored_before` to have caught) has `revisit_at IS NULL` and stays
        out of this read forever — matching `_is_due`'s old arithmetic ("a
        note with neither trigger is not due"), now expressed in SQL rather
        than by scanning `standing()` in Python.

        `limit` is a plain `LIMIT`, not an over-fetch: the caller
        (`nora.language.note_curator`) is the one with a metrics collaborator
        to count a capped tick against, on `anchored_before`'s own precedent
        of leaving that bookkeeping to the adaptor rather than the store.
        """
        if limit <= 0:
            return ()
        stmt = (
            self._head_join()
            .where(
                NOTE.c.workspace == workspace,
                NOTE.c.retired_at.is_(None),
                NOTE.c.revisit_at.is_not(None),
                NOTE.c.revisit_at <= now_ms,
            )
            .order_by(NOTE.c.revisit_at.asc(), NOTE.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple((_row_to_note(r), r.body) for r in rows)

    # ---- read-back, for the shared contract suite --------------------------
    #
    # `DreamSink` has two verbs and DELIBERATELY no third, so there is no read
    # verb on the port and the contract suite supplies a `read_row` adapter per
    # implementation instead. These two are what that adapter is built from.
    # They are NOT a widening of the port: `SqlFactStore.fact_by_id` exists for
    # exactly the same reason and is not on `DreamSink` either.

    async def revision_by_id(self, *, revision_id: str) -> NoteRevision | None:
        """One revision by id, or `None`. **Not workspace-scoped, and that is
        deliberate rather than an omission** — a revision is reached through the
        note that owns it, so scoping happens at `note_by_id` below. Scoping
        here too would mean joining `note` on every call to answer a question
        the caller then asks again.
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

    async def note_by_id(self, *, workspace: str, note_id: str) -> Note | None:
        """One note by id within a workspace, **retired or not**.

        `read` above cannot serve this: it goes through `_head_join`, which is
        the model-facing path, and a caller asking whether a note was retired
        needs the row precisely BECAUSE it is retired. Workspace-scoped like
        every other read here — a caller must not be able to confirm a note
        exists in a workspace it may not read.
        """
        if not _is_uuid(note_id):
            return None
        stmt = sa.select(NOTE).where(
            NOTE.c.id == note_id, NOTE.c.workspace == workspace,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        if row is None:
            return None
        return Note(
            id=row.id, workspace=row.workspace, title=row.title,
            description=row.description, uncertainty=row.uncertainty,
            closing_condition=row.closing_condition,
            established_in=row.established_in, created_at=row.created_at,
            head_revision_id=row.head_revision_id,
            anchor_date=row.anchor_date,
            anchor_entity_ids=tuple(row.anchor_entity_ids or ()),
            retired_at=row.retired_at, replaced_by=row.replaced_by,
            retire_reason=row.retire_reason,
            revisit_at=row.revisit_at, strikes=row.strikes,
            last_wake_revision_id=row.last_wake_revision_id,
            event_id=row.event_id, reminder_id=row.reminder_id,
        )

    # ---- DreamSink (the curator's write boundary) --------------------------

    async def propose(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> Sequence[str]:
        """`DreamSink.propose`. `key` is the WORKSPACE (§3/DEC-105).

        **ONE TRANSACTION FOR THE WHOLE BATCH, and this is a correction.** The
        first version looped over `append_revision`, each of which opened its
        own transaction, so a batch was N independent writes — while this
        docstring already claimed the opposite as a requirement. Found by cold
        review (2026-08-27), and the contradiction was between two things I
        wrote: the store was partial, and a test I added pinned the partial
        behaviour as intended. **That is a test encoding the defect as the
        spec** — the second instance of that shape in one day.

        The requirement is real and is why the fix went this way rather than
        the docstring being softened: the curator's merge is «append the merged
        body to the survivor, then retire the originals». If the append commits
        and the retirement does not, the notebook holds a survivor carrying
        merged text beside a duplicate nobody retired, and the caller was told
        nothing happened. **A partially applied cross-note edit is the one
        outcome worse than not running**, because it is invisible: no error, no
        count, and an index that looks ordinary.

        **Type checks run BEFORE the first write**, for the same reason. A bad
        payload at index 1 used to land after index 0 had committed, which is
        the accept-and-discard failure this tier's own axis caught one verb
        over — the call reports one thing and the store holds another.

        **Heads chain within the batch.** Two proposals naming the same note
        used to be a guaranteed `StaleNoteHeadError` — the first moved the head,
        the second named the old one — which made a behaviour this docstring
        CLAIMED ("two proposals against one note return that id twice")
        unreachable. It also quietly weakened the shared suite: the note
        factory had to mint a fresh note per call, so
        `test_propose_returns_one_id_per_proposal_in_order`'s distinctness
        assertion held for a reason unrelated to what it asserts. The batch now
        carries the head each proposal moved and the next against that note
        CASes against it, which makes the claim true and the assertion
        real. The compare-and-swap is unweakened
        — it still refuses anything the batch did not itself write, which is
        exactly the tag path revising mid-tick.

        **Refuses a foreign payload by `isinstance`, never duck-typing it**
        (§15.4): `payload` is `object` at the port so the runtime never names an
        adaptor's value type, and this check is where that opacity is paid for.
        """
        # (1) VALIDATE EVERYTHING FIRST. Nothing below writes until every
        # payload is known to be a shape this store handles.
        # Built as a typed list rather than checked in place: the loop below
        # then narrows on a real union instead of on `object`, so the shape it
        # depends on is proved by the type checker rather than asserted.
        typed: list[tuple[Proposal, NoteCreationProposal | NoteRevisionProposal]] = []
        for p in proposals:
            payload = p.payload
            if not isinstance(
                payload, NoteCreationProposal | NoteRevisionProposal
            ):
                raise TypeError(
                    f"SqlNoteStore.propose accepts a NoteRevisionProposal "
                    f"(revise a note that exists) or a NoteCreationProposal "
                    f"(start carrying a new one), got "
                    f"{type(payload).__name__}"
                )
            typed.append((p, payload))

        # RESOLVED BEFORE THE TRANSACTION OPENS, for the WHOLE BATCH — the
        # same ordering `create` uses and for the same reason (issue #100): a
        # lookup against `fact_entity` is a READ, not part of the write it
        # feeds. Indexed by position in `typed` rather than by note id, since
        # a creation's note id does not exist until `_new_note` mints it
        # inside the transaction below.
        resolved_by_index: list[dict[str, str]] = [
            await self._resolve_names(
                workspace=key, names=payload.anchor_entity_ids,
            )
            if isinstance(payload, NoteCreationProposal) else {}
            for _p, payload in typed
        ]

        ids: list[str] = []
        # The head each note reached WITHIN this batch, so a second proposal
        # against the same note CASes against what the first one wrote rather
        # than against a head that is already stale by construction.
        heads: dict[str, str] = {}
        # What each note's FIRST proposal in this batch claimed to be written
        # against. Distinct from `heads`, which is what the batch has since
        # written — see the check below.
        declared: dict[str, str] = {}
        async with self._engine.begin() as conn:
            for i, (p, payload) in enumerate(typed):
                if isinstance(payload, NoteCreationProposal):
                    note, first = self._new_note(
                        workspace=key, payload=payload, proposal=p,
                        now_ms=now_ms,
                    )
                    await self._create_on_conn(conn, note=note, revision=first)
                    await self._attach_note_entities(
                        conn,
                        [
                            (note.id, eid) for eid in
                            dict.fromkeys(resolved_by_index[i].values())
                        ],
                    )
                    heads[note.id] = first.id
                    ids.append(note.id)
                    continue
                # THE DECLARED PARENT IS CHECKED, NOT OVERRIDDEN. Until
                # 2026-08-27 the tracked head was used as a silent override, so
                # a second proposal declaring ANY parent — including a bogus
                # one — landed as long as the batch had already touched that
                # note. The scenario that makes it real is the likely one: a
                # curator emitting two proposals both parented on the head it
                # READ, with different bodies, which is what a model
                # duplicating its own output produces. Before chaining that was
                # a StaleNoteHeadError; with a silent override both land, the
                # second body wins, the first is buried, and the caller is told
                # both succeeded — a silent overwrite on the one verb whose
                # docstring is about not half-applying.
                #
                # A caller cannot name the intermediate revision (it does not
                # exist until this batch writes it), so re-declaring the SAME
                # parent is the only legitimate thing a second proposal can do.
                # Anything else is a stale read and is refused as one.
                if payload.note_id in declared:
                    if payload.parent_revision_id != declared[payload.note_id]:
                        raise StaleNoteHeadError(
                            f"note {payload.note_id} was already revised in "
                            f"this batch against "
                            f"{declared[payload.note_id]}; this proposal names "
                            f"{payload.parent_revision_id} — read it again",
                            observed_head=heads.get(payload.note_id),
                        )
                else:
                    declared[payload.note_id] = payload.parent_revision_id
                revision = NoteRevision(
                    id=str(uuid4()), note_id=payload.note_id,
                    body=payload.body,
                    parent_revision_id=heads.get(
                        payload.note_id, payload.parent_revision_id,
                    ),
                    written_at=now_ms, established_by=p.established_by,
                    instruction_sha256=p.instruction_sha256, model=p.model,
                )
                if not _is_uuid(revision.note_id) or not _is_uuid(
                    revision.parent_revision_id or "",
                ):
                    raise StaleNoteHeadError(
                        f"note {revision.note_id!r} is not a note id this "
                        f"store could look up — read it again",
                        observed_head=None,
                    )
                await self._append_on_conn(
                    conn, workspace=key, revision=revision,
                )
                heads[revision.note_id] = revision.id
                # **THE NOTE's id, not the revision's.** The port's rationale
                # decides it: `propose` returns ids so `supersede` can NAME what
                # it produced, and this tier's `supersede` retires a NOTE.
                # Revision ids would satisfy the sentence and break its purpose.
                ids.append(revision.note_id)
        return tuple(ids)

    async def supersede(
        self,
        *,
        key: str,
        target: str,
        replaced_by: str | None,
        because: str,
        source: str | None,
        now_ms: int,
    ) -> int:
        """`DreamSink.supersede` — retirement, which is a status change and
        never a delete. Returns rows affected.

        **A null `replaced_by` is LEGAL here, and that is the whole reason the
        port is permissive.** `SqlFactStore.supersede` raises on it, because
        `fact`'s `(superseded_by is null) = (superseded_at is null)` CHECK makes
        a fact either standing or superseded-with-a-successor. A note is not: an
        anchor date passed and the dinner happened, and it is replaced by
        nothing. Copying the fact store's refusal here would force the curator
        to mint a placeholder id to satisfy a signature — the "invent a value to
        satisfy a schema" move this codebase forbids everywhere else.

        **What IS refused is a successor with no retirement.** `note`'s
        `note_replacement_implies_retirement_check` is a ONE-DIRECTIONAL
        implication — a replacement implies a retirement, never the converse —
        and this verb always sets `retired_at`, so the illegal state is
        unreachable through it. The double must refuse it too, by reaching
        around its own types, or the two implementations are not
        interchangeable and the contract suite cannot see the difference.

        `because` is stored in `retire_reason` and is validated against
        `NoteSupersedeReason.ALL`. A free-text reason would make the retirement
        vocabulary unqueryable — «how many notes did the curator merge this
        week» is the reading that justifies the curator existing, and it cannot
        be answered against prose.

        A `target` that names no standing note in this workspace reports **0
        rather than raising**, including a malformed id: a background pass that
        raises on a row it does not recognise takes the whole tick down with it.
        A malformed `replaced_by`, by contrast, RAISES — same split and same
        reasoning as `SqlFactStore`: that is a caller bug, and reporting 0 would
        make it indistinguishable from "already retired", an ERROR surfacing as
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
                f"free text would make 'how many were merged' unanswerable"
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
        stmt = (
            sa.update(NOTE)
            .where(
                NOTE.c.id == target,
                NOTE.c.workspace == key,
                NOTE.c.retired_at.is_(None),
            )
            .values(
                retired_at=now_ms, replaced_by=replaced_by, retire_reason=because,
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount)

    async def record_miss(
        self, *, workspace: str, probe: str, conversation_id: str, now_ms: int,
    ) -> int:
        """The negative cache (§4.5), `fact_candidate`'s shape and its reason:
        the SECOND miss on the same ground is evidence rather than a guess. An
        insert per miss would make one gap look like N unrelated gaps and
        nothing would ever cross a threshold, so this UPSERTs on
        `note_candidate_probe_key` — `(workspace, probe)` — and returns the new
        `occurrence_count`.

        Three details that are decisions, not defaults:

        - **`first_seen_at` is never overwritten.** How long a gap has been
          open is the other half of the evidence; last-seen alone cannot tell a
          question asked three times this morning from one asked every month
          since March.
        - **`observed_in` appends only a conversation it does not already
          hold.** It answers "in how many DIFFERENT rooms", which is what makes
          a probe repeated in one room weaker evidence than the same probe in
          three; appending unconditionally turns one chatty room into a false
          consensus and grows the array without bound.
        - **The whole thing is one statement.** A read-then-write would let two
          concurrent turns in the same workspace each see count 1 and each write
          2, losing an occurrence — the same race the CAS above exists to close,
          and the reason this uses `ON CONFLICT` rather than a `SELECT`.
        """
        insert = pg_insert(NOTE_CANDIDATE).values(
            id=str(uuid4()), workspace=workspace, probe=probe,
            first_seen_at=now_ms, last_seen_at=now_ms, occurrence_count=1,
            observed_in=[conversation_id],
        )
        stmt = insert.on_conflict_do_update(
            constraint="note_candidate_probe_key",
            set_={
                "occurrence_count": NOTE_CANDIDATE.c.occurrence_count + 1,
                "last_seen_at": now_ms,
                "observed_in": sa.case(
                    (
                        sa.literal(conversation_id) == sa.any_(
                            NOTE_CANDIDATE.c.observed_in
                        ),
                        NOTE_CANDIDATE.c.observed_in,
                    ),
                    else_=NOTE_CANDIDATE.c.observed_in.op("||")(
                        sa.literal(conversation_id)
                    ),
                ),
            },
        ).returning(NOTE_CANDIDATE.c.occurrence_count)
        async with self._engine.begin() as conn:
            count = (await conn.execute(stmt)).scalar_one()
        return int(count)

    # ---- the create path (§4, issue #98) -----------------------------------

    async def record_create_candidate(
        self,
        *,
        workspace: str,
        title: str,
        description: str,
        body: str,
        closing_condition: str,
        anchor_date: int,
        anchor_entity_ids: Sequence[str],
        message_ids: Sequence[int],
        now_ms: int,
    ) -> NoteCreateCandidate:
        """Stage a proposed note (`kind='create'`) for the deterministic gate
        in `nora.language.note_candidates` to decide over. Never called for a
        `kind='miss'` row — that is `record_miss`, above, on ITS OWN
        uniqueness domain.

        **Upserts on THIS TIER'S OWN anchor key** —
        `note_candidate_create_anchor_key`, `(workspace, anchor_date,
        anchor_entity_ids)` — never on `title` (design 33 §4/M10): a
        re-observation of the same occasion refreshes the staged proposal
        rather than duplicating it. `anchor_entity_ids` is sorted and
        de-duplicated HERE, not trusted from the caller, because it is half of
        that key and an unsorted array would let two spellings of one
        occasion collide under two different keys — the defect
        `nora.domain.notes.anchor_key` exists to close one layer up.

        **The upsert is guarded by `WHERE status = 'pending'`, and that guard
        is the whole point.** A candidate the gate has already decided —
        `created` or `refused` — is a SETTLED row: refreshing its text or its
        sighting window on a later re-observation would relitigate a decision
        this tier's whole design keeps out of the model's hands (§4's "the
        model never writes a note directly"). So a re-sighting after
        settlement is a no-op here: `RETURNING` yields no row for it (Postgres
        treats a `DO UPDATE ... WHERE` whose condition is false as `DO
        NOTHING` for that row), and the settled row is looked up and returned
        instead — the caller always gets back the real row either way, and can
        inspect its `status` before deciding whether to run the gate at all.
        """
        entities = sorted(set(anchor_entity_ids))
        ids = list(dict.fromkeys(message_ids))
        insert = pg_insert(NOTE_CANDIDATE).values(
            id=str(uuid4()), workspace=workspace, kind="create",
            title=title, description=description, body=body,
            closing_condition=closing_condition, anchor_date=anchor_date,
            anchor_entity_ids=entities, message_ids=ids,
            status=CANDIDATE_PENDING, attempts=0,
            first_seen_at=now_ms, last_seen_at=now_ms,
        )
        stmt = insert.on_conflict_do_update(
            index_elements=[
                NOTE_CANDIDATE.c.workspace, NOTE_CANDIDATE.c.anchor_date,
                NOTE_CANDIDATE.c.anchor_entity_ids,
            ],
            index_where=sa.text("kind = 'create'"),
            set_={
                "title": insert.excluded.title,
                "description": insert.excluded.description,
                "body": insert.excluded.body,
                "closing_condition": insert.excluded.closing_condition,
                "last_seen_at": now_ms,
                "attempts": NOTE_CANDIDATE.c.attempts + 1,
                # APPENDED, not de-duplicated against the existing array — a
                # repeated id in the attribution list costs nothing and is not
                # the property this table's uniqueness protects. Contrast
                # `record_miss`'s `observed_in`, where de-duplication DOES
                # matter (a count that must not be inflated by one chatty
                # room) — this attribution list is not that count.
                "message_ids": NOTE_CANDIDATE.c.message_ids.op("||")(
                    sa.literal(ids, type_=pg.ARRAY(sa.Integer))
                ),
            },
            where=NOTE_CANDIDATE.c.status == CANDIDATE_PENDING,
        ).returning(NOTE_CANDIDATE)
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).one_or_none()
            if row is None:
                # The guard blocked the update: this occasion's candidate was
                # already settled. Read it back rather than trusting the
                # caller's own values, which the settled decision supersedes.
                row = (await conn.execute(
                    sa.select(NOTE_CANDIDATE).where(
                        NOTE_CANDIDATE.c.workspace == workspace,
                        NOTE_CANDIDATE.c.kind == "create",
                        NOTE_CANDIDATE.c.anchor_date == anchor_date,
                        NOTE_CANDIDATE.c.anchor_entity_ids == entities,
                    )
                )).one()
        return _row_to_create_candidate(row)

    async def set_create_candidate_status(
        self, *, workspace: str, candidate_id: str, status: str, now_ms: int,
    ) -> int:
        """Advance one create-candidate to a TERMINAL status. Returns rows
        affected — `0` if the row is gone, in another workspace, or already
        terminal.

        **Idempotent by construction, never a re-decision.** The `WHERE
        status = 'pending'` guard means a second call for a row the gate
        already settled affects nothing — the same discipline
        `record_create_candidate`'s own upsert guard enforces, so the two
        writers can never disagree about whether a row is still open to a
        decision.
        """
        if status not in (CANDIDATE_CREATED, CANDIDATE_REFUSED):
            raise ValueError(
                f"SqlNoteStore.set_create_candidate_status got "
                f"status={status!r}; only a TERMINAL status may be set this "
                f"way — {(CANDIDATE_CREATED, CANDIDATE_REFUSED)}"
            )
        if not _is_uuid(candidate_id):
            return 0
        stmt = (
            sa.update(NOTE_CANDIDATE)
            .where(
                NOTE_CANDIDATE.c.id == candidate_id,
                NOTE_CANDIDATE.c.workspace == workspace,
                NOTE_CANDIDATE.c.kind == "create",
                NOTE_CANDIDATE.c.status == CANDIDATE_PENDING,
            )
            .values(status=status, last_seen_at=now_ms)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount)

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
