"""`SqlFactStore` — the fact tier's `DreamSink` + `FactReader`, over SQLAlchemy
Core. The tier's first store (docs/superpowers/specs/2026-08-20-fact-tier-design.md);
`nora.language.fact_adaptor` is the writer above it — both phases, built and
wired. What keeps the tier dark in production is `[facts] enabled = false`,
not a missing adaptor; this header said "no writer yet" long after one
existed.

Three behaviours belong to THIS store, not to `DreamSink` — the port stays
general, and a store is where the type and the cardinality are known again
(`DreamSink.supersede`'s own docstring):

1. **`propose` `isinstance`-checks every `Proposal.payload` against
   `FactVerdict` and refuses anything else**, raising `TypeError`. `payload`
   is `object` at the port on purpose (`Proposal`'s docstring: the runtime
   never names an adaptor's own value type) — that genericity is exactly why
   nothing upstream of this store can catch a wrong value, so this is the one
   place that must.
2. **A `not_covered` verdict writes NOTHING — no row, no id in the returned
   sequence.** `FactVerdict.__post_init__` already refuses a `not_covered`
   verdict that tries to `supersedes` a fact (the type-level half of R-53);
   this is the write-level half: the tier's whole thesis is that a source's
   silence adds nothing and removes nothing, and «write a row that asserts
   nothing» is not a way to add nothing — it is a way to add a row. Chosen
   over refusing (raising) the whole batch because a `not_covered` verdict is
   not an error, it is the SAME legitimate finding `nothing_found` is at the
   port level; one bad-shaped item in a batch should not fail its siblings.
   `propose`'s return is therefore not always `len(proposals)` long — see its
   own docstring.
3. **`supersede` refuses a null `replaced_by`.** `DreamSink.supersede`'s own
   docstring is explicit that this is deliberate: "permissive at the port,
   strict at each implementation... A sink whose rows require a successor
   refuses the null itself." `fact`'s CHECK
   (`fact_supersession_is_timed_check`) is `(superseded_by is null) =
   (superseded_at is null)` — a biconditional — so writing `superseded_at`
   with no `superseded_by` is not "optional here", it is refused by the
   database too; this store raises `ValueError` before ever reaching it,
   which is a FACT-TIER rule (this CHECK, this docstring), not a `DreamSink`
   rule — the note tier's `retire` has no successor and must remain legal at
   the port for exactly that reason.

**Entity resolution is out of scope for this store.** `FactVerdict` carries
`subject`/`object` as plain strings, not entity ids — `fact_entity`'s alias
resolution (`big walk` / `Big Walk` / 大步行 → one canonical id) is a
candidate-selection concern this class does not perform. `propose` writes
`subject_id = payload.subject` and `object_literal = payload.object` verbatim,
leaving `object_entity_id` NULL — which since migration 0039 is an ordinary
resolution-pending row rather than a schema violation: the literal IS the
object, and the entity id is an additive pointer the promotion pass fills in
later without touching the words. A fact written by this store always has
`warrant_kind = 'source'` (the only shape a `FactVerdict` can express; see the
design doc's warrant table) and always lands `fidelity = 'tentative'` (the
column's own server default, and the correct value for a freshly-adjudicated
claim nobody has confirmed independently yet).

**`propose` ignores `FactVerdict.supersedes`.** A `contradicted` verdict
names the fact it supersedes (`FactVerdict.__post_init__` requires it), but
this store does not read that field to retire anything — retirement is a
separate, explicit `supersede` call, made by whatever runtime logic decides
to make it (spec §4.1). Reading only this module could reasonably suggest a
`contradicted` verdict retires its target as a side effect of `propose`; it
does not.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.facts import (
    ASSERTER_JOIN_STATUSES,
    DEFAULT_MAX_PREDICATE_CHARS,
    DEFAULT_MAX_TERM_CHARS,
    AsserterJoinStatus,
    DisputeOutcome,
    Fact,
    FactAsserter,
    FactVerdict,
    refuse_over_operating_caps,
)
from nora.domain.ports import Proposal
from nora.infra.sql.schema import FACT, FACT_ASSERTER

log = logging.getLogger(__name__)


def _row_to_fact(r: Row[Any]) -> Fact:
    return Fact(
        id=r.id, workspace=r.workspace, subject_id=r.subject_id,
        predicate=r.predicate, object_entity_id=r.object_entity_id,
        object_literal=r.object_literal, warrant_kind=r.warrant_kind,
        source_url=r.source_url, source_excerpt=r.source_excerpt,
        valid_from=r.valid_from, valid_to=r.valid_to, created_at=r.created_at,
        superseded_at=r.superseded_at, superseded_by=r.superseded_by,
        supersede_reason=r.supersede_reason, instruction_sha256=r.instruction_sha256,
        model=r.model, established_by=r.established_by, fidelity=r.fidelity,
        supersede_source=r.supersede_source,
        disputed_at=r.disputed_at, disputed_by=r.disputed_by,
        disputed_reason=r.disputed_reason, disputed_tag_id=r.disputed_tag_id,
    )


async def oversized_terms(
    engine: AsyncEngine, *, max_term_chars: int, max_predicate_chars: int,
) -> tuple[str, ...]:
    """Rows already stored that exceed the CURRENT operating caps, described
    one per line — empty when there are none.

    **This exists because refusing a config above its ceiling is not enough**
    (`30-review-response.md` §5). Lowering `[facts] max_term_chars` below rows
    that already exist leaves them satisfying the database CHECK — which is the
    ceiling, not the knob — while quietly defeating the prompt budget the cap
    was lowered to protect. The tier would keep rendering exactly the oversized
    rows the operator just said were too big, and nothing would say so.

    So the refusal is at STARTUP and it NAMES the offending rows, because
    "lower the number and see what happens" is not a thing an operator can do
    to a background tier nobody is watching. Truncating them instead was
    considered and refused: silently shortening somebody's recorded claim to
    satisfy a knob is a worse outcome than not starting.

    Reads both tables, because a candidate becomes a fact.
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.text(
                "select 'fact' as t, id::text as i, "
                "  greatest(length(subject_id), length(object_literal)) as n, "
                "  'term' as w "
                "from fact "
                "where length(subject_id) > :term or length(object_literal) > :term "
                "union all "
                "select 'fact', id::text, length(predicate), 'predicate' "
                "from fact where length(predicate) > :pred "
                "union all "
                "select 'fact_candidate', id::text, "
                "  greatest(length(subject_id), length(object)), 'term' "
                "from fact_candidate "
                "where length(subject_id) > :term or length(object) > :term "
                "union all "
                "select 'fact_candidate', id::text, length(predicate), 'predicate' "
                "from fact_candidate where length(predicate) > :pred "
                "union all "
                # `fact_entity` too. Its `canonical_name` is written by
                # promotion, which bounds it at the veto length — but the
                # COLUMN has no ceiling and `resolve_or_create` is public, so
                # the bound is a property of one caller rather than of the
                # store. An operator lowering the cap wants to know about the
                # entity table as well as the two it was named for.
                "select 'fact_entity', id::text, length(canonical_name), 'term' "
                "from fact_entity where length(canonical_name) > :term "
                "order by 1, 2"
            ),
            {"term": max_term_chars, "pred": max_predicate_chars},
        )).all()
    # Positional, not by name: `Row.t` collides with SQLAlchemy's own tuple
    # accessor and silently returns the whole row, which is how the first
    # version of this produced `('fact', '<uuid>', 300, 'term') <uuid>: ...`
    # and still satisfied a substring assertion.
    return tuple(
        f"{table} {row_id}: {which} is {n} characters"
        for table, row_id, n, which in rows
    )


class SqlFactStore:
    """`SqlFactStore(engine)` — a bare `AsyncEngine` is still enough. No
    config, no clock; `now_ms` arrives on `propose`/`supersede`, not on this
    constructor, so a contract-suite fixture in another session can build this
    class without guessing what else it needs.

    **`metrics` is the one addition, and it is optional.** The attribution join
    (§7) has three states that must be told apart, and a store that could not
    count them would leave «nobody claimed this» and «I could not check»
    indistinguishable — the exact R-27 inversion the join exists to avoid. It
    is INJECTED and never imported (`nora.infra` sits below `nora.obs`), and
    optional so the promise above survives; `tests/infra/test_fact_store.py`
    passes a real `Metrics` wherever it asserts on those states.
    """

    def __init__(
        self, engine: AsyncEngine, *,
        max_term_chars: int = DEFAULT_MAX_TERM_CHARS,
        max_predicate_chars: int = DEFAULT_MAX_PREDICATE_CHARS,
        metrics: Any = None,
    ) -> None:
        """`metrics` is OPTIONAL and INJECTED, never imported — the same shape
        `SqlPresenceStore` and `SqlMobilityStore` use, and for the same
        import-linter reason (`nora.infra` sits below `nora.obs`).

        Optional rather than required because the class docstring's promise
        still holds: a contract-suite fixture builds this from a bare
        `AsyncEngine` and must never have to guess what else it needs. What it
        costs is that the attribution join's three states go uncounted in such
        a fixture, which is why `test_fact_store.py` passes a real `Metrics`
        wherever it asserts on them.
        """
        self._engine = engine
        self._max_term_chars = max_term_chars
        self._max_predicate_chars = max_predicate_chars
        self._metrics = metrics

    def _count_asserters(self, status: AsserterJoinStatus) -> None:
        # RUNTIME check, not just mypy: this module IS in `make gate`'s mypy
        # scope, but the vocabulary constant is what the metric's help text is
        # written from and a value drifting from it is the label-vocabulary
        # failure CLAUDE.md names. Cheap, and it fires in the fixture that has
        # no metrics too.
        if status not in ASSERTER_JOIN_STATUSES:
            raise ValueError(
                f"nora_fact_asserter_total status must be one of "
                f"{sorted(ASSERTER_JOIN_STATUSES)}, got {status!r}"
            )
        if self._metrics is not None:
            self._metrics.inc("nora_fact_asserter_total", status=status)

    async def _attribute(
        self, conn: Any, *, fact_id: str, candidate_id: str | None,
        now_ms: int,
    ) -> AsserterJoinStatus:
        """`message_id -> sender_id -> person_id`, in the SAME transaction as
        the fact write (`30-review-response.md` §4: fact write + candidate
        retirement + provenance rows are one boundary).

        **The join runs through the candidate BY ID**, not by re-deriving it
        from the verdict's triple. The candidate is the only row that knows
        which messages the claim came from (`observed_in`), and the adaptor is
        the only thing that knows WHICH candidate a verdict settled — it holds
        `_Shown.candidate_ids`, keyed on what this run actually put in front of
        the model. Matching on the triple here instead was wrong in both
        directions: the model rewording a claim by one character (which the
        adjudicator documents as expected, logs and counts) made the join miss
        and report a wiring gap, and a triple match against the whole table
        could hand a settled `not_covered` candidate's asserters to an
        unrelated later fact.

        **Three states, and the middle one is the easiest to get wrong.**

        - `ok` — asserter rows written.
        - `nothing_found` — the messages are THERE and none of them names a
          person. That is an unmapped sender, and it is a real finding about
          the room (`nora_unmapped_senders_total` is the same case one layer
          up).
        - `unavailable` — no candidate to join through, or the candidate's
          messages are GONE. `AuditStore.prune_all` deletes messages by
          conversation on a schedule while `observed_in` survives it, so a
          pruned conversation leaves a candidate whose evidence no longer
          exists. Reporting that as «nobody claimed this» is exactly R-27's
          forbidden inversion, and this module's own help text asserted it
          before the distinction was measured.

        **Two guards keep an attribution problem from taking the WRITE down**,
        because the fact is the thing that matters and this runs inside its
        transaction:

        - `::bigint`, not `::int`. `messages.id` is INT4 and promotes cleanly,
          but `observed_in` holds model-derived digits of unbounded length —
          Python ints have no width — so `'99999999999'::int` raises «out of
          range» and rolls the fact back with it. The `~ '^[0-9]+$'` shape
          guard does not help: it matches any length.
        - `length(btrim(m.person_id)) > 0`. `messages.person_id` has no
          non-blank CHECK and `fact_asserter.person_id` does, so a blank one
          would raise a CheckViolation and lose the fact.
        """
        if candidate_id is None:
            return "unavailable"
        try:
            UUID(candidate_id)
        except ValueError:
            return "unavailable"
        resolvable, named = (await conn.execute(
            sa.text(
                "select count(*), "
                "  count(*) filter (where length(btrim(m.person_id)) > 0) "
                "from fact_candidate c "
                "cross join lateral unnest(c.observed_in) as oi(mid) "
                "join messages m on m.id = oi.mid::bigint "
                "where c.id = :cid and oi.mid ~ '^[0-9]+$'"
            ),
            {"cid": candidate_id},
        )).one()
        if not resolvable:
            # Either the candidate is gone, or every message it named has been
            # pruned. Both are «I could not check», never «nobody».
            return "unavailable"
        if not named:
            return "nothing_found"
        await conn.execute(
            sa.text(
                "insert into fact_asserter "
                "(fact_id, person_id, message_id, asserted_at) "
                "select :fid, btrim(m.person_id), m.id, :now "
                "from fact_candidate c "
                "cross join lateral unnest(c.observed_in) as oi(mid) "
                "join messages m on m.id = oi.mid::bigint "
                "where c.id = :cid and oi.mid ~ '^[0-9]+$' "
                "  and length(btrim(m.person_id)) > 0 "
                "on conflict do nothing"
            ),
            {"fid": fact_id, "now": now_ms, "cid": candidate_id},
        )
        return "ok"

    async def mark_disputed(
        self, *, workspace: str, fact_id: str, person_id: str | None,
        reason: str | None, tag_id: str, now_ms: int,
    ) -> DisputeOutcome:
        """Mark a standing row disputed. 31 §7's state machine, set half.

        **CAS on `disputed_at IS NULL`**, then a status read to tell the two
        failure causes apart — because `already_disputed` and
        `already_superseded` are different facts about the row and the metric's
        labels demand both. A single CAS cannot distinguish them, and reporting
        the friendlier of the two is how a second member's honest dispute comes
        to look like a row that was already handled.

        A dispute on an ALREADY-SUPERSEDED row is a no-op: that row has already
        stopped rendering as a standing claim, so there is nothing left to
        mark. A second dispute on a disputed row is a no-op too — the second
        asserter is recorded on the CANDIDATE it proposes, which is where
        asserters live.
        """
        try:
            UUID(fact_id)
        except ValueError:
            return "unknown"
        async with self._engine.begin() as conn:
            affected = (await conn.execute(
                sa.update(FACT)
                .where(
                    FACT.c.id == fact_id,
                    FACT.c.workspace == workspace,
                    FACT.c.disputed_at.is_(None),
                    FACT.c.superseded_at.is_(None),
                )
                .values(
                    disputed_at=now_ms, disputed_by=person_id,
                    disputed_reason=reason, disputed_tag_id=tag_id,
                )
            )).rowcount
            if affected:
                return "ok"
            row = (await conn.execute(
                sa.select(FACT.c.disputed_at, FACT.c.superseded_at)
                .where(FACT.c.id == fact_id, FACT.c.workspace == workspace)
            )).one_or_none()
        if row is None:
            return "unknown"
        # Superseded FIRST: a row that is both has already stopped rendering as
        # a standing claim, and that is the more informative of the two.
        if row.superseded_at is not None:
            return "already_superseded"
        return "already_disputed" if row.disputed_at is not None else "unknown"

    async def clear_disputed(
        self, *, workspace: str, fact_id: str, expected_at: int,
    ) -> bool:
        """Clear a dispute, CAS on the value that was read. 31 §7, clear half.

        **Only inside adjudication's transaction, and only on the value it
        read.** A second member's dispute landing mid-dismissal changes
        `disputed_at`, this CAS fails, the dismissal does NOT clear, and the
        adjudication becomes a candidate carrying both — 30 §4's supersession
        CAS, same shape and same reason.

        Dismissal itself requires an affirmative contradiction: `not_covered`
        never reaches here, because R-53 one level up says a source that does
        not mention something has not denied it.
        """
        async with self._engine.begin() as conn:
            affected = (await conn.execute(
                sa.update(FACT)
                .where(
                    FACT.c.id == fact_id,
                    FACT.c.workspace == workspace,
                    FACT.c.disputed_at == expected_at,
                )
                .values(
                    disputed_at=None, disputed_by=None,
                    disputed_reason=None, disputed_tag_id=None,
                )
            )).rowcount
        return bool(affected)

    async def disputed_facts(
        self, *, workspace: str, limit: int,
    ) -> tuple[Fact, ...]:
        """Standing rows currently disputed, oldest dispute first — the
        priority queue's input, and the backlog §7.1 refuses to drain silently.

        Oldest first because an aged dispute is the one that most needs
        adjudicating, and because it makes the queue fair rather than
        last-in-first-out.
        """
        if limit <= 0:
            return ()
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(
                    sa.select(FACT)
                    .where(
                        FACT.c.workspace == workspace,
                        FACT.c.disputed_at.isnot(None),
                        FACT.c.superseded_at.is_(None),
                    )
                    .order_by(FACT.c.disputed_at.asc())
                    .limit(limit)
                )).all()
        except Exception:
            log.exception("disputed_facts unavailable for workspace %s", workspace)
            return ()
        return tuple(_row_to_fact(r) for r in rows)

    async def facts_asserted_by(
        self, *, workspace: str, person_id: str, limit: int,
    ) -> tuple[Fact, ...]:
        """The sender slice (19 §6). Standing rows only, newest first.

        `limit <= 0` yields `()` BEFORE touching the database — Postgres errors
        on a negative LIMIT, and the double must degrade the same way rather
        than answering a different question the way `list[:-1]` silently does.

        Never raises, for `standing_facts`' reason: this runs inside the turn's
        `asyncio.gather`, where one uncaught exception cancels its siblings and
        silences the whole turn.
        """
        if limit <= 0:
            return ()
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(
                    sa.select(FACT)
                    .where(
                        FACT.c.workspace == workspace,
                        FACT.c.superseded_at.is_(None),
                        sa.exists().where(
                            FACT_ASSERTER.c.fact_id == FACT.c.id,
                            FACT_ASSERTER.c.person_id == person_id,
                        ),
                    )
                    .order_by(FACT.c.created_at.desc())
                    .limit(limit)
                )).all()
        except Exception:
            log.exception(
                "facts_asserted_by unavailable for workspace %s", workspace,
            )
            return ()
        return tuple(_row_to_fact(r) for r in rows)

    async def standing_subjects(self, *, workspace: str) -> tuple[str, ...]:
        """Distinct subjects of standing facts. Never raises."""
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(
                    sa.select(FACT.c.subject_id)
                    .where(
                        FACT.c.workspace == workspace,
                        FACT.c.superseded_at.is_(None),
                    )
                    .distinct()
                )).all()
        except Exception:
            log.exception(
                "standing_subjects unavailable for workspace %s", workspace,
            )
            return ()
        return tuple(r[0] for r in rows)

    async def standing_count(self, *, workspace: str) -> int:
        """The extent (19 §6, 23 §3). `0` on an unreachable store — a wrong
        number here would be a claim about the size of what she knows, and the
        honest degradation is to claim nothing."""
        try:
            async with self._engine.connect() as conn:
                return int((await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(FACT)
                    .where(
                        FACT.c.workspace == workspace,
                        FACT.c.superseded_at.is_(None),
                    )
                )).scalar_one())
        except Exception:
            log.exception("standing_count unavailable for workspace %s", workspace)
            return 0

    async def asserters(self, *, fact_id: str) -> tuple[FactAsserter, ...]:
        """Who claimed this fact, oldest first. `()` for a fact nobody could be
        attributed to AND for a `fact_id` that names nothing — the caller is
        rendering, and `21` §7.5 is explicit that recording an asserter is not
        permission to say so, so there is no state here worth branching on.

        **Ordered by `message_id`, with `asserted_at` first for a shape that
        does not exist yet.** Every asserter of one fact is written by a single
        `_attribute` call, so they all share `now_ms` and the timestamp orders
        nothing today — `message_id` is what actually sorts them, and message
        ids are monotonic per conversation, so this reads as "in the order the
        room said it". The leading `asserted_at` is there for the day a second
        pass attributes an existing fact; stating that here rather than
        implying the timestamp is doing work now.

        A non-UUID `fact_id` returns `()` rather than raising, the same rule
        `standing_facts` states for itself: this read runs on a rendering path
        and a malformed id is an unknown row, not a reason to take a turn
        down.
        """
        try:
            UUID(fact_id)
        except ValueError:
            return ()
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(FACT_ASSERTER)
                .where(FACT_ASSERTER.c.fact_id == fact_id)
                .order_by(FACT_ASSERTER.c.asserted_at, FACT_ASSERTER.c.message_id)
            )).all()
        return tuple(
            FactAsserter(
                fact_id=r.fact_id, person_id=r.person_id,
                message_id=r.message_id, asserted_at=r.asserted_at,
            )
            for r in rows
        )

    async def propose(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> Sequence[str]:
        """See the module docstring's (1) and (2). `key` is the workspace."""
        rows = []
        ids: list[str] = []
        attributions: list[tuple[str, str | None]] = []
        for p in proposals:
            if not isinstance(p.payload, FactVerdict):
                raise TypeError(
                    f"SqlFactStore.propose only accepts a FactVerdict payload, "
                    f"got {type(p.payload).__name__}"
                )
            verdict = p.payload
            over = refuse_over_operating_caps(
                subject=verdict.subject, predicate=verdict.predicate,
                obj=verdict.object, max_term_chars=self._max_term_chars,
                max_predicate_chars=self._max_predicate_chars,
            )
            if over is not None:
                # RAISES rather than dropping: a verdict this long reached the
                # store past an adaptor that should have refused it at parse,
                # which is a caller bug and not a model one. Dropping it would
                # be indistinguishable from `not_covered` — a fact silently not
                # written while the tick reports success.
                raise ValueError(
                    f"SqlFactStore.propose: {over} is "
                    f"{len(getattr(verdict, 'object' if over == 'object' else over))} "
                    f"characters, over the operating cap. The CEILING is DDL "
                    f"and would refuse it too, but not until the flush — and a "
                    f"cap that only binds at the ceiling leaves oversized rows "
                    f"satisfying the CHECK while defeating the prompt budget "
                    f"the cap exists to protect (30 §5)."
                )
            if verdict.status == "not_covered":
                # (2): silence adds nothing and removes nothing — no row, no id.
                continue
            fact_id = str(uuid4())
            rows.append({
                "id": fact_id,
                "workspace": key,
                "subject_id": verdict.subject,
                "predicate": verdict.predicate,
                "object_entity_id": None,
                "object_literal": verdict.object,
                "warrant_kind": "source",
                "source_url": verdict.source_url,
                "source_excerpt": verdict.source_excerpt,
                "valid_from": now_ms,
                "created_at": now_ms,
                "instruction_sha256": p.instruction_sha256,
                "model": p.model,
                "established_by": p.established_by,
            })
            ids.append(fact_id)
            attributions.append((fact_id, p.candidate_id))
        if not rows:
            return ()
        # Buffered, and emitted only AFTER the transaction commits. Counting
        # inside it would assert attributions the database never got: a later
        # proposal in the same batch failing rolls the whole boundary back,
        # and the counters for the earlier ones would already have moved. It
        # also stops a `Metrics.inc` failure from destroying the write it was
        # only meant to measure.
        statuses: list[AsserterJoinStatus] = []
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(FACT), rows)
            for fact_id, candidate_id in attributions:
                statuses.append(await self._attribute(
                    conn, fact_id=fact_id, candidate_id=candidate_id,
                    now_ms=now_ms,
                ))
        for status in statuses:
            self._count_asserters(status)
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
        """See the module docstring's (3): refuses a null `replaced_by`.

        `target` that does not name a real, standing row reports 0 rather
        than raising — including a `target` that is not even UUID-shaped.
        `fact.id` is `pg.UUID`, so a non-UUID string would otherwise reach
        asyncpg as a raw type error instead of the ordinary "unknown row"
        outcome `test_superseding_an_unknown_row_reports_zero_rather_than_
        raising` requires: a background pass that raises on a row it does
        not recognise takes the whole tick down with it.

        `replaced_by`, by contrast, is NOT given the same treatment when it
        is malformed (non-null but not UUID-shaped): that is a caller BUG,
        not an absent row, and reporting 0 would make it indistinguishable
        from "already superseded" or "no such row" — an ERROR silently
        surfacing as `nothing_found`, the exact shape R-27 forbids one layer
        up. It is also the one case this store CAN tell apart from a
        genuinely unknown row: a well-formed but nonexistent `replaced_by`
        already raises `IntegrityError` via `fact_superseded_by_fkey`, so
        treating the malformed case identically keeps both mistakes in the
        same class — raise — rather than one raising and the other
        vanishing into a `0` depending only on whether the string happens to
        parse as a UUID.
        """
        if replaced_by is None:
            raise ValueError(
                "SqlFactStore.supersede requires a successor: fact's "
                "(superseded_by is null) = (superseded_at is null) CHECK "
                "makes a fact either standing or superseded-with-a-successor, "
                "never superseded with no record of what replaced it — this "
                "is a fact-tier rule, not a DreamSink rule (see "
                "DreamSink.supersede's own docstring)"
            )
        try:
            UUID(replaced_by)
        except ValueError:
            raise ValueError(
                f"SqlFactStore.supersede got a malformed replaced_by "
                f"{replaced_by!r}: not a UUID. A caller bug, not an absent "
                f"row — reporting 0 here would be indistinguishable from "
                f"target not existing, which is exactly the ERROR-as-"
                f"nothing_found shape R-27 forbids."
            ) from None
        try:
            UUID(target)
        except ValueError:
            return 0
        stmt = (
            sa.update(FACT)
            .where(
                FACT.c.id == target,
                FACT.c.workspace == key,
                FACT.c.superseded_at.is_(None),
            )
            .values(
                superseded_at=now_ms,
                superseded_by=replaced_by,
                supersede_reason=because,
                supersede_source=source,
                # Supersession CLEARS the dispute, in the same statement that
                # sets it (31 §7). A superseded row does not render as a
                # standing claim, so an objection to it is an objection to a
                # version that no longer stands; leaving the columns set would
                # make `disputed_facts` return rows nobody can act on.
                #
                # An earlier version of this comment justified the clear by
                # `nora_fact_dispute_open`, a gauge that is declared nowhere
                # and emitted nowhere. The clear is right on its own terms —
                # the reading it was said to protect does not exist.
                disputed_at=None,
                disputed_by=None,
                disputed_reason=None,
                disputed_tag_id=None,
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount

    async def standing_facts(
        self, *, workspace: str, subject_ids: Sequence[str], limit: int,
    ) -> tuple[Fact, ...]:
        """The read on the tag path's critical path. NEVER raises: an
        unavailable store yields `()` — the same shape `SqlLanguageStore.
        room_language` degrades to for the same reason (this can run inside a
        turn's `asyncio.gather` beside other reads, where one uncaught
        exception cancels every sibling and silences the whole turn).

        A negative `limit` clamps to 0 rather than raising or reaching the
        database — Postgres itself errors on `LIMIT` with a negative value,
        which the broad `except Exception` below would otherwise turn into
        an indistinguishable `()`, and `InMemoryFactStore.standing_facts`
        must degrade the SAME way rather than silently dropping trailing
        facts the way `list[:-1]` does for `limit=-1` (2026-08-21 review).
        """
        if not subject_ids or limit <= 0:
            return ()
        try:
            stmt = (
                sa.select(FACT)
                .where(
                    FACT.c.workspace == workspace,
                    FACT.c.subject_id.in_(subject_ids),
                    FACT.c.superseded_at.is_(None),
                )
                .order_by(FACT.c.created_at.desc())
                .limit(limit)
            )
            async with self._engine.connect() as conn:
                rows = (await conn.execute(stmt)).all()
            return tuple(_row_to_fact(r) for r in rows)
        except Exception:
            log.exception(
                "standing_facts unavailable for workspace %s", workspace,
            )
            return ()

    async def recent_facts(self, *, workspace: str, limit: int) -> tuple[Fact, ...]:
        """See the port. Same never-raises and same negative-limit clamp as
        `standing_facts` — it runs on the same critical path, inside the same
        `asyncio.gather`, where one uncaught exception cancels every sibling
        read and silences the whole turn."""
        if limit <= 0:
            return ()
        try:
            stmt = (
                sa.select(FACT)
                .where(
                    FACT.c.workspace == workspace,
                    FACT.c.superseded_at.is_(None),
                )
                .order_by(FACT.c.created_at.desc())
                .limit(limit)
            )
            async with self._engine.connect() as conn:
                rows = (await conn.execute(stmt)).all()
            return tuple(_row_to_fact(r) for r in rows)
        except Exception:
            log.exception("recent_facts unavailable for workspace %s", workspace)
            return ()

    async def fact_by_id(self, *, workspace: str, fact_id: str) -> Fact | None:
        """History reads, including superseded rows — no `superseded_at`
        filter, unlike `standing_facts`. A `fact_id` that is not even
        UUID-shaped is "does not exist", the same as one that is
        well-formed but absent — never a raised type error."""
        try:
            UUID(fact_id)
        except ValueError:
            return None
        stmt = sa.select(FACT).where(
            FACT.c.id == fact_id, FACT.c.workspace == workspace,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_fact(row)
