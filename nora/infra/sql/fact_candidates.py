"""`SqlFactCandidateStore` — the candidate tier's `DreamSink` +
`FactCandidateReader`, over SQLAlchemy Core. Sibling of
`nora.infra.sql.facts.SqlFactStore` and built to the same shape; like it,
there is no writer above it yet (`nora.dreamer` has no fact adaptor), so it
exists today to be exercised by `tests/infra/test_fact_candidate_store.py`
and to be wired into a real adaptor later without changing shape.

**Why `fact_candidate` exists at all** (spec §5.1 — this is the whole
argument, and the table is meaningless without it). A `not_covered` verdict
on a contradicting candidate **writes nothing and removes nothing** —
otherwise the tier would hold both «the black tower exists» and «there is no
black tower» at once and could never be read. But if nothing is recorded, the
same unprovable claim is re-extracted and re-adjudicated every tick, forever,
and it fails quietly because nobody is waiting on the Dreamer. So the NEGATIVE
result is made durable: `status = 'not_covered'` with `last_adjudicated_at`
and `attempts`. **The tier remembers what it could not settle, and that is the
difference between a bounded cost and an unbounded one.**

Everything below follows from that sentence:

1. **`propose` is an UPSERT on the natural key**, `(workspace, subject_id,
   predicate, object)` — the table's own `fact_candidate_triple_key`. A claim
   sighted again is the SAME candidate, not a new one: a second row would
   split its `occurrence_count` (§5's recurrence ranking, which decides which
   candidates are worth a research loop) and, worse, would let a fresh
   `pending` row shadow a `not_covered` one — re-spending the loop the
   negative cache exists to save. So a repeat sighting bumps
   `occurrence_count`, moves `last_seen_at` and merges the new scope into
   `observed_in` **without duplicating it**, and deliberately does NOT touch
   `status`, `attempts` or `first_seen_at`.
2. **`propose` `isinstance`-checks every `Proposal.payload` against
   `CandidateClaim` and raises `TypeError` on anything else.** `payload` is
   `object` at the port on purpose (the runtime never names an adaptor's own
   value type), which is exactly why nothing upstream of this store can catch
   a wrong value — `SqlFactStore.propose` documents the same duty for the same
   reason.
3. **`supersede` RETIRES a candidate; the row is RETAINED and stays
   readable.** It stamps `status`, `last_adjudicated_at` and `attempts + 1`
   and nothing else. «Invalidate, never delete» is `DreamSink`'s structural
   claim; here it is also the point of the table, because a deleted
   `not_covered` candidate is re-extracted next tick.

**The tier is ASYMMETRIC on purpose: a narrow claim in, a full row out.**
`propose` takes `CandidateClaim` — subject, predicate, object, and the message
the claim was asserted in — while `pending` returns `FactCandidate`, the ROW.
An adaptor cannot supply the row's other nine fields and must not be asked to
invent them: `id` is minted here (a candidate's identity is its triple; a
client-minted id would be a second identity for the same row, and the two
would disagree the first time the same claim was sighted twice),
`first_seen_at`/`last_seen_at` come from `now_ms` (the verb's clock, so one
tick's rows agree about when the tick happened), `occurrence_count` is what
the upsert computes, `status` is always `pending` on a write, `attempts` and
`last_adjudicated_at` are moved only by `supersede`, and `established_by`,
`instruction_sha256` and `model` come from the **`Proposal`** — what produced
a row is provenance only the runtime knows, which is why all three ride on
`Proposal` rather than inside `payload` at all. See `CandidateClaim`'s own
docstring. (The last two landed 2026-08-21 in migration `0027`, found by
joining `tests/contract/test_dream_sink_contract.py`: this store was dropping
the DEC-125/DEC-126 pair every `Proposal` already carried, which is provenance
thrown away at the one place it is free to keep.)

**`observed_in` holds the MESSAGE the claim was seen in, not the key** (flagged
2026-08-21, and the one place this store departs from the instruction it was
built to). §5's Phase A is "read the window, emit candidate triples **with the
turn each came from**", and `CandidateClaim.message_id` is that turn; each
sighting merges its message id in, so the column accumulates the distinct turns
a claim was asserted across and `occurrence_count` counts them. The
alternative — taking it from `key` — holds only for a SCOPE-keyed sink: this
one is workspace-keyed (DEC-105, and `DreamSink`'s own docstring says so),
so `observed_in = (key,)` would write the same constant into every row in the
workspace, a column carrying exactly zero information, and would throw away
the only field on the payload that says where the claim came from.

**Three refusals in `supersede` that are THIS store's, not `DreamSink`'s.**
The port is permissive and each implementation is strict where its own rows
are (`DreamSink.supersede`'s own docstring):

- **`because` must be `adjudicated` or `not_covered`** — the two terminal
  statuses, the same way `SqlFactStore` constrains `stale|wrong|merged`.
  Unlike that store, this one cannot defer the check to the database:
  `fact_candidate_status_check` also admits `'pending'`, and «supersede a
  candidate into pending» is not a retirement at all — it would silently
  un-settle a row and hand it straight back to `pending()`. Constrained here
  because the CHECK is a wider set than this verb's legal one.
- **`replaced_by` must be `None`** — and this is the second tier to need the
  port's null legal (the note tier's `retire` was the first): a candidate that
  could not be settled is replaced by nothing at all, and an adjudicated one is
  succeeded by a `fact`, which is a row in a DIFFERENT tier. `replaced_by` is
  documented at the port as "the successor IN THIS TIER", and no candidate ever
  succeeds another. A non-null value is therefore REFUSED rather than accepted
  and dropped: `fact_candidate` has no column to hold it, so accepting it would
  tell the caller a successor was recorded when nothing recorded it and nothing
  could ever tell — an ERROR surfacing as `ok`, which is the R-27 shape this
  codebase refuses one layer up. If a candidate ever should name the fact that
  settled it, that is a column and a migration, not a silent drop.
- **`source` must be `None`**, for exactly the same reason: a supersession's
  warrant belongs on the `fact` row (`fact.supersede_source`), this table has
  no column for one, and dropping a citation the caller believed was stored is
  the same undetectable loss.

An unknown `target` — including one that is not UUID-shaped, which `pg.UUID`
would otherwise turn into a raw type error — reports 0 and raises nothing. A
background pass that raises on a row it does not recognise takes the whole
tick down with it.

**`pending` is §5's deterministic filter, and §5's point is that this filter
is CODE, not a model.** See its own docstring for the ranking, and for the one
rank that is deliberately NOT implemented.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, get_args
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.facts import CandidateClaim, FactCandidate
from nora.domain.ports import Proposal
from nora.infra.sql.schema import FACT, FACT_CANDIDATE, FACT_ENTITY

# The two statuses `supersede` may write. NOT `CandidateStatus` itself:
# `pending` is a legal status for a ROW and an illegal reason for a
# RETIREMENT, and collapsing the two sets is how a settled candidate gets
# handed back to `pending()`.
TerminalStatus = Literal["adjudicated", "not_covered"]
_TERMINAL_STATUSES: frozenset[str] = frozenset(get_args(TerminalStatus))


def _row_to_candidate(r: Row[Any]) -> FactCandidate:
    return FactCandidate(
        id=r.id, workspace=r.workspace, subject_id=r.subject_id,
        predicate=r.predicate, object=r.object, first_seen_at=r.first_seen_at,
        last_seen_at=r.last_seen_at, occurrence_count=r.occurrence_count,
        observed_in=tuple(r.observed_in), status=r.status,
        established_by=r.established_by,
        instruction_sha256=r.instruction_sha256, model=r.model,
        last_adjudicated_at=r.last_adjudicated_at, attempts=r.attempts,
    )


class SqlFactCandidateStore:
    """`__init__(self, engine: AsyncEngine)` — and nothing else, matching
    `SqlFactStore`'s own constructor for the reason its docstring states: a
    fixture in another session must be able to build this class from a bare
    `AsyncEngine` and never have to guess what else it needs. `now_ms`
    arrives on `propose`/`supersede`, not here.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def propose(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> Sequence[str]:
        """Upsert each candidate on its triple; return the id of every row
        touched, in order. See the module docstring's (1) and (2).

        Ids, not a count — `supersede` has to be able to NAME what a
        `propose` produced. For a repeat sighting that id is the EXISTING
        row's, which is the whole point of upserting on the natural key: the
        caller gets the identity of the candidate it just re-sighted, not the
        identity of a duplicate. Two proposals in one batch naming the same
        triple therefore return the same id twice, and the second bumps the
        first rather than colliding with it.

        `key` is the workspace.

        One statement per proposal rather than one multi-row insert: a
        multi-row `ON CONFLICT DO UPDATE` raises `cardinality_violation` the
        moment a single batch names the same triple twice — a Phase A
        extraction over one window can legitimately emit the same claim from
        two turns — and RETURNING would then have to be re-paired with the
        input by hand. They share one transaction, so the batch is still
        all-or-nothing.
        """
        rows: list[dict[str, Any]] = []
        for p in proposals:
            if not isinstance(p.payload, CandidateClaim):
                raise TypeError(
                    f"SqlFactCandidateStore.propose only accepts a "
                    f"CandidateClaim payload, got {type(p.payload).__name__}"
                )
            claim = p.payload
            rows.append({
                "id": str(uuid4()),
                "workspace": key,
                "subject_id": claim.subject,
                "predicate": claim.predicate,
                "object": claim.object,
                "first_seen_at": now_ms,
                "last_seen_at": now_ms,
                "occurrence_count": 1,
                "observed_in": [str(claim.message_id)],
                "status": "pending",
                "attempts": 0,
                "established_by": p.established_by,
                "instruction_sha256": p.instruction_sha256,
                "model": p.model,
            })
        if not rows:
            return ()
        ids: list[str] = []
        async with self._engine.begin() as conn:
            for row in rows:
                insert = pg.insert(FACT_CANDIDATE).values(row)
                upsert = insert.on_conflict_do_update(
                    constraint="fact_candidate_triple_key",
                    set_={
                        "last_seen_at": insert.excluded.last_seen_at,
                        "occurrence_count": FACT_CANDIDATE.c.occurrence_count + 1,
                        # Merge, never duplicate: `observed_in` is the SET of
                        # turns a claim was asserted in (§5's "with the turn
                        # each came from"), and re-appending one already
                        # there would grow the array without bound when the
                        # same tick re-reads the same window. `except`
                        # de-duplicates its own left input too; `order by 1`
                        # keeps the appended tail deterministic rather than
                        # set-operation-arbitrary.
                        "observed_in": sa.text(
                            "fact_candidate.observed_in || array("
                            "select unnest(excluded.observed_in) "
                            "except select unnest(fact_candidate.observed_in) "
                            "order by 1)"
                        ),
                        # status / attempts / first_seen_at / established_by /
                        # instruction_sha256 / model are deliberately absent:
                        # a re-sighting is not an adjudication, and resetting
                        # `status` here would hand a settled candidate back to
                        # `pending()` every time somebody said the same thing
                        # again — the unbounded re-adjudication §5.1 exists to
                        # stop. The provenance trio is left alone for the
                        # matching reason: it says which run ESTABLISHED this
                        # row, and a later sighting did not establish it.
                    },
                ).returning(FACT_CANDIDATE.c.id)
                ids.append((await conn.execute(upsert)).scalar_one())
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
        """Retire `target` into a terminal status, stamping
        `last_adjudicated_at` and incrementing `attempts`. Returns rows
        affected. The row is RETAINED — see the module docstring's (3) and
        its three refusals.

        Only a `pending` row is affected: a settled candidate is settled, and
        a second call reports 0 rather than re-stamping it. That is the
        idempotence the Dreamer loop needs (it can legitimately re-fire over
        work it already did), and it is also the negative cache holding —
        re-adjudicating a `not_covered` candidate every tick is the unbounded
        cost this table exists to bound. Nothing moves a settled row back to
        `pending`, and there is deliberately no verb that would; `attempts` is
        incremented rather than set to 1 so that a future retry pass has a
        counter to carry on rather than a flag to reinterpret.
        """
        if because not in _TERMINAL_STATUSES:
            raise ValueError(
                f"SqlFactCandidateStore.supersede takes because in "
                f"{sorted(_TERMINAL_STATUSES)} — the two TERMINAL candidate "
                f"statuses — got {because!r}. `fact_candidate_status_check` "
                f"also admits 'pending', which is a legal status for a row "
                f"and not a legal reason for a retirement, so this store "
                f"constrains it rather than deferring to the CHECK."
            )
        if replaced_by is not None:
            raise ValueError(
                f"SqlFactCandidateStore.supersede takes no successor, got "
                f"{replaced_by!r}: `replaced_by` is the successor IN THIS "
                f"TIER (DreamSink.supersede) and no candidate ever succeeds "
                f"another — an adjudicated candidate is succeeded by a fact, "
                f"a row in a different tier and table. fact_candidate has no "
                f"column for it, and accepting-then-dropping it would report "
                f"a successor recorded that nothing recorded."
            )
        if source is not None:
            raise ValueError(
                f"SqlFactCandidateStore.supersede takes no source, got "
                f"{source!r}: a supersession's warrant belongs on the fact "
                f"row (fact.supersede_source). fact_candidate has no column "
                f"for one, and silently dropping a citation the caller "
                f"believed was stored is the same undetectable loss."
            )
        try:
            UUID(target)
        except ValueError:
            # `fact_candidate.id` is `pg.UUID`, so a non-UUID string would
            # otherwise reach asyncpg as a raw type error instead of the
            # ordinary "unknown row" outcome. A background pass that raises
            # on a row it does not recognise takes the whole tick with it.
            return 0
        stmt = (
            sa.update(FACT_CANDIDATE)
            .where(
                FACT_CANDIDATE.c.id == target,
                FACT_CANDIDATE.c.workspace == key,
                FACT_CANDIDATE.c.status == "pending",
            )
            .values(
                status=because,
                last_adjudicated_at=now_ms,
                attempts=FACT_CANDIDATE.c.attempts + 1,
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount

    async def pending(
        self, *, workspace: str, limit: int,
    ) -> tuple[FactCandidate, ...]:
        """§5's deterministic filter — **the cost decision, in code**. One
        research loop per candidate is the expensive step, so which
        candidates come back, and in what order, IS the budget.

        The two DROPS, applied first:

        1. Candidates already backed by a live, non-superseded `fact` with
           the same triple. There is nothing left to prove.
        2. Unresolvable-subject candidates seen once — one-off noise. A
           subject is "resolvable" when some `fact_entity` in the workspace
           carries it as its canonical name or as an ALIAS: alias resolution
           is the axis `FactEntity`'s own docstring calls load-bearing, and
           checking `canonical_name` alone would drop a candidate about `big
           walk` because the entity is stored as `Big Walk`.

        Then the ORDER:

        3. **Candidates that CONTRADICT a live fact first** — the same
           `(workspace, subject_id, predicate)` with a different object. This
           is the tier's purpose; a filter that ranks these anywhere else
           still type-checks and still looks like it works, and just spends
           the tick on the wrong claims.
        4. *(§5's fourth rank — candidates asserted with ZERO tool calls, the
           pure-inference shape likeliest to be wrong — is NOT implemented,
           deliberately.* `fact_candidate` carries no tool-call count and
           nothing on the write path supplies one. Every proxy available here
           (`observed_in` length, `occurrence_count`, absence of a
           `fact_entity`) measures something else entirely, so inventing one
           would rank candidates by a number that does not mean what the
           ranking claims it means — worse than the honest gap, because a
           wrong ranking is invisible while a missing one is written down. It
           needs a column, and a column needs a migration.)
        5. Everything else by `occurrence_count` descending, then
           `last_seen_at` descending (most recently re-sighted first, so a
           stale tie loses to a live one).

        Only `status = 'pending'` is ever returned: `not_covered` is the
        negative cache and returning it would re-spend the loop that failed.

        `limit <= 0` yields `()` **before touching the database** — Postgres
        errors on a negative `LIMIT`, and `InMemoryFactCandidateStore` must
        degrade the same way rather than answering a different question the
        way `list[:-1]` silently does (the trap
        `SqlFactStore.standing_facts` documents for itself).

        **Raises rather than degrading to `()`.** Unlike
        `FactReader.standing_facts` this is not on a tag's critical path; it
        runs in the Dreamer's tick, where `()` would say «nothing to
        adjudicate» when the truth is «I could not check» — see
        `FactCandidateReader`'s own docstring.
        """
        if limit <= 0:
            return ()
        c = FACT_CANDIDATE.alias("c")
        same_triple_fact = (
            sa.select(sa.literal(1))
            .where(
                FACT.c.workspace == c.c.workspace,
                FACT.c.subject_id == c.c.subject_id,
                FACT.c.predicate == c.c.predicate,
                FACT.c.object_literal == c.c.object,
                FACT.c.superseded_at.is_(None),
            )
            .exists()
        )
        # `is_distinct_from`, not `!=`, and it is now a BELT rather than the
        # load-bearing part. Before migration 0038 an entity-object fact had
        # `object_literal` NULL; `null = 'x'` is NULL, so the dedup predicate
        # above never fired for it and the claim was re-adjudicated every tick
        # forever, ranking FIRST each time because this predicate then read it
        # as a contradiction of its own candidate. 0038 made `object_literal`
        # NOT NULL — the literal always carries the words and the entity id is
        # an additive pointer beside it — so the NULL that caused it cannot
        # occur. `is_distinct_from` stays because a three-valued comparison
        # that degrades to a two-valued one costs nothing and the column's
        # nullability is a schema fact, not an invariant this query controls.
        contradicts_a_fact = (
            sa.select(sa.literal(1))
            .where(
                FACT.c.workspace == c.c.workspace,
                FACT.c.subject_id == c.c.subject_id,
                FACT.c.predicate == c.c.predicate,
                FACT.c.object_literal.is_distinct_from(c.c.object),
                FACT.c.superseded_at.is_(None),
            )
            .exists()
        )
        subject_resolves = (
            sa.select(sa.literal(1))
            .where(
                FACT_ENTITY.c.workspace == c.c.workspace,
                sa.or_(
                    FACT_ENTITY.c.canonical_name == c.c.subject_id,
                    c.c.subject_id == sa.any_(FACT_ENTITY.c.aliases),
                ),
            )
            .exists()
        )
        stmt = (
            sa.select(c)
            .where(
                c.c.workspace == workspace,
                c.c.status == "pending",
                ~same_triple_fact,
                ~sa.and_(c.c.occurrence_count <= 1, ~subject_resolves),
            )
            .order_by(
                sa.case((contradicts_a_fact, 0), else_=1),
                c.c.occurrence_count.desc(),
                c.c.last_seen_at.desc(),
            )
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(_row_to_candidate(r) for r in rows)

    async def candidate_by_id(
        self, *, workspace: str, candidate_id: str,
    ) -> FactCandidate | None:
        """One candidate by id, in ANY status — the read that proves a
        superseded row was retained rather than removed, which is
        `DreamSink`'s structural claim and this table's whole reason to
        exist. Not on `FactCandidateReader`: `pending` is the port's job
        (choosing what to adjudicate), and a by-id read is this store's own
        surface, the same split `SqlFactStore.fact_by_id` sits on relative to
        `standing_facts`. A `candidate_id` that is not even UUID-shaped is
        "does not exist", never a raised type error.
        """
        try:
            UUID(candidate_id)
        except ValueError:
            return None
        stmt = sa.select(FACT_CANDIDATE).where(
            FACT_CANDIDATE.c.id == candidate_id,
            FACT_CANDIDATE.c.workspace == workspace,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_candidate(row)
