"""`SqlFactCandidateStore` — the candidate tier's `DreamSink` +
`FactCandidateReader`, over SQLAlchemy Core. Sibling of
`nora.infra.sql.facts.SqlFactStore` and built to the same shape; like it,
`nora.language.fact_adaptor.FactCandidateAdaptor` is the writer above it, and
this store is where the tier's two REFUSALS live — a claim about somebody in
the room, and a term over the operating cap. This header said "no writer yet"
long after one existed, which is the same stale claim `SqlFactStore`'s header
carried; the tier is DISABLED, not unwritten. Also exercised by
`tests/infra/test_fact_candidate_store.py`
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

**`observed_in` holds the MESSAGES the claim was seen in, not the key** (flagged
2026-08-21, and the one place this store departs from the instruction it was
built to). §5's Phase A is "read the window, emit candidate triples **with the
turn each came from**", and `CandidateClaim.message_ids` are those turns; each
sighting merges its message ids in, so the column accumulates the distinct
turns a claim was asserted across.

**`occurrence_count` does NOT count them, and since plural `message_ids` it
never did.** It counts SIGHTINGS — `propose` calls — while `observed_in` counts
turns, and one sighting can now carry several (D31: an episode is a
conversational chunk, so a claim asserted across two messages is one sighting
with two ids). The two numbers came apart and the distinction is load-bearing
downstream: `pending`'s seen-once drop and its recurrence ranking both key on
`occurrence_count`, so a claim made across three turns of one episode is
correctly treated as one sighting rather than as three people agreeing. The
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

import logging
from collections import Counter
from collections.abc import Sequence
from typing import Any, Literal, get_args
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.bounds import cut, over_fetch
from nora.domain.facts import (
    DEFAULT_MAX_PREDICATE_CHARS,
    DEFAULT_MAX_TERM_CHARS,
    CandidateClaim,
    FactCandidate,
    normalise_name,
    refuse_over_operating_caps,
)
from nora.domain.ports import Proposal
from nora.infra.sql.schema import FACT, FACT_CANDIDATE, FACT_ENTITY

log = logging.getLogger(__name__)

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

    def __init__(
        self, engine: AsyncEngine, *,
        roster: frozenset[str] = frozenset(),
        roster_names: frozenset[str] = frozenset(),
        max_term_chars: int = DEFAULT_MAX_TERM_CHARS,
        max_predicate_chars: int = DEFAULT_MAX_PREDICATE_CHARS,
        metrics: Any = None,
    ) -> None:
        """`roster` is the `person_id`s `identity.toml` knows and
        `roster_names` is every name the group calls them — their display name,
        their first and last names, and the operator-authored aliases (阿Miles,
        米高, a handle). Either one refuses a claim about that person (25 §2.4).

        **Both, because an id-only match is the half that does not work.** A
        claim about a person in SUBJECT position is a fact about a human being
        living in the wrong tier — those belong to Honcho and its peer cards,
        and the knowledge tier holding them is the shape the design refuses.
        Matching only `person_id` leaves the entire class reachable through the
        name everybody actually uses.

        **EXACT after normalisation, never a prefix or a substring**, and this
        stage earned that rule: the promotion veto's month abbreviations
        matched any word starting with them, so `mar` refused "Marco's car" and
        `may` refused "maybe". A display name like "May", "Sky" or "Grace"
        under a prefix match would eat ordinary words — and over-refusing is
        not free, because it silently deletes legitimate facts.

        **These names are used to REFUSE, never to resolve.** `PersonRef`'s own
        note is that aliases must never decide who a message is FROM — a
        display name is spoofable and a sender is authenticated by platform id
        alone. Refusing on one is the safe direction: the worst it costs is a
        claim about a thing that shares a person's name.

        `metrics` is injected and never imported, the shape `SqlPresenceStore`
        uses. All three default to empty/None so a contract-suite fixture still
        builds this from a bare `AsyncEngine` — and an empty roster refuses
        NOTHING, which is the correct degradation: a sink that refused
        everything because nobody wired the roster would be a worse failure
        than the one it guards against, and it would look exactly like a quiet
        room.
        """
        self._engine = engine
        self._roster_ids = frozenset(
            normalise_name(p) for p in roster if p.strip()
        )
        self._roster_names = frozenset(
            normalise_name(n) for n in roster_names if n.strip()
        ) - self._roster_ids
        self._max_term_chars = max_term_chars
        self._max_predicate_chars = max_predicate_chars
        self._metrics = metrics

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
        refused: Counter[str] = Counter()
        for p in proposals:
            if not isinstance(p.payload, CandidateClaim):
                raise TypeError(
                    f"SqlFactCandidateStore.propose only accepts a "
                    f"CandidateClaim payload, got {type(p.payload).__name__}"
                )
            claim = p.payload
            # 25 §2.4 / 21 §2. The extraction objective's FIRST exclusion is
            # "anything about a person in this room", and a probe measured the
            # model breaking it at five person-claims in six edges WITH the
            # roster supplied and a Person type declared. So the boundary is
            # checkable and NOT enforced by the prompt, and 17 §7's rule
            # applies: refuse loudly, never accept and forget.
            #
            # **The SUBJECT only.** A person in OBJECT position is 21 §2's own
            # worked example of a legitimate claim — "Grant's wedding is on
            # the 9th" is a claim about an event, not about Grant's
            # disposition. The restriction bites on predicates ABOUT a person,
            # which is what a person-subject triple is.
            over = refuse_over_operating_caps(
                subject=claim.subject, predicate=claim.predicate,
                obj=claim.object, max_term_chars=self._max_term_chars,
                max_predicate_chars=self._max_predicate_chars,
            )
            if over is not None:
                # DROPPED here, where the fact store RAISES, and the asymmetry
                # is the tier's own: a candidate is one sighting of one claim
                # and losing it costs a re-extraction, while a verdict has
                # already spent a research loop. Counted, because a silent drop
                # reads as "the model found nothing".
                refused["over_cap"] += 1
                log.info(
                    "fact candidate dropped: %s is over the operating cap "
                    "(workspace=%s)", over, key,
                )
                continue
            subject = normalise_name(claim.subject)
            reason = (
                "person_id" if subject in self._roster_ids
                else "display_name" if subject in self._roster_names
                else None
            )
            if reason is not None:
                # Counted BY REASON so the two are separate series. If matching
                # on a name over-refuses, this counter is how anyone finds out;
                # if it never fires, that is worth knowing too — an id-only
                # match would have looked identical from outside.
                refused[reason] += 1
                log.info(
                    "fact candidate refused: subject %r is a roster person "
                    "(matched on %s, workspace=%s)", claim.subject, reason, key,
                )
                continue
            rows.append({
                "id": str(uuid4()),
                "workspace": key,
                "subject_id": claim.subject,
                "predicate": claim.predicate,
                "object": claim.object,
                "first_seen_at": now_ms,
                "last_seen_at": now_ms,
                "occurrence_count": 1,
                # `dict.fromkeys`, not a set: the merge below promises "never
                # duplicate" and the FIRST insert was exempt from it, so
                # `[41, 41]` wrote `{41,41}` and contradicted the invariant
                # one statement later. Order-preserving so the array is
                # stable rather than set-arbitrary.
                "observed_in": list(
                    dict.fromkeys(str(i) for i in claim.message_ids)
                ),
                "status": "pending",
                "attempts": 0,
                "established_by": p.established_by,
                "instruction_sha256": p.instruction_sha256,
                "model": p.model,
            })
        if self._metrics is not None:
            for reason, count in refused.items():
                self._metrics.inc(
                    "nora_fact_person_claim_refused_total", float(count),
                    matched=reason,
                )
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
        settled = int(result.rowcount)
        # `nora_fact_candidates_total`'s FIRST call site. It has been declared
        # since the tier shipped and emitted nowhere, which renders as a series
        # that is genuinely always zero — and it is named exactly as though it
        # answers «did the tier produce anything», so a null from it is
        # indistinguishable from a null from a tier that never ran. That is
        # CLAUDE.md's rule about reading an instrument before checking it can
        # measure the thing, and this counter could not.
        #
        # HERE, on the settle, because this is the only per-candidate EVENT in
        # the tier. The declaration's original vocabulary (already_known /
        # noise / deferred) describes properties of the standing pending queue,
        # which a counter cannot carry: incrementing it from a census counts
        # reads x rows rather than rows, which is a bug this branch already
        # fixed once elsewhere. The queue's shape is a GAUGE's job and
        # `nora_fact_entity_unresolved` is how that is done here.
        #
        # Zero is not counted: `supersede` is idempotent by design and a
        # re-fire over settled work legitimately affects no rows.
        if settled and self._metrics is not None:
            self._metrics.inc(
                "nora_fact_candidates_total", float(settled), outcome=because,
            )
        return settled

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
        # load-bearing part. Before migration 0039 an entity-object fact had
        # `object_literal` NULL; `null = 'x'` is NULL, so the dedup predicate
        # above never fired for it and the claim was re-adjudicated every tick
        # forever, ranking FIRST each time because this predicate then read it
        # as a contradiction of its own candidate. 0039 made `object_literal`
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
        # `nora_fact_entity_unresolved`'s FIRST call site. It has been
        # declared with none since the tier shipped, which renders as a series
        # that is genuinely always zero — and it is the tier's ONLY instrument
        # for the entity problem, so the one thing that could observe
        # fragmentation could not observe anything (02's seventh blind layer,
        # found while writing the contract that depends on it).
        #
        # Counted HERE rather than at the drop, because a subject that resolves
        # to nothing is a reading about the tier whether or not this particular
        # candidate also happened to be seen twice. Pending rows only: a
        # settled candidate's subject is somebody else's problem.
        unresolved_stmt = (
            sa.select(sa.func.count())
            .select_from(c)
            .where(
                c.c.workspace == workspace,
                c.c.status == "pending",
                sa.not_(subject_resolves),
            )
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
                # TIE-BREAK, and this is the ordering that spends the
                # tick's research budget. One propose batch stamps every
                # candidate with one `last_seen_at` and they commonly share
                # `occurrence_count = 1`, so all three keys tie routinely
                # and Postgres may then return any order. Which candidates
                # get adjudicated varied run to run, and one over the limit
                # could be starved indefinitely.
                c.c.id.asc(),
            )
            # OVER-FETCH BY ONE (`nora.domain.bounds`). The extra row is never
            # returned; its existence is the only cheap, snapshot-consistent
            # answer to "was this queue longer than the tick could take".
            .limit(over_fetch(limit))
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
            unresolved = (await conn.execute(unresolved_stmt)).scalar_one()
        # Same connection as the read it describes: a count taken against a
        # different snapshot would be a number about a moment nothing else
        # observed.
        # A GAUGE, not a counter, and set unconditionally including at zero.
        # This is a census of the pending queue taken on every read; adding it
        # to a monotonic counter measured how often the queue was READ rather
        # than how much of it is unresolved. 0 says the read ran and everything
        # resolved; absent says the tier has never read this workspace.
        if self._metrics is not None:
            self._metrics.gauge(
                "nora_fact_entity_unresolved", float(unresolved),
                workspace=workspace,
            )
        # The cut applies to the PAGE only. `unresolved` above is a census of
        # the whole pending queue and stays that way because it comes from its
        # own unbounded statement, not from `rows` — the two numbers answer
        # different questions and must not be derived from one another. A
        # census narrowed to the page would read healthier the more backlog
        # there was, which is the opposite of what it is for.
        kept = cut(
            rows, limit=limit, site="fact_candidates.pending",
            metrics=self._metrics,
        )
        return tuple(_row_to_candidate(r) for r in kept)

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
