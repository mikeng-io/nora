"""`SqlFactStore` — the fact tier's `DreamSink` + `FactReader`, over SQLAlchemy
Core. The tier's first store (docs/superpowers/specs/2026-08-20-fact-tier-design.md);
still no writer above it (`nora.dreamer` has no fact adaptor yet), so this
class exists to be exercised by `tests/contract/test_dream_sink_contract.py`
and `tests/infra/test_fact_store.py` today, and wired into a real adaptor
later without changing shape.

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
never `object_entity_id` — a fact written by this store always has
`warrant_kind = 'source'` (the only shape a `FactVerdict` can express; see the
design doc's warrant table) and always lands `fidelity = 'tentative'` (the
column's own server default, and the correct value for a freshly-adjudicated
claim nobody has confirmed independently yet).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.facts import Fact, FactVerdict
from nora.domain.ports import Proposal
from nora.infra.sql.schema import FACT

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
    )


class SqlFactStore:
    """`__init__(self, engine: AsyncEngine)` — and nothing else. No config, no
    clock, no metrics. `now_ms` arrives on `propose`/`supersede`, not on this
    constructor: a contract-suite fixture in another session can build this
    class from a bare `AsyncEngine` alone and must never have to guess what
    else it needs.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def propose(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> Sequence[str]:
        """See the module docstring's (1) and (2). `key` is the workspace."""
        rows = []
        ids: list[str] = []
        for p in proposals:
            if not isinstance(p.payload, FactVerdict):
                raise TypeError(
                    f"SqlFactStore.propose only accepts a FactVerdict payload, "
                    f"got {type(p.payload).__name__}"
                )
            verdict = p.payload
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
        if not rows:
            return ()
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(FACT), rows)
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

        `target`/`key` that do not name a real, standing row report 0 rather
        than raising — including a `target` that is not even UUID-shaped.
        `fact.id` is `pg.UUID`, so a non-UUID string would otherwise reach
        asyncpg as a raw type error instead of the ordinary "unknown row"
        outcome `test_superseding_an_unknown_row_reports_zero_rather_than_
        raising` requires: a background pass that raises on a row it does
        not recognise takes the whole tick down with it.
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
            UUID(target)
            UUID(replaced_by)
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
        exception cancels every sibling and silences the whole turn)."""
        if not subject_ids:
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
