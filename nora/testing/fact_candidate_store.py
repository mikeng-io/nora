"""`InMemoryFactCandidateStore` and `fact_candidate_proposal_factory` — the
candidate tier's testing doubles, domain-only. `nora.testing` may not import
`nora.infra` (`lint-imports`), so this module builds everything from
`nora.domain.facts`/`nora.domain.ports` alone; nothing here talks to SQL.

`InMemoryFactCandidateStore` is a SECOND implementation of `DreamSink` +
`FactCandidateReader` (`nora.infra.sql.fact_candidates.SqlFactCandidateStore`
is the first), not a fake shaped to dodge behaviour — CLAUDE.md's rule
("never write a test whose fake answers every request the same way") is why it
carries the SAME refusals and the SAME §5 filter, including the parts that are
easy to get quietly wrong:

- the `isinstance` check on `Proposal.payload` (`CandidateClaim`, the narrow
  write-side type — a full `FactCandidate` row is what `pending` RETURNS, and
  the tier is asymmetric on purpose);
- upsert on the triple `(workspace, subject_id, predicate, object)`, merging
  `observed_in` — the turns the claim was asserted in, from
  `CandidateClaim.message_ids` — without duplicating, and leaving
  `status`/`attempts`/`first_seen_at` alone, along with the provenance trio
  (`established_by`/`instruction_sha256`/`model`, migration 0027) which names
  the run that ESTABLISHED the row and which a later sighting did not;
- `supersede`'s three refusals (`because` must be terminal, `replaced_by` and
  `source` must be `None`) and its `pending`-only, retain-the-row update;
- `pending`'s two drops, its contradiction-first ordering, and its
  `limit <= 0 -> ()` clamp. That last one is why this class exists in this
  shape at all: `matches[:limit]` with `limit=-1` silently keeps every
  candidate but the last, which is a DIFFERENT WRONG ANSWER to the same
  question the SQL store answers with `()` — the trap
  `SqlFactStore.standing_facts` already documents for its own double.

It carries no delete method, matching `DreamSink`'s own shape ("invalidate,
never delete") and §5.1's reason for the table.

**The facts and entities `pending` filters against are injected.** The SQL
store reads them from the same database in one statement; this one has no
database, so it takes the two collections through `__init__` — the only seam
this repo's test-seam policy allows, and the same shape the parametrized suite
uses to seed both implementations from one test body. Passing the lists in and
appending to them afterwards is deliberate: a fixture builds the store once and
each test seeds the world it needs.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import cast
from uuid import uuid4

from nora.domain.facts import (
    CandidateClaim,
    CandidateStatus,
    Fact,
    FactCandidate,
    FactEntity,
)
from nora.domain.ports import Proposal

_counter = itertools.count(1)

_TERMINAL_STATUSES: frozenset[str] = frozenset({"adjudicated", "not_covered"})


def fact_candidate_proposal_factory(
    *,
    subject: str | None = None,
    predicate: str = "has-attribute",
    # `object` shadows the builtin, deliberately: it is the column's name and
    # `CandidateClaim`'s own field name, and renaming it here would make the
    # factory's keyword differ from the field it fills.
    object: str | None = None,
    message_ids: tuple[int, ...] | None = None,
    instruction_sha256: str | None = None,
    model: str | None = None,
    established_by: str | None = None,
) -> Proposal:
    """A `Proposal` whose `payload` is a valid `CandidateClaim` — the NARROW
    write-side type, not the `FactCandidate` row (see `CandidateClaim`'s own
    docstring for why the tier is asymmetric).

    Every `None` argument fills with a value distinct from the others AND
    distinct from the previous call, mirroring `fact_proposal_factory`'s own
    contract: `instruction_sha256`/`model`/`established_by` are three
    different strings, so a caller that swaps two of them in an assertion
    notices, and the triple varies too so two calls never collide on the
    natural key unless a test asks them to.
    """
    n = next(_counter)
    if instruction_sha256 is None:
        instruction_sha256 = hashlib.sha256(f"instruction-{n}".encode()).hexdigest()
    if model is None:
        model = f"contract-fixture-model-{n}"
    if established_by is None:
        established_by = f"run-{n}"
    claim = CandidateClaim(
        subject=subject if subject is not None else f"entity-{n}",
        predicate=predicate,
        object=object if object is not None else f"value-{n}",
        message_ids=message_ids if message_ids is not None else (n,),
    )
    return Proposal(
        payload=claim,
        instruction_sha256=instruction_sha256,
        model=model,
        established_by=established_by,
    )


@dataclass
class InMemoryFactCandidateStore:
    """`DreamSink` + `FactCandidateReader`, backed by a plain dict keyed on
    the natural key the SQL store's `fact_candidate_triple_key` keys on.

    `facts` and `entities` are the world `pending` filters against — see the
    module docstring. Both default to empty, so a caller that only exercises
    the write half constructs this with zero setup, the same affordance
    `InMemoryFactStore()` offers.
    """

    facts: list[Fact] = field(default_factory=list)
    entities: list[FactEntity] = field(default_factory=list)
    _by_triple: dict[tuple[str, str, str, str], FactCandidate] = field(
        default_factory=dict,
    )
    _seq: int = 0

    async def propose(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> tuple[str, ...]:
        """See the module docstring: refuses a non-`CandidateClaim` payload,
        upserts on the triple, and returns the id of every row touched — the
        EXISTING id for a re-sighting."""
        ids: list[str] = []
        for p in proposals:
            if not isinstance(p.payload, CandidateClaim):
                raise TypeError(
                    f"InMemoryFactCandidateStore.propose only accepts a "
                    f"CandidateClaim payload, got {type(p.payload).__name__}"
                )
            claim = p.payload
            triple = (key, claim.subject, claim.predicate, claim.object)
            # Deduped on the first insert, matching the SQL store: the
            # merge below promises "never duplicate" and the first write
            # was exempt from it.
            seen_in = tuple(dict.fromkeys(str(i) for i in claim.message_ids))
            existing = self._by_triple.get(triple)
            if existing is None:
                self._seq += 1
                fresh = FactCandidate(
                    # `uuid4` for the reason `InMemoryFactStore.propose`
                    # gives: `SqlFactCandidateStore` mints one, and a double
                    # whose ids are a different SHAPE cannot reproduce a defect
                    # that turns on the shape.
                    id=str(uuid4()),
                    workspace=key,
                    subject_id=claim.subject,
                    predicate=claim.predicate,
                    object=claim.object,
                    first_seen_at=now_ms,
                    last_seen_at=now_ms,
                    occurrence_count=1,
                    observed_in=seen_in,
                    status="pending",
                    established_by=p.established_by,
                    # The DEC-125/DEC-126 pair, from the `Proposal` — migration
                    # 0027's columns. Established once and never moved by a
                    # re-sighting, exactly as the SQL store's upsert leaves
                    # them out of its `set_`.
                    instruction_sha256=p.instruction_sha256,
                    model=p.model,
                )
                self._by_triple[triple] = fresh
                ids.append(fresh.id)
                continue
            # A re-sighting: bump, move, merge — and leave `status`,
            # `attempts` and `first_seen_at` exactly where they were.
            merged = tuple(dict.fromkeys((*existing.observed_in, *seen_in)))
            self._by_triple[triple] = replace(
                existing,
                last_seen_at=now_ms,
                occurrence_count=existing.occurrence_count + 1,
                observed_in=merged,
            )
            ids.append(existing.id)
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
        """See the module docstring's three refusals, matching
        `SqlFactCandidateStore.supersede` — candidate-tier rules, not
        `DreamSink` ones. The row is retained and stays readable."""
        if because not in _TERMINAL_STATUSES:
            raise ValueError(
                f"InMemoryFactCandidateStore.supersede takes because in "
                f"{sorted(_TERMINAL_STATUSES)} — the two TERMINAL candidate "
                f"statuses — got {because!r}: 'pending' is a legal status for "
                f"a row and not a legal reason for a retirement."
            )
        if replaced_by is not None:
            raise ValueError(
                f"InMemoryFactCandidateStore.supersede takes no successor, "
                f"got {replaced_by!r}: no candidate ever succeeds another, "
                f"and accepting-then-dropping the id would report a successor "
                f"recorded that nothing recorded."
            )
        if source is not None:
            raise ValueError(
                f"InMemoryFactCandidateStore.supersede takes no source, got "
                f"{source!r}: a supersession's warrant belongs on the fact "
                f"row, and this tier has nowhere to keep one."
            )
        found = self._locate(workspace=key, candidate_id=target)
        if found is None:
            return 0
        triple, existing = found
        if existing.status != "pending":
            return 0
        self._by_triple[triple] = replace(
            existing,
            # `because` is `str` at the port; `FactCandidate.status` is this
            # tier's own constrained Literal. The cast defers to
            # `FactCandidate.__post_init__`'s own runtime check, matching
            # `InMemoryFactStore.supersede`'s handling of `supersede_reason`
            # — except that the guard above is STRICTER than that check,
            # because `pending` passes it and is not a retirement.
            status=cast(CandidateStatus, because),
            last_adjudicated_at=now_ms,
            attempts=existing.attempts + 1,
        )
        return 1

    async def pending(
        self, *, workspace: str, limit: int,
    ) -> tuple[FactCandidate, ...]:
        """§5's filter, same drops and same order as
        `SqlFactCandidateStore.pending` — including the deliberately
        unimplemented fourth rank (zero-tool-call candidates), which has no
        column here either. Raises nothing today because nothing here can
        fail; it makes no never-raises promise, matching the port."""
        if limit <= 0:
            return ()
        survivors = [
            c for c in self._by_triple.values()
            if c.workspace == workspace
            and c.status == "pending"
            and not self._backed_by_a_live_fact(c)
            and not (c.occurrence_count <= 1 and not self._subject_resolves(c))
        ]
        survivors.sort(
            key=lambda c: (
                0 if self._contradicts_a_live_fact(c) else 1,
                -c.occurrence_count,
                -c.last_seen_at,
            )
        )
        return tuple(survivors[:limit])

    async def candidate_by_id(
        self, *, workspace: str, candidate_id: str,
    ) -> FactCandidate | None:
        """One candidate by id, in ANY status — the read that proves a
        retired row was retained. Mirrors
        `SqlFactCandidateStore.candidate_by_id`."""
        found = self._locate(workspace=workspace, candidate_id=candidate_id)
        return None if found is None else found[1]

    # -- the world `pending` filters against ---------------------------------

    def _live_facts(self, candidate: FactCandidate) -> list[Fact]:
        return [
            f for f in self.facts
            if f.workspace == candidate.workspace
            and f.subject_id == candidate.subject_id
            and f.predicate == candidate.predicate
            and f.is_standing
        ]

    def _backed_by_a_live_fact(self, candidate: FactCandidate) -> bool:
        return any(
            f.object_literal == candidate.object for f in self._live_facts(candidate)
        )

    def _contradicts_a_live_fact(self, candidate: FactCandidate) -> bool:
        # `is distinct from` in SQL, `!=` here. Since migration 0039
        # `object_literal` is NOT NULL on both sides, so the two agree without
        # the NULL case that used to separate them — which is the point: the
        # production comparison and this one broke the negative cache
        # IDENTICALLY, so the contract suite could not have caught it. A fake
        # that mirrors the bug faithfully is the failure CLAUDE.md's
        # fake-answers-everything-the-same-way rule is about, one level in.
        return any(
            f.object_literal != candidate.object for f in self._live_facts(candidate)
        )

    def _subject_resolves(self, candidate: FactCandidate) -> bool:
        return any(
            e.workspace == candidate.workspace
            and (
                e.canonical_name == candidate.subject_id
                or candidate.subject_id in e.aliases
            )
            for e in self.entities
        )

    def _locate(
        self, *, workspace: str, candidate_id: str,
    ) -> tuple[tuple[str, str, str, str], FactCandidate] | None:
        for triple, c in self._by_triple.items():
            if triple[0] == workspace and c.id == candidate_id:
                return triple, c
        return None
