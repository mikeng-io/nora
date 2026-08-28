"""`InMemoryFactStore` and `fact_proposal_factory` — the fact tier's testing
doubles, domain-only. `nora.testing` may not import `nora.infra`
(`lint-imports`), so this module builds everything from
`nora.domain.facts`/`nora.domain.ports` alone; nothing here talks to SQL.

`InMemoryFactStore` is a SECOND implementation of `DreamSink`/`FactReader`
(`nora.infra.sql.facts.SqlFactStore` is the first), not a fake shaped to
dodge behaviour — CLAUDE.md's rule ("never write a test whose fake answers
every request the same way") is why it applies the SAME three refusals
`SqlFactStore` does rather than accepting anything: an `isinstance` check on
`Proposal.payload`, silence on `not_covered`, and a raised `ValueError` on a
null `replaced_by`. It carries no delete method, matching `DreamSink`'s own
shape ("invalidate, never delete").

`fact_proposal_factory` builds a `Proposal` whose `payload` is a valid
`FactVerdict` — for another session's contract suite (a note-tier store, most
likely) to hand to *its own* store and prove a foreign payload is refused,
the way `tests/contract/test_dream_sink_contract.py`'s own foreign-payload
test hands this tier's factory output to a store that is not this tier's.
That is also why it stays domain-only: a suite living outside `nora.infra`
must be able to import it.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import cast
from uuid import UUID, uuid4

from nora.domain.facts import (
    DisputeOutcome,
    Fact,
    FactAsserter,
    FactVerdict,
    SupersedeReason,
)
from nora.domain.ports import Proposal

_counter = itertools.count(1)


def _distinct_sha256(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def fact_proposal_factory(
    *,
    instruction_sha256: str | None = None,
    model: str | None = None,
    established_by: str | None = None,
) -> Proposal:
    """A `Proposal` whose `payload` is a valid, `supported` `FactVerdict`.

    Every `None` argument fills with a value distinct from the others AND
    distinct from the previous call — `instruction_sha256`/`model`/
    `established_by` are three different strings, never the same literal
    three times, so a caller that swaps two of them in an assertion notices.
    The verdict's own `subject`/`predicate`/`object`/`source_url`/
    `source_excerpt` vary the same way, so two calls never collide on a
    natural key either.
    """
    n = next(_counter)
    if instruction_sha256 is None:
        instruction_sha256 = _distinct_sha256(f"instruction-{n}")
    if model is None:
        model = f"contract-fixture-model-{n}"
    if established_by is None:
        established_by = f"run-{n}"
    verdict = FactVerdict(
        subject=f"entity-{n}",
        predicate="has-attribute",
        object=f"value-{n}",
        status="supported",
        addresses_claim=True,
        source_url=f"https://example.test/fact-fixture/{n}",
        source_excerpt=f"the excerpt backing claim {n}",
    )
    return Proposal(
        payload=verdict,
        instruction_sha256=instruction_sha256,
        model=model,
        established_by=established_by,
    )


@dataclass
class InMemoryFactStore:
    """`DreamSink` + `FactReader`, backed by a plain dict. `__init__` takes
    no arguments — there is no engine to inject — so a contract-suite fixture
    can construct this with zero setup, the same affordance
    `SqlFactStore(engine)`'s docstring states explicitly for its own
    constructor."""

    _facts: dict[tuple[str, str], Fact] = field(default_factory=dict)
    _asserters: list[FactAsserter] = field(default_factory=list)

    async def propose(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> tuple[str, ...]:
        """See the module docstring: refuses a non-`FactVerdict` payload,
        writes nothing for a `not_covered` verdict."""
        ids: list[str] = []
        for p in proposals:
            if not isinstance(p.payload, FactVerdict):
                raise TypeError(
                    f"InMemoryFactStore.propose only accepts a FactVerdict "
                    f"payload, got {type(p.payload).__name__}"
                )
            verdict = p.payload
            if verdict.status == "not_covered":
                continue
            # `uuid4`, not a readable counter, because `SqlFactStore.propose`
            # mints `str(uuid4())` and THE SHAPE IS LOAD-BEARING. The Dreamer's
            # egress screen drops any envelope item carrying a UUID
            # (`nora.agent.dream_screen`), which is the defect #63 fixes — and a
            # double minting `fact-1` cannot reproduce it, so the test written to
            # prove that fix passed against the reverted code. A readable id is
            # more pleasant in a failure message and is worth nothing if it makes
            # the double disagree with the store about a property a test turns on.
            fact_id = str(uuid4())
            fact = Fact(
                id=fact_id,
                workspace=key,
                subject_id=verdict.subject,
                predicate=verdict.predicate,
                object_entity_id=None,
                object_literal=verdict.object,
                warrant_kind="source",
                source_url=verdict.source_url,
                source_excerpt=verdict.source_excerpt,
                valid_from=now_ms,
                created_at=now_ms,
                instruction_sha256=p.instruction_sha256,
                model=p.model,
                established_by=p.established_by,
            )
            self._facts[(key, fact_id)] = fact
            ids.append(fact_id)
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
        """See the module docstring: refuses a null `replaced_by`, matching
        `SqlFactStore.supersede` — a fact-tier rule, not a `DreamSink` one."""
        if replaced_by is None:
            raise ValueError(
                "InMemoryFactStore.supersede requires a successor: a fact is "
                "either standing or superseded-with-a-successor, never "
                "superseded with no record of what replaced it"
            )
        existing = self._facts.get((key, target))
        if existing is None or existing.superseded_at is not None:
            return 0
        # `because` is `str` at the port (DreamSink.supersede) but
        # `Fact.supersede_reason` is the fact tier's own constrained Literal
        # — the cast defers to `Fact.__post_init__`'s own runtime check
        # (raises `ValueError` on an invalid value), matching
        # `SqlFactStore.supersede`, which defers the same check to
        # `fact_supersede_reason_check` in the database. Neither validates
        # ahead of time; both refuse via the same layer that already had to
        # own the enum.
        self._facts[(key, target)] = replace(
            existing,
            superseded_at=now_ms,
            superseded_by=replaced_by,
            supersede_reason=cast(SupersedeReason, because),
            supersede_source=source,
            # Supersession clears the dispute, matching the SQL store: a
            # superseded row does not render as a standing claim, so the flag
            # would otherwise make the open-dispute gauge count resolved rows
            # forever.
            disputed_at=None, disputed_by=None,
            disputed_reason=None, disputed_tag_id=None,
        )
        return 1

    async def mark_disputed(
        self, *, workspace: str, fact_id: str, person_id: str | None,
        reason: str | None, tag_id: str, now_ms: int,
    ) -> DisputeOutcome:
        """The same four outcomes as the SQL store, in the same order — and the
        order is the behaviour: superseded is reported ahead of disputed
        because a row that is both has already stopped standing."""
        existing = self._facts.get((workspace, fact_id))
        if existing is None:
            return "unknown"
        if existing.superseded_at is not None:
            return "already_superseded"
        if existing.disputed_at is not None:
            return "already_disputed"
        self._facts[(workspace, fact_id)] = replace(
            existing, disputed_at=now_ms, disputed_by=person_id,
            disputed_reason=reason, disputed_tag_id=tag_id,
        )
        return "ok"

    async def clear_disputed(
        self, *, workspace: str, fact_id: str, expected_at: int,
    ) -> bool:
        existing = self._facts.get((workspace, fact_id))
        if existing is None or existing.disputed_at != expected_at:
            return False
        self._facts[(workspace, fact_id)] = replace(
            existing, disputed_at=None, disputed_by=None,
            disputed_reason=None, disputed_tag_id=None,
        )
        return True

    async def disputed_facts(
        self, *, workspace: str, limit: int,
    ) -> tuple[Fact, ...]:
        if limit <= 0:
            return ()
        rows = [
            f for (ws, _fid), f in self._facts.items()
            if ws == workspace and f.is_disputed and f.is_standing
        ]
        # Tie-break on `id`, matching `SqlFactStore`. One `recognise()`
        # stamps every row it marks with one `now_ms`, so ties here are
        # the normal case — and a stable sort made this double
        # deterministic where production was not.
        rows.sort(key=lambda f: (f.disputed_at or 0, f.id))
        return tuple(rows[:limit])

    async def facts_asserted_by(
        self, *, workspace: str, person_id: str, limit: int,
    ) -> tuple[Fact, ...]:
        """The sender slice, over whatever `seed_asserters` recorded. Same
        clamp as `standing_facts` and for the same reason: the double must
        degrade the way the real store does, or a test that passes here fails
        against Postgres."""
        if limit <= 0:
            return ()
        mine = {
            a.fact_id for a in self._asserters if a.person_id == person_id
        }
        matches = [
            f for (ws, fid), f in self._facts.items()
            if ws == workspace and fid in mine and f.is_standing
        ]
        # Tie-break on `id`, matching `SqlFactStore`: one propose batch
        # writes every row with one `now_ms`.
        matches.sort(key=lambda f: (f.created_at, f.id), reverse=True)
        return tuple(matches[:limit])

    async def standing_subjects(self, *, workspace: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            f.subject_id for (ws, _fid), f in self._facts.items()
            if ws == workspace and f.is_standing
        ))

    async def standing_count(self, *, workspace: str) -> int:
        return sum(
            1 for (ws, _fid), f in self._facts.items()
            if ws == workspace and f.is_standing
        )

    async def asserters(self, *, fact_id: str) -> tuple[FactAsserter, ...]:
        """Reads back what `seed_asserters` put in, oldest first.

        **This double does NOT perform the attribution join, and that is
        stated rather than left to be discovered.** `SqlFactStore._attribute`
        walks `fact_candidate.observed_in -> messages.person_id`, two tables
        this class does not hold; reproducing it here would mean reproducing
        an unmapped sender, a pruned message and a missing candidate, and a
        double that got any of them subtly wrong would make a green test a
        claim about the double. So the JOIN is asserted against real Postgres
        only (`tests/infra/test_fact_store.py`), and what is shared here is
        the READ — which does branch, on a malformed id and on ordering, and
        matches production on both.
        """
        try:
            UUID(fact_id)
        except ValueError:
            return ()
        rows = [a for a in self._asserters if a.fact_id == fact_id]
        rows.sort(key=lambda a: (a.asserted_at, a.message_id))
        return tuple(rows)

    def seed_fact(self, fact: Fact) -> None:
        """Test seam for a fact this double did not `propose`. Its SQL sibling
        gets one for free from the FK that refuses an orphan asserter; without
        it here, the two halves of `facts_asserted_by` answered differently."""
        self._facts[(fact.workspace, fact.id)] = fact

    def seed_asserters(self, *asserters: FactAsserter) -> None:
        """Test seam for `asserters`, and the only writer this double has for
        them — a production `propose` writes them through SQL the double
        cannot run."""
        self._asserters.extend(asserters)

    async def standing_facts(
        self, *, workspace: str, subject_ids: Sequence[str], limit: int,
    ) -> tuple[Fact, ...]:
        # A negative limit clamps to 0, matching SqlFactStore's own
        # degradation (2026-08-21 review) — `list[:limit]` with a negative
        # limit silently drops trailing entries instead (`[:-1]` keeps all
        # but the last), which is a different, wrong answer for the same
        # input.
        if limit <= 0:
            return ()
        wanted = set(subject_ids)
        matches = [
            f for (ws, _fid), f in self._facts.items()
            if ws == workspace and f.subject_id in wanted and f.is_standing
        ]
        # Tie-break on `id`, matching `SqlFactStore`: one propose batch
        # writes every row with one `now_ms`.
        matches.sort(key=lambda f: (f.created_at, f.id), reverse=True)
        return tuple(matches[:limit])

    async def embeddable(
        self, *, workspace: str, limit: int,
    ) -> tuple[tuple[Fact, ...], str]:
        """Pass 4's backlog read. **This verb was MISSING from the double**,
        so wiring `FactEmbedder` against it would have raised
        `AttributeError` into the sweep's blanket handler and reported
        `unavailable` forever — a wiring gap wearing an outage's label, which
        is the one thing this tier's whole R-27 discipline exists to prevent.

        The double holds no embedding state, so it cannot reproduce
        production's cursor — unembedded first, then least-recently-embedded.
        It sorts by the cohort tie-break alone and SAYS SO here, because a
        double that silently answered a different question would let a
        sweep-level test assert an ordering production does not have. The
        convergence property is tested against real Postgres, in
        `tests/infra/test_fact_store.py`, where the join exists.
        """
        if limit <= 0:
            return (), "nothing_found"
        matches = [
            f for (ws, _fid), f in self._facts.items()
            if ws == workspace and f.is_standing
        ]
        matches.sort(key=lambda f: (f.created_at, f.id))
        out = tuple(matches[:limit])
        return out, "ok" if out else "nothing_found"

    async def recent_facts(self, *, workspace: str, limit: int) -> tuple[Fact, ...]:
        # Same clamp as `standing_facts` above and for the same reason: the
        # double must degrade the way the real store does, or a test that
        # passes here would fail against Postgres.
        if limit <= 0:
            return ()
        matches = [
            f for (ws, _fid), f in self._facts.items()
            if ws == workspace and f.is_standing
        ]
        # Tie-break on `id`, matching `SqlFactStore`: one propose batch
        # writes every row with one `now_ms`.
        matches.sort(key=lambda f: (f.created_at, f.id), reverse=True)
        return tuple(matches[:limit])

    async def fact_by_id(self, *, workspace: str, fact_id: str) -> Fact | None:
        return self._facts.get((workspace, fact_id))
