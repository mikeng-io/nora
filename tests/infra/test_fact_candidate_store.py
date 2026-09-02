"""Candidate-tier behaviour, parametrized over BOTH implementations —
`nora.infra.sql.fact_candidates.SqlFactCandidateStore` and
`nora.testing.InMemoryFactCandidateStore` — the way
`tests/infra/test_fact_store.py` is over the fact tier's two. A refusal or a
ranking asserted against only one implementation is a claim about that
implementation, not about the tier, and this tier's double promises "the SAME
refusals and the SAME §5 filter" in its own docstring.

`tests/contract/test_dream_sink_contract.py` is deliberately NOT the home for
any of this: that file is `DreamSink`'s shared shape. These are the CANDIDATE
tier's own rules — §5's deterministic selection filter, §5.1's negative cache,
and the three `supersede` refusals `SqlFactCandidateStore`'s own docstring
names.

**`SqlFactCandidateStore` now DOES join that suite's `IMPLEMENTATIONS` table**
— it is the third entry — but it did not join for one entry, and the gap was
a REPORTED FINDING first. That file's docstring claimed joining was
"genuinely one entry" and said that if it stopped being true it was a defect;
it was measured here (2026-08-21) by adding the entry and running it — 4
passed, 9 failed — and then repaired on `fact-contract-repair`. What the nine
cost, because the next tier should know before it starts:

1. **Three assertions read back columns `fact_candidate` did not have.**
   `instruction_sha256`/`model` (`test_provenance_survives_the_write`) were a
   real gap and closed by **migration `0027_fact_candidate_provenance`** —
   DEC-125/DEC-126 provenance on an adaptor-written table that predated its
   adaptor, which this store had been discarding off every `Proposal`. A
   successor (`test_a_supersession_can_name_its_successor`) and a supersession
   warrant (`test_source_and_replaced_by_are_kept_apart`) were NOT: no
   candidate ever succeeds another (an adjudicated one is succeeded by a
   `fact`, a row in a different table) and a supersession's warrant belongs on
   that `fact` row, so there is nothing for a column here to hold. Those two
   became axis states, not columns.
2. **Five passed a non-null `replaced_by` into calls this store refuses.**
   `nullable_replaced_by` was a BOOL over an axis with three states —
   null-legal, null-refused, and (here) **null-required** — replaced by
   `NullableAxis`, which each entry names per field for both `replaced_by`
   and `source`, and which is itself the factory every test now gets those
   values from.
3. **Two of those five, plus one more, also hard-coded `because`.** The suite
   assumed a zero-row `supersede` never reaches a reason check, which holds
   for a store that defers to a Postgres CHECK and NOT for this one, whose
   terminal-status refusal is a `ValueError` raised before any row is
   touched. Every `supersede` in that file now takes its reason from
   `make_because()`.

One of the nine was a plain defect independent of this tier:
`test_a_supersession_with_no_successor_is_legal_or_refused_by_the_store`'s
nullable branch hard-coded `because="retired"` instead of calling
`make_because()`, even though the call asserts `affected == 1` and the file's
own rule is that `make_because` is used "everywhere a `supersede` call is
expected to actually affect a row". `"retired"` is not a legal reason in the
note tier either (`merged`/`retired-expired`/`retired-closed`), which would
have hit it next.

`InMemoryFactCandidateStore` still does not join that table, for the reason
`InMemoryFactStore` does not: it is this tier's own double, tested here
against the SQL store, and the suite's third entry exists to exercise the
PORT against a real store.

`test_the_port_has_no_delete_verb` and `isinstance(store, DreamSink)` are the
properties of that suite this tier carries unaided; the second is asserted
below, and the first is already global (it checks the Protocol, not a store).
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, replace

import pytest
import sqlalchemy as sa

from nora.domain.facts import Fact, FactCandidate, FactEntity, FactVerdict
from nora.domain.ports import DreamSink, FactCandidateReader, Proposal
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.fact_candidates import SqlFactCandidateStore
from nora.infra.sql.facts import SqlFactStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import FACT_ENTITY
from nora.obs.metrics import Metrics
from nora.testing import (
    InMemoryFactCandidateStore,
    fact_candidate_proposal_factory,
    fact_proposal_factory,
)

NOW = 1_755_200_000_000
WS = "workspace-a"


def _candidate(
    *,
    subject: str = "e1",
    predicate: str = "has",
    object: str = "v1",
    message_ids: tuple[int, ...] = (1,),
    established_by: str = "run-1",
) -> Proposal:
    """A `Proposal` carrying a `CandidateClaim` — the NARROW write-side type.
    `FactCandidate`, the row, is what `pending` gives back; an adaptor cannot
    supply a row's id, timestamps, occurrence count or status and is not asked
    to invent them (see `CandidateClaim`'s own docstring)."""
    return fact_candidate_proposal_factory(
        subject=subject, predicate=predicate, object=object,
        message_ids=message_ids, established_by=established_by,
    )


@pytest.fixture(scope="module")
def candidate_pg_url(pg_admin) -> Iterator[str]:
    """One database, migrated once, for every test in this module.

    The house pattern — function-scoped `pg_url` plus `run_migrations` per test
    — costs a CREATE DATABASE, the whole migration chain and a DROP for each of
    this file's 56 tests. That degrades the shared testcontainer, and the bill
    is paid by `tests/integration/test_latency.py`, which writes audit rows to
    the same container and asserts a p95. A defect in one file charged to
    another, which is exactly why it read as an unrelated flake.
    """

    import psycopg

    dbname = "t_factcand_" + uuid.uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@dataclass
class _World:
    """The store plus the two seeding verbs `pending`'s filter needs. Both are
    per-implementation because the two stores read the world from different
    places — the SQL store joins `fact`/`fact_entity` in the same database,
    the double is handed lists — which is the same per-implementation-callable
    shape `tests/contract/test_dream_sink_contract.py`'s `IMPLEMENTATIONS`
    uses, and for the same reason: anything the shared body needs that is not
    on the port is supplied by the entry, never hard-coded to one backing."""

    store: object
    seed_fact: Callable[..., object]
    seed_entity: Callable[..., object]
    # What PROMOTION does (`25-knowledge-pipeline.md` §4): points a standing
    # fact's object at an entity, WITHOUT touching the literal. Seeding is
    # split from resolving on purpose — production writes the literal at the
    # sink and adds the pointer in a later pass, and a helper that did both at
    # once would test a row shape no writer ever produces.
    resolve_object: Callable[..., object]


def _fact(
    *, workspace: str, subject: str, predicate: str, object: str, fact_id: str,
    superseded_by: str | None = None,
) -> Fact:
    return Fact(
        id=fact_id, workspace=workspace, subject_id=subject, predicate=predicate,
        object_entity_id=None, object_literal=object, valid_from=NOW,
        created_at=NOW, instruction_sha256="a" * 64, model="m",
        established_by="run-seed",
        source_url="https://x.test/a", source_excerpt="the excerpt",
        superseded_at=None if superseded_by is None else NOW + 1,
        superseded_by=superseded_by,
        supersede_reason=None if superseded_by is None else "wrong",
    )


def _verdict_proposal(*, subject: str, predicate: str, object: str) -> Proposal:
    p = fact_proposal_factory()
    return Proposal(
        payload=FactVerdict(
            subject=subject, predicate=predicate, object=object,
            status="supported", addresses_claim=True,
            source_url="https://x.test/a", source_excerpt="the excerpt",
        ),
        instruction_sha256=p.instruction_sha256, model=p.model,
        established_by=p.established_by,
    )


@pytest.fixture(params=["memory", "sql"])
async def world(request) -> AsyncIterator[_World]:
    if request.param == "memory":
        facts: list[Fact] = []
        entities: list[FactEntity] = []
        seq = iter(range(1, 10_000))

        async def seed_fact(
            *, workspace: str = WS, subject: str, predicate: str = "has",
            object: str, superseded_by_object: str | None = None,
        ) -> None:
            fid = f"seed-fact-{next(seq)}"
            successor_id: str | None = None
            if superseded_by_object is not None:
                successor_id = f"seed-fact-{next(seq)}"
                facts.append(_fact(
                    workspace=workspace, subject=subject, predicate=predicate,
                    object=superseded_by_object, fact_id=successor_id,
                ))
            facts.append(_fact(
                workspace=workspace, subject=subject, predicate=predicate,
                object=object, fact_id=fid, superseded_by=successor_id,
            ))

        async def seed_entity(
            *, workspace: str = WS, canonical_name: str,
            aliases: tuple[str, ...] = (),
        ) -> str:
            eid = f"seed-entity-{next(seq)}"
            entities.append(FactEntity(
                id=eid, workspace=workspace,
                canonical_name=canonical_name, created_at=NOW, aliases=aliases,
            ))
            return eid

        async def resolve_object(*, object: str, entity_id: str) -> None:
            for i, f in enumerate(facts):
                if f.object_literal == object:
                    facts[i] = replace(f, object_entity_id=entity_id)

        yield _World(
            store=InMemoryFactCandidateStore(facts=facts, entities=entities),
            seed_fact=seed_fact, seed_entity=seed_entity,
            resolve_object=resolve_object,
        )
        return

    # ONE database and ONE migration chain for this module — see
    # `candidate_pg_url` below. Row isolation between tests comes from
    # truncating the four fact-tier tables, which is the guarantee this file
    # actually needs; rebuilding the schema 56 times was buying nothing and
    # charging `tests/integration/test_latency.py` for it.
    url = request.getfixturevalue("candidate_pg_url")
    engine = create_sql_engine(url)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "TRUNCATE fact_premise, fact, fact_candidate, fact_entity "
            "RESTART IDENTITY CASCADE"
        ))
    fact_store = SqlFactStore(engine)

    async def seed_fact(
        *, workspace: str = WS, subject: str, predicate: str = "has",
        object: str, superseded_by_object: str | None = None,
    ) -> None:
        ids = await fact_store.propose(
            key=workspace,
            proposals=(_verdict_proposal(
                subject=subject, predicate=predicate, object=object,
            ),),
            now_ms=NOW,
        )
        if superseded_by_object is None:
            return
        successor = await fact_store.propose(
            key=workspace,
            proposals=(_verdict_proposal(
                subject=subject, predicate=predicate, object=superseded_by_object,
            ),),
            now_ms=NOW,
        )
        await fact_store.supersede(
            key=workspace, target=ids[0], replaced_by=successor[0],
            because="wrong", source=None, now_ms=NOW + 1,
        )

    async def seed_entity(
        *, workspace: str = WS, canonical_name: str,
        aliases: tuple[str, ...] = (),
    ) -> str:
        async with engine.begin() as conn:
            return str((await conn.execute(
                sa.insert(FACT_ENTITY).values(
                    workspace=workspace, canonical_name=canonical_name,
                    aliases=list(aliases), created_at=NOW,
                ).returning(FACT_ENTITY.c.id)
            )).scalar_one())

    async def resolve_object(*, object: str, entity_id: str) -> None:
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "update fact set object_entity_id = :e where object_literal = :o"
            ), {"e": entity_id, "o": object})

    try:
        yield _World(
            store=SqlFactCandidateStore(engine), seed_fact=seed_fact,
            seed_entity=seed_entity, resolve_object=resolve_object,
        )
    finally:
        await engine.dispose()


@pytest.fixture
def store(world: _World):
    return world.store


# ---------------------------------------------------------------------------
# the two ports
# ---------------------------------------------------------------------------


def test_the_store_satisfies_both_halves(store):
    """The write half is `DreamSink` (two verbs, no third); the read half is
    the tier's own port, never bolted onto `DreamSink` — `FactReader`'s
    reasoning, one table over."""
    assert isinstance(store, DreamSink)
    assert isinstance(store, FactCandidateReader)


# ---------------------------------------------------------------------------
# workspace isolation
# ---------------------------------------------------------------------------


async def test_a_candidate_never_surfaces_in_a_sibling_workspace(store):
    """`workspace` is `Conversation.workspace` (DEC-105) — the same key R-49
    refuses to relay across. `fact_candidate_triple_key` leads with it for
    this reason: the same claim in two workspaces is two candidates."""
    # Sighted twice in each workspace so both survive §5's
    # unresolvable-subject-seen-once drop — the variable under test is the
    # workspace, not the filter.
    ids = await store.propose(
        key="workspace-a",
        proposals=(_candidate(subject="shared"), _candidate(subject="shared")),
        now_ms=NOW,
    )
    other = await store.propose(
        key="workspace-b",
        proposals=(_candidate(subject="shared"), _candidate(subject="shared")),
        now_ms=NOW,
    )

    a = await store.pending(workspace="workspace-a", limit=10)
    b = await store.pending(workspace="workspace-b", limit=10)

    assert ids[0] != other[0], "the same triple in two workspaces is two rows"
    assert [c.id for c in a] == [ids[0]]
    assert [c.id for c in b] == [other[0]]


# ---------------------------------------------------------------------------
# the isinstance refusal
# ---------------------------------------------------------------------------


async def test_propose_refuses_a_payload_that_is_not_a_fact_candidate(store):
    """`payload: object` at the port means nothing upstream can catch a wrong
    value — this store is where it is caught. The foreign value is another
    tier's real factory output, never a hand-rolled bad object, so it stays
    honest as payload types evolve."""
    with pytest.raises(TypeError):
        await store.propose(
            key=WS, proposals=(fact_proposal_factory(),), now_ms=NOW,
        )


# ---------------------------------------------------------------------------
# propose upserts on the triple
# ---------------------------------------------------------------------------


async def test_a_repeat_sighting_bumps_the_same_row_rather_than_writing_a_second(
    store,
):
    """A claim sighted again is the SAME candidate. A second row would split
    `occurrence_count`, which is §5's recurrence ranking, and would let a
    fresh `pending` row shadow a settled one."""
    first = await store.propose(key=WS, proposals=(_candidate(),), now_ms=NOW)

    second = await store.propose(
        key=WS, proposals=(_candidate(),), now_ms=NOW + 5_000,
    )

    assert second == first, "the re-sighting returns the EXISTING row's id"
    rows = await store.pending(workspace=WS, limit=10)
    assert len(rows) == 1
    assert rows[0].occurrence_count == 2
    assert rows[0].first_seen_at == NOW, "first_seen_at is not moved by a re-sighting"
    assert rows[0].last_seen_at == NOW + 5_000


async def test_a_repeat_sighting_merges_observed_in_without_duplicating(store):
    """`observed_in` is the SET of TURNS a claim was asserted in — §5's Phase
    A emits "candidate triples with the turn each came from", and
    `CandidateClaim.message_ids` are those turns. It accumulates across sightings
    and never duplicates: re-reading the same window would otherwise grow the
    array without bound for a claim nobody repeated.

    Asserted BOTH ways on purpose. A store that appended blindly passes an
    accumulate-only check, and a store that overwrote passes a
    no-duplicates-only check; only doing both at once catches either."""
    await store.propose(key=WS, proposals=(_candidate(message_ids=(101,)),), now_ms=NOW)

    await store.propose(
        key=WS, proposals=(_candidate(message_ids=(101,)),), now_ms=NOW + 1,
    )
    await store.propose(
        key=WS, proposals=(_candidate(message_ids=(102,)),), now_ms=NOW + 2,
    )

    rows = await store.pending(workspace=WS, limit=10)
    assert sorted(rows[0].observed_in) == ["101", "102"]
    assert rows[0].occurrence_count == 3


async def test_one_sighting_across_two_messages_records_both(world):
    """D31 makes an episode a conversational CHUNK, so ONE sighting of a claim
    can carry several turns — distinct from the repeat-sighting case above,
    which merges across CALLS. A store that took only the first id would pass
    every test in this file that existed before plural landed, because each of
    them proposes one id at a time.

    Both implementations, because the fake's list append and the SQL array
    literal are two different pieces of code that must agree — this is exactly
    where a double that quietly differs from production hides a defect."""
    store = world.store
    # Seen ONCE, so without a resolvable subject §5's second drop removes the
    # row and this test would assert against an empty read — passing for a
    # reason that has nothing to do with plural ids.
    await world.seed_entity(canonical_name="e1")
    await store.propose(
        key=WS, proposals=(_candidate(message_ids=(101, 102, 103)),), now_ms=NOW,
    )

    rows = await store.pending(workspace=WS, limit=10)

    assert sorted(rows[0].observed_in) == ["101", "102", "103"]
    # The positive control: occurrence is a count of SIGHTINGS, not of
    # messages. Three ids in one call is one sighting, and a store that
    # conflated them would read as corroboration nobody gave.
    assert rows[0].occurrence_count == 1


async def test_the_proposals_provenance_lands_on_the_row(store):
    """Migration 0027's two columns plus `established_by` — the trio that says
    what produced this row. All three ride on `Proposal` rather than inside
    `payload` (`Proposal`'s own docstring), and a store that drops them writes
    rows that look right and can never be traced back to what produced them.

    Asserted against what the proposal itself carries: the factory mints a
    different value for each of the three on every call, so a store that
    swapped two of them would still be caught."""
    p = fact_candidate_proposal_factory(
        subject="e1", predicate="has", object="v1", message_ids=(1,),
    )

    ids = await store.propose(key=WS, proposals=(p,), now_ms=NOW)

    row = await store.candidate_by_id(workspace=WS, candidate_id=ids[0])
    assert row is not None
    assert row.instruction_sha256 == p.instruction_sha256
    assert row.model == p.model
    assert row.established_by == p.established_by


async def test_a_repeat_sighting_does_not_rewrite_the_establishing_run(store):
    """The provenance trio belongs to the run that ESTABLISHED the row, beside
    `first_seen_at`, and a later sighting did not establish it. Rewriting it
    would leave a row whose digest names an instruction that never produced
    it — worse than no digest, because it reads as a real one."""
    first = fact_candidate_proposal_factory(
        subject="e1", predicate="has", object="v1", message_ids=(1,),
    )
    later = fact_candidate_proposal_factory(
        subject="e1", predicate="has", object="v1", message_ids=(2,),
    )
    assert later.instruction_sha256 != first.instruction_sha256, (
        "the factory must vary provenance per call or this test measures nothing"
    )
    ids = await store.propose(key=WS, proposals=(first,), now_ms=NOW)

    await store.propose(key=WS, proposals=(later,), now_ms=NOW + 1)

    row = await store.candidate_by_id(workspace=WS, candidate_id=ids[0])
    assert row is not None
    assert row.occurrence_count == 2, "the re-sighting really did land"
    assert row.instruction_sha256 == first.instruction_sha256
    assert row.model == first.model
    assert row.established_by == first.established_by


async def test_two_sightings_of_one_triple_in_one_batch_do_not_collide(store):
    """One Phase A extraction over one window can legitimately emit the same
    claim from two turns. A multi-row `ON CONFLICT DO UPDATE` raises
    `cardinality_violation` on exactly this, which is why `propose` issues one
    statement per proposal inside a single transaction."""
    ids = await store.propose(
        key=WS, proposals=(_candidate(), _candidate()), now_ms=NOW,
    )

    assert ids[0] == ids[1]
    rows = await store.pending(workspace=WS, limit=10)
    assert len(rows) == 1
    assert rows[0].occurrence_count == 2


async def test_a_repeat_sighting_does_not_revive_a_settled_candidate(store):
    """§5.1's whole argument: the negative cache is what makes the cost
    bounded. If saying the same unprovable thing again reset `status` to
    `pending`, the tier would re-adjudicate it every tick forever — precisely
    the unbounded cost this table was discovered by costing."""
    ids = await store.propose(key=WS, proposals=(_candidate(),), now_ms=NOW)
    await store.supersede(
        key=WS, target=ids[0], replaced_by=None, because="not_covered",
        source=None, now_ms=NOW + 1,
    )

    await store.propose(key=WS, proposals=(_candidate(),), now_ms=NOW + 2)

    assert await store.pending(workspace=WS, limit=10) == ()
    row = await store.candidate_by_id(workspace=WS, candidate_id=ids[0])
    assert row is not None
    assert (row.status, row.attempts, row.occurrence_count) == ("not_covered", 1, 2)


# ---------------------------------------------------------------------------
# supersede — retire, retain, and the three refusals
# ---------------------------------------------------------------------------


async def test_supersede_retires_the_row_and_keeps_it_readable(store):
    """"Invalidate, never delete" is `DreamSink`'s structural claim, and here
    it is also the point of the table: a DELETED `not_covered` candidate is
    re-extracted next tick, which is the same unbounded cost as never having
    recorded it."""
    ids = await store.propose(key=WS, proposals=(_candidate(),), now_ms=NOW)

    affected = await store.supersede(
        key=WS, target=ids[0], replaced_by=None, because="adjudicated",
        source=None, now_ms=NOW + 7,
    )

    row = await store.candidate_by_id(workspace=WS, candidate_id=ids[0])
    assert affected == 1
    assert row is not None
    assert isinstance(row, FactCandidate)
    assert (row.status, row.last_adjudicated_at, row.attempts) == (
        "adjudicated", NOW + 7, 1,
    )


async def test_a_null_successor_is_legal_here(store):
    """The port permits a null `replaced_by` and this is the SECOND tier to
    need it (the note tier's `retire` was the first): a candidate that could
    not be settled is replaced by nothing at all. `SqlFactStore` refuses the
    same null, which is what makes this a per-implementation promise rather
    than a port-level one."""
    ids = await store.propose(key=WS, proposals=(_candidate(),), now_ms=NOW)

    affected = await store.supersede(
        key=WS, target=ids[0], replaced_by=None, because="not_covered",
        source=None, now_ms=NOW + 1,
    )

    assert affected == 1


async def test_supersede_refuses_a_non_terminal_because(store):
    """`fact_candidate_status_check` admits `pending`, which is a legal status
    for a ROW and not a legal reason for a RETIREMENT — deferring this to the
    CHECK the way `SqlFactStore` defers `stale|wrong|merged` would let a
    supersession silently un-settle a row and hand it straight back to
    `pending()`."""
    ids = await store.propose(key=WS, proposals=(_candidate(),), now_ms=NOW)

    with pytest.raises(ValueError):
        await store.supersede(
            key=WS, target=ids[0], replaced_by=None, because="pending",
            source=None, now_ms=NOW + 1,
        )


async def test_supersede_refuses_a_successor_it_cannot_record(store):
    """`replaced_by` is the successor IN THIS TIER, and no candidate succeeds
    another — an adjudicated candidate is succeeded by a `fact`, in a
    different table. There is no column for it, so accepting it and dropping
    it would report a successor recorded that nothing recorded, and nothing
    could ever tell."""
    ids = await store.propose(key=WS, proposals=(_candidate(),), now_ms=NOW)

    with pytest.raises(ValueError):
        await store.supersede(
            key=WS, target=ids[0], replaced_by=ids[0], because="adjudicated",
            source=None, now_ms=NOW + 1,
        )


async def test_supersede_refuses_a_source_it_cannot_record(store):
    """Same loss, other field: a supersession's warrant belongs on the `fact`
    row (`fact.supersede_source`). Silently dropping a citation the caller
    believed was stored is exactly the ERROR-as-`ok` shape R-27 forbids one
    layer up."""
    ids = await store.propose(key=WS, proposals=(_candidate(),), now_ms=NOW)

    with pytest.raises(ValueError):
        await store.supersede(
            key=WS, target=ids[0], replaced_by=None, because="adjudicated",
            source="https://keengamer.test/big-walk", now_ms=NOW + 1,
        )


@pytest.mark.parametrize(
    "target", ["not-a-uuid", "3f2504e0-4f89-11d3-9a0c-0305e82c3301"],
)
async def test_superseding_an_unknown_target_reports_zero_rather_than_raising(
    store, target,
):
    """A background pass that raises on a row it does not recognise takes the
    whole tick with it. Both shapes are "unknown": a well-formed id nobody
    holds, and a string `fact_candidate.id`'s `pg.UUID` column cannot even
    parse — the second would otherwise surface as a raw asyncpg type error
    rather than the ordinary zero."""
    await store.propose(key=WS, proposals=(_candidate(),), now_ms=NOW)

    affected = await store.supersede(
        key=WS, target=target, replaced_by=None, because="adjudicated",
        source=None, now_ms=NOW,
    )

    assert affected == 0


async def test_superseding_twice_affects_nothing_the_second_time(store):
    """Idempotent, because the Dreamer loop can legitimately re-fire over work
    it already did — and because re-adjudicating a settled candidate every
    tick is the unbounded cost the negative cache exists to bound."""
    ids = await store.propose(key=WS, proposals=(_candidate(),), now_ms=NOW)
    first = await store.supersede(
        key=WS, target=ids[0], replaced_by=None, because="not_covered",
        source=None, now_ms=NOW + 1,
    )

    second = await store.supersede(
        key=WS, target=ids[0], replaced_by=None, because="not_covered",
        source=None, now_ms=NOW + 2,
    )

    row = await store.candidate_by_id(workspace=WS, candidate_id=ids[0])
    assert (first, second) == (1, 0)
    assert row is not None
    assert row.last_adjudicated_at == NOW + 1, "the second call re-stamped nothing"
    assert row.attempts == 1


async def test_a_key_cannot_supersede_another_keys_row(store):
    ids = await store.propose(key="workspace-a", proposals=(_candidate(),), now_ms=NOW)

    affected = await store.supersede(
        key="workspace-b", target=ids[0], replaced_by=None, because="adjudicated",
        source=None, now_ms=NOW + 1,
    )

    assert affected == 0


# ---------------------------------------------------------------------------
# pending — §5's deterministic filter, which is CODE, not a model
# ---------------------------------------------------------------------------


async def test_pending_never_returns_a_settled_candidate(store):
    """`not_covered` is the negative cache. Returning it would re-spend the
    research loop that already failed to settle it — the difference between a
    bounded cost and an unbounded one, stated as a query."""
    # Two sightings each: both candidates must SURVIVE §5's
    # unresolvable-subject-seen-once drop, or this test would pass even for a
    # store that returned nothing at all.
    settled = await store.propose(
        key=WS, proposals=(_candidate(), _candidate()), now_ms=NOW,
    )
    still_open = await store.propose(
        key=WS,
        proposals=(_candidate(object="v2"), _candidate(object="v2")),
        now_ms=NOW,
    )
    await store.supersede(
        key=WS, target=settled[0], replaced_by=None, because="not_covered",
        source=None, now_ms=NOW + 1,
    )

    rows = await store.pending(workspace=WS, limit=10)

    assert [c.id for c in rows] == [still_open[0]]


async def test_pending_drops_a_candidate_already_backed_by_a_live_fact(world):
    """§5's first drop: there is nothing left to prove, and proving it again
    costs one research loop — the expensive step this filter exists to
    ration."""
    store = world.store
    # The entity is seeded so the candidate would otherwise SURVIVE: without
    # it the seen-once-unresolvable drop removes the row for a different
    # reason and this test passes without ever exercising the first drop.
    await world.seed_entity(canonical_name="e1")
    await world.seed_fact(subject="e1", object="v1")
    await store.propose(
        key=WS, proposals=(_candidate(subject="e1", object="v1"),), now_ms=NOW,
    )

    assert await store.pending(workspace=WS, limit=10) == ()


async def test_pending_drops_a_candidate_backed_by_a_fact_with_a_resolved_object(
    world,
):
    """The negative cache broke exactly here (`21-substrate-contract.md`
    §6.4). Before migration 0039 a promoted object made `object_literal`
    NULL, dedup compares that column, and `NULL = 'x'` is NULL — so the claim
    was never deduped, was re-adjudicated every tick forever, and ranked FIRST
    each time because the contradiction predicate then read it as
    contradicting its own candidate.

    The literal now survives promotion, so resolving an object changes nothing
    about whether its claim is already proven. The assertion is the SAME as
    the unresolved case one test up; what varies is only the promotion."""
    store = world.store
    await world.seed_entity(canonical_name="e1")
    object_entity = await world.seed_entity(canonical_name="v1")
    await world.seed_fact(subject="e1", object="v1")
    await world.resolve_object(object="v1", entity_id=object_entity)
    await store.propose(
        key=WS, proposals=(_candidate(subject="e1", object="v1"),), now_ms=NOW,
    )

    assert await store.pending(workspace=WS, limit=10) == ()


async def test_a_resolved_object_still_ranks_a_genuine_contradiction_first(world):
    """The positive control for the test above, and the other half of the same
    defect. `== ()` is also true of a store that returned nothing because the
    seeding failed, so this asserts something only a run that RAN produces:
    against the SAME resolved fact, a candidate proposing a DIFFERENT object
    comes back, and comes back as a contradiction rather than as ordinary
    recurrence."""
    store = world.store
    await world.seed_entity(canonical_name="e1")
    await world.seed_entity(canonical_name="other")
    object_entity = await world.seed_entity(canonical_name="v1")
    await world.seed_fact(subject="e1", object="v1")
    await world.resolve_object(object="v1", entity_id=object_entity)
    contradiction = await store.propose(
        key=WS, proposals=(_candidate(subject="e1", object="v2"),), now_ms=NOW,
    )
    # A SECOND candidate, contradicting nothing and seen more often. Without it
    # the assertion below was a one-element list, which every possible ordering
    # satisfies — the ranking clause could be deleted outright and the test
    # stayed green.
    ordinary = await store.propose(
        key=WS,
        proposals=(_candidate(subject="other", predicate="p", object="o"),),
        now_ms=NOW,
    )
    await store.propose(
        key=WS,
        proposals=(_candidate(subject="other", predicate="p", object="o"),),
        now_ms=NOW + 1,
    )

    rows = await store.pending(workspace=WS, limit=10)

    assert [c.id for c in rows] == [contradiction[0], ordinary[0]]


async def test_pending_keeps_a_candidate_whose_backing_fact_was_superseded(world):
    """The drop is on a LIVE fact, not any fact. A superseded row is history —
    the belief it recorded is no longer held, so the claim is unproven again
    and worth a loop. Without this the negative half of the first drop would
    be indistinguishable from a filter that simply never fires."""
    store = world.store
    await world.seed_entity(canonical_name="e1")
    await world.seed_fact(subject="e1", object="v1", superseded_by_object="v2")
    ids = await store.propose(
        key=WS, proposals=(_candidate(subject="e1", object="v1"),), now_ms=NOW,
    )

    rows = await store.pending(workspace=WS, limit=10)

    assert [c.id for c in rows] == [ids[0]]


async def test_pending_drops_an_unresolvable_subject_seen_once(world):
    """§5's second drop: one-off noise. Seen once AND naming a subject no
    `fact_entity` in the workspace knows."""
    store = world.store
    await store.propose(
        key=WS, proposals=(_candidate(subject="who-even", object="v1"),),
        now_ms=NOW,
    )

    assert await store.pending(workspace=WS, limit=10) == ()


async def test_pending_keeps_an_unresolvable_subject_seen_twice(world):
    """Both halves of the drop are load-bearing: recurrence is what turns
    noise into a claim worth checking. A filter that dropped on the subject
    alone would silence every genuinely new entity the tier has not met yet."""
    store = world.store
    ids = await store.propose(
        key=WS, proposals=(_candidate(subject="who-even"),), now_ms=NOW,
    )
    await store.propose(
        key=WS, proposals=(_candidate(subject="who-even"),), now_ms=NOW + 1,
    )

    rows = await store.pending(workspace=WS, limit=10)

    assert [c.id for c in rows] == [ids[0]]


async def test_pending_keeps_a_once_seen_candidate_whose_subject_resolves_by_alias(
    world,
):
    """`FactEntity.aliases` is load-bearing at workspace scope — `big walk` /
    `Big Walk` / 大步行 must resolve to ONE entity. Checking `canonical_name`
    alone would drop a candidate about `big walk` as unresolvable noise
    because the entity happens to be stored as `Big Walk`."""
    store = world.store
    await world.seed_entity(canonical_name="Big Walk", aliases=("big walk", "大步行"))
    ids = await store.propose(
        key=WS, proposals=(_candidate(subject="big walk"),), now_ms=NOW,
    )

    rows = await store.pending(workspace=WS, limit=10)

    assert [c.id for c in rows] == [ids[0]]


async def test_pending_ranks_a_contradiction_ahead_of_a_more_frequent_candidate(world):
    """§5's third rank, and the tier's PURPOSE: «there is no black tower»
    asserted against a standing fact is the case this whole tier was built
    for. Asserted against a rival with a HIGHER `occurrence_count` on purpose
    — a filter that ordered by recurrence first would still look like it
    works, and would just spend the tick on the popular claim instead of the
    contradicting one."""
    store = world.store
    await world.seed_entity(canonical_name="tower")
    await world.seed_fact(subject="tower", predicate="has", object="black_tower")
    contradicting = await store.propose(
        key=WS,
        proposals=(_candidate(subject="tower", object="no_black_tower"),),
        now_ms=NOW,
    )
    popular = await store.propose(
        key=WS, proposals=(_candidate(subject="e9", object="v9"),), now_ms=NOW,
    )
    for tick in range(1, 4):
        await store.propose(
            key=WS, proposals=(_candidate(subject="e9", object="v9"),),
            now_ms=NOW + tick,
        )

    rows = await store.pending(workspace=WS, limit=10)

    assert [c.id for c in rows] == [contradicting[0], popular[0]]


async def test_pending_orders_the_rest_by_recurrence_then_recency(world):
    """§5's last rank. Recurrence gates which candidates are worth a loop,
    never whether a fact is TRUE — a single authoritative source establishes a
    fact, which is why `room_lexeme`'s scoring model is right for slang and
    wrong here."""
    store = world.store
    await world.seed_entity(canonical_name="e-often")
    await world.seed_entity(canonical_name="e-recent")
    await world.seed_entity(canonical_name="e-stale")
    often = await store.propose(
        key=WS, proposals=(_candidate(subject="e-often"),), now_ms=NOW,
    )
    await store.propose(
        key=WS, proposals=(_candidate(subject="e-often"),), now_ms=NOW + 1,
    )
    stale = await store.propose(
        key=WS, proposals=(_candidate(subject="e-stale"),), now_ms=NOW,
    )
    recent = await store.propose(
        key=WS, proposals=(_candidate(subject="e-recent"),), now_ms=NOW + 9,
    )

    rows = await store.pending(workspace=WS, limit=10)

    assert [c.id for c in rows] == [often[0], recent[0], stale[0]]


async def test_pending_caps_at_limit(store):
    await store.propose(
        key=WS,
        proposals=(
            _candidate(subject="a", object="v"),
            _candidate(subject="a", object="v"),
            _candidate(subject="b", object="v"),
            _candidate(subject="b", object="v"),
        ),
        now_ms=NOW,
    )

    rows = await store.pending(workspace=WS, limit=1)

    assert len(rows) == 1


async def test_pending_with_a_negative_limit_clamps_to_empty(store):
    """The trap `SqlFactStore.standing_facts` documents, one table over:
    Postgres errors on a negative `LIMIT`, while `list[:-1]` silently keeps
    every candidate but the last — a DIFFERENT WRONG ANSWER to the same
    question. Both implementations clamp before doing any work, so they
    agree."""
    # TWO surviving rows, not one: `list[:-1]` over a single-element list is
    # `[]`, which would agree with the clamp by accident and prove nothing.
    await store.propose(
        key=WS,
        proposals=(
            _candidate(object="v1"), _candidate(object="v1"),
            _candidate(object="v2"), _candidate(object="v2"),
        ),
        now_ms=NOW,
    )
    assert len(await store.pending(workspace=WS, limit=10)) == 2

    assert await store.pending(workspace=WS, limit=-1) == ()
    assert await store.pending(workspace=WS, limit=0) == ()


# ---------------------------------------------------------------------------
# the sink's own refusals and readings — SQL only, because both are the
# STORE's behaviour and the double deliberately has neither
# ---------------------------------------------------------------------------


@pytest.fixture
async def sql_sink(candidate_pg_url):
    engine = create_sql_engine(candidate_pg_url)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "TRUNCATE fact_premise, fact, fact_candidate, fact_entity "
            "RESTART IDENTITY CASCADE"
        ))
        # The attribution tests below write `messages` rows; the module-scoped
        # database keeps them across tests, so clear them here rather than let
        # one test's sender leak into the next one's join.
        await conn.execute(sa.text("delete from messages"))
    metrics = Metrics()
    try:
        yield SqlFactCandidateStore(
            engine,
            roster=frozenset({"miles", "grant"}),
            roster_names=frozenset({"Miles Novak", "阿Miles", "Grace"}),
            metrics=metrics,
        ), engine, metrics
    finally:
        await engine.dispose()


async def test_a_claim_about_a_roster_person_is_refused_at_the_sink(sql_sink):
    """25 §2.4. The objective's first exclusion is "anything about a person in
    this room", and a probe measured the model breaking it at five
    person-claims in six edges WITH the roster supplied — so the boundary is
    checkable and not enforced by the prompt. 17 §7's rule then applies: refuse
    loudly, never accept and forget.

    The sibling claim surviving is the positive control: a sink that refused
    everything would satisfy the first assertion on its own."""
    store, _engine, metrics = sql_sink

    ids = await store.propose(
        key=WS,
        proposals=(
            _candidate(subject="MILES", predicate="likes", object="ramen"),
            _candidate(subject="big walk", predicate="made_by", object="hh"),
        ),
        now_ms=NOW,
    )

    assert len(ids) == 1
    assert metrics.get(
        "nora_fact_person_claim_refused_total", matched="person_id") == 1


async def test_a_person_in_object_position_is_not_refused(sql_sink):
    """21 §2's own worked example: "Grant's wedding is on the 9th" is a claim
    about an EVENT with Grant in object position, not a claim about Grant's
    disposition. The restriction bites on predicates ABOUT a person, which is
    what a person-SUBJECT triple is — and a refusal that read both positions
    would delete the case the design uses to explain itself."""
    store, _engine, metrics = sql_sink

    ids = await store.propose(
        key=WS,
        proposals=(_candidate(subject="the wedding", predicate="of",
                              object="Grant"),),
        now_ms=NOW,
    )

    assert len(ids) == 1
    assert metrics.get(
        "nora_fact_person_claim_refused_total", matched="person_id") == 0
    assert metrics.get(
        "nora_fact_person_claim_refused_total", matched="display_name") == 0


async def test_an_empty_roster_refuses_nothing(sql_sink, candidate_pg_url):
    """The degradation that matters. A sink whose roster nobody wired must
    refuse NOTHING, not everything — the second would be a worse failure than
    the one this guard exists for, and it would look identical to a quiet
    room."""
    engine = create_sql_engine(candidate_pg_url)
    try:
        store = SqlFactCandidateStore(engine)
        ids = await store.propose(
            key=WS,
            proposals=(_candidate(subject="miles", predicate="likes",
                                  object="ramen"),),
            now_ms=NOW,
        )
        assert len(ids) == 1
    finally:
        await engine.dispose()


async def test_an_unresolvable_subject_is_counted_not_merely_dropped(sql_sink):
    """`nora_fact_entity_unresolved_total` was declared with ZERO call sites —
    the tier's only instrument for the entity problem, unable to observe it.
    This is that call site.

    Two candidates, one resolvable and one not, so the number is a reading and
    not a row count: a counter that simply reported every pending candidate
    would pass an assertion of 1 on a one-row store."""
    store, engine, metrics = sql_sink
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "insert into fact_entity (id, workspace, canonical_name, "
            "created_at) values (gen_random_uuid(), :w, 'big walk', 1)"
        ), {"w": WS})
    await store.propose(
        key=WS,
        proposals=(
            _candidate(subject="big walk", predicate="p", object="o"),
            _candidate(subject="who-even", predicate="p", object="o"),
        ),
        now_ms=NOW,
    )

    await store.pending(workspace=WS, limit=10)

    assert metrics.get("nora_fact_entity_unresolved", workspace=WS) == 1


async def test_a_claim_about_a_person_by_the_name_the_room_uses_is_refused(sql_sink):
    """The half an id-only match misses, and the half that matters: nobody
    writes `miles` in a sentence, they write the name the group calls him. A
    claim about a person in subject position is a fact about a human being
    living in the wrong tier — that is Honcho's axis, and an id-only refusal
    leaves the entire class reachable through the ordinary name.

    Counted on its OWN label, so over-refusal by name is findable rather than
    hidden inside the id series."""
    store, _engine, metrics = sql_sink

    ids = await store.propose(
        key=WS,
        proposals=(
            _candidate(subject="阿Miles", predicate="lives_in", object="TST"),
            _candidate(subject="big walk", predicate="made_by", object="hh"),
        ),
        now_ms=NOW,
    )

    assert len(ids) == 1
    assert metrics.get(
        "nora_fact_person_claim_refused_total", matched="display_name") == 1
    assert metrics.get(
        "nora_fact_person_claim_refused_total", matched="person_id") == 0


async def test_the_name_match_is_exact_and_never_a_prefix(sql_sink):
    """The rule this stage earned the hard way. The promotion veto's month
    abbreviations matched any word STARTING with them, so `mar` refused
    "Marco's car" and `may` refused "maybe". A display name under a prefix
    match would eat ordinary words — and over-refusing is not free, because it
    silently deletes legitimate facts nobody will ever know were dropped.

    "Grace" is on this roster; "Grace Period" and "graceful" are not people."""
    store, _engine, metrics = sql_sink

    ids = await store.propose(
        key=WS,
        proposals=(
            _candidate(subject="Grace Period", predicate="is", object="30 days"),
            _candidate(subject="gracefulness", predicate="is", object="a virtue"),
        ),
        now_ms=NOW,
    )

    assert len(ids) == 2
    assert metrics.get(
        "nora_fact_person_claim_refused_total", matched="display_name") == 0
    # The positive control: the exact name IS refused, so the zero above is
    # about the prefix and not about a matcher that never fires.
    await store.propose(
        key=WS,
        proposals=(_candidate(subject="grace", predicate="likes", object="x"),),
        now_ms=NOW,
    )
    assert metrics.get(
        "nora_fact_person_claim_refused_total", matched="display_name") == 1


async def test_a_candidate_over_the_operating_cap_is_dropped_and_counted(sql_sink):
    """The candidate store DROPS where the fact store RAISES, and the asymmetry
    is the tier's own: a candidate is one sighting of one claim and losing it
    costs a re-extraction, while a verdict has already spent a research loop.

    Counted either way, because a silent drop reads as "the model found
    nothing"."""
    _store, engine, metrics = sql_sink
    capped = SqlFactCandidateStore(engine, max_term_chars=50, metrics=metrics)

    ids = await capped.propose(
        key=WS,
        proposals=(
            _candidate(subject="x" * 51, predicate="p", object="o"),
            _candidate(subject="big walk", predicate="p", object="o"),
        ),
        now_ms=NOW,
    )

    assert len(ids) == 1
    assert metrics.get(
        "nora_fact_person_claim_refused_total", matched="over_cap") == 1


async def test_the_settle_counter_moves_once_per_settled_row(sql_sink):
    """**`nora_fact_candidates_total` had no call site at all** — declared at
    the tier's first commit and emitted nowhere, which renders as a series
    that is genuinely always zero.

    It is the instrument an operator reaches for to ask «did the tier produce
    anything», so a null from it was indistinguishable from a null from a tier
    that never ran — CLAUDE.md's rule about checking an instrument can measure
    the thing before reading it, failing on the counter most likely to be read
    first.

    Labelled by the terminal status, so the two outcomes are separable: a
    counter that lumped `not_covered` in with `adjudicated` would report a
    tier settling work while every settle was a failure to prove anything.
    """
    store, _engine, metrics = sql_sink
    ids = await store.propose(
        key=WS,
        proposals=(
            _candidate(subject="big walk", predicate="made_by", object="hh"),
            _candidate(subject="打風", predicate="means", object="a typhoon"),
        ),
        now_ms=NOW,
    )
    assert len(ids) == 2

    await store.supersede(
        key=WS, target=ids[0], replaced_by=None,
        because="adjudicated", source=None, now_ms=NOW + 1,
    )
    await store.supersede(
        key=WS, target=ids[1], replaced_by=None,
        because="not_covered", source=None, now_ms=NOW + 1,
    )

    assert metrics.get("nora_fact_candidates_total", outcome="adjudicated") == 1
    assert metrics.get("nora_fact_candidates_total", outcome="not_covered") == 1

    # Idempotence, and it is why the increment is guarded on `rowcount`: the
    # Dreamer loop legitimately re-fires over work it already did, and a
    # counter that moved on a no-op would report the tier settling candidates
    # that were settled on a previous tick.
    await store.supersede(
        key=WS, target=ids[0], replaced_by=None,
        because="adjudicated", source=None, now_ms=NOW + 2,
    )
    assert metrics.get("nora_fact_candidates_total", outcome="adjudicated") == 1


async def test_the_pending_queue_is_stable_across_reads_of_unchanged_state(store):
    """**This queue decides which candidates spend the tick's research
    budget**, and all three of its ordering keys tie routinely: one propose
    batch stamps every row with one `last_seen_at`, and candidates commonly
    share `occurrence_count = 1`. Postgres may return any order among equals,
    so the tick adjudicated a different set each run and a candidate past the
    limit could be starved indefinitely.

    Six candidates read four at a time: the LIMIT has to be under the
    population or every ordering returns the same set and the test passes
    against an unordered query. Read twice, because the property is «identical
    stored state, identical answer» rather than «this particular subset».

    Each candidate is proposed TWICE so it survives §5's
    seen-once-unresolvable drop — otherwise this would pass against a store
    that returned nothing at all.
    """
    ids = list(dict.fromkeys(await store.propose(
        key=WS,
        proposals=tuple(
            _candidate(object=f"v{i}") for i in range(6) for _ in (0, 1)
        ),
        now_ms=NOW,
    )))
    # `propose` returns one id PER PROPOSAL, and a repeat of the same triple
    # upserts onto the same row — so twelve proposals give twelve ids over six
    # distinct rows. Deduped here rather than proposed once, because a single
    # sighting is dropped by the seen-once-unresolvable filter and the queue
    # would be empty.
    assert len(ids) == 6

    first = await store.pending(workspace=WS, limit=4)
    again = await store.pending(workspace=WS, limit=4)

    assert [c.id for c in first] == sorted(ids)[:4], (
        "the queue must be a deterministic prefix of the eligible set"
    )
    assert [c.id for c in again] == [c.id for c in first]


def test_the_pending_query_declares_its_tie_break_in_sql():
    """The half no execution can carry. Removing the tie-break and observing a
    stable order proves only that this size and this statistics snapshot
    happened to plan a sort — a reviewer deleted it and 1046 tests stayed
    green.

    The docstring is stripped first: a structural check that reads prose is an
    instrument that cannot fail, which is how the first version of this
    pattern passed with the clause deleted.
    """
    fn = SqlFactCandidateStore.pending
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
    ):
        del tree.body[0]
    source = ast.unparse(tree)

    assert "c.c.id" in source, (
        "pending() slices a LIMIT off a three-key order whose keys tie "
        "routinely; without a unique tie-break the research budget is spent "
        "arbitrarily"
    )


# ---------------------------------------------------------------------------
# the adjudication queue says when its bound bit (`nora.domain.bounds`)
# ---------------------------------------------------------------------------


async def _seed_surviving(store, n: int) -> None:
    """`n` candidates that survive §5's two drops.

    Proposed TWICE on purpose: the unresolvable-subject-seen-once drop removes
    a candidate whose subject resolves to no `fact_entity` and has been seen
    exactly once, so a single sighting would leave `pending` empty and every
    assertion below would pass for the wrong reason.
    """
    proposals = tuple(
        _candidate(subject=f"thing-{i}", predicate="has", object=f"v{i}")
        for i in range(n)
    )
    await store.propose(key=WS, proposals=proposals, now_ms=NOW)
    await store.propose(key=WS, proposals=proposals, now_ms=NOW + 1)


async def test_a_clipped_adjudication_queue_is_counted(sql_sink):
    """The FIRST site wired, and the reason for that ordering: this is the one
    bound on the tier's WRITE path. Every other truncation clips a VIEW of
    state and is in principle recoverable from a later read; a capped
    adjudication queue is invisible from every read below it, because the row
    was never promoted — the claim stays `pending` and `standing_facts`
    correctly reports nothing about a subject Nora has an unprocessed claim
    about. That is «I have not checked yet» wearing `nothing_found`'s face, at
    the bottom of the tier."""
    store, _engine, metrics = sql_sink
    await _seed_surviving(store, 9)

    rows = await store.pending(workspace=WS, limit=8)

    assert len(rows) == 8, "the over-fetched row must never escape"
    assert metrics.get(
        "nora_bound_truncated_total", site="fact_candidates.pending") == 1


async def test_a_queue_that_exactly_fits_is_not_counted(sql_sink):
    """THE CASE THAT MAKES THE SERIES MEAN ANYTHING, and the positive control
    for the test above: eight candidates under a limit of eight is a full page
    with nothing behind it. A counter that fired here would be measuring «the
    tick was busy», which is true of every healthy run, and the two readings
    would be indistinguishable."""
    store, _engine, metrics = sql_sink
    await _seed_surviving(store, 8)

    rows = await store.pending(workspace=WS, limit=8)

    assert len(rows) == 8
    assert metrics.get(
        "nora_bound_truncated_total", site="fact_candidates.pending") == 0


async def test_the_unresolved_census_is_not_narrowed_to_one_page(sql_sink):
    """`nora_fact_entity_unresolved` is a census of the WHOLE pending queue,
    and adding a page bound next to it invites deriving one from the other.
    This pins that it is not: the gauge must come from its own unbounded
    statement, so it reads 9 whatever page size was asked for. A census
    narrowed to the page would look healthier the more backlog there was —
    the opposite of what it is for.

    FALSIFIED (2026-08-29): clamping the census to `limit` in
    `SqlFactCandidateStore.pending` turns this red (2.0 != 9)."""
    store, _engine, metrics = sql_sink
    await _seed_surviving(store, 9)

    await store.pending(workspace=WS, limit=2)

    assert metrics.get("nora_fact_entity_unresolved", workspace=WS) == 9


# ---------------------------------------------------------------------------
# asserted_by / asserted_at — the propose-time messages join (32 §3.4)
# ---------------------------------------------------------------------------
#
# SQL only, for the same reason the fact tier's attribution-join tests are:
# the join walks `messages`, a table `InMemoryFactCandidateStore` does not
# hold, and a double that faked attribution would make every green test here
# a claim about the double. The double's `FactCandidate` defaults carry the
# None instead.


async def _seed_message(
    engine, *, mid: int, person_id: str | None, ts: int,
) -> None:
    """One `messages` row — the same shape `tests/infra/test_fact_store.py`'s
    helper writes, one tier up. `person_id` NULL is the unmapped sender:
    resolved at ingest or not at all, which is why `sender_id` is the
    fallback rather than a follow-up lookup."""
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "insert into messages (id, platform, conversation_id, "
                "conversation_type, sender_id, ts, body, person_id) values "
                "(:id, 'signal', 'g1', 'group', :s, :ts, 'x', :p)"
            ),
            {"id": mid, "ts": ts, "s": f"sender-{mid}", "p": person_id},
        )


async def test_propose_attributes_the_claim_to_its_first_message(sql_sink):
    """32 §3.4: `asserted_by` is the FIRST message's `person_id`,
    `asserted_at` its `ts` — when the claim was SAID, which neither
    `first_seen_at` nor `last_seen_at` (when it was SEEN) can say.

    The second message belongs to somebody else and is the positive control:
    a join that took the LAST id, or any id, would attribute to grant and
    pass a test that seeded only one message."""
    store, engine, _ = sql_sink
    await _seed_message(engine, mid=41, person_id="miles", ts=NOW - 60_000)
    await _seed_message(engine, mid=42, person_id="grant", ts=NOW - 30_000)

    ids = await store.propose(
        key=WS, proposals=(_candidate(message_ids=(41, 42)),), now_ms=NOW,
    )

    row = await store.candidate_by_id(workspace=WS, candidate_id=ids[0])
    assert row is not None
    assert (row.asserted_by, row.asserted_at) == ("miles", NOW - 60_000)


async def test_an_unmapped_sender_falls_back_to_the_sender_id(sql_sink):
    """An unmapped sender has no `person_id` at ingest, by design. NULL here
    would say "unattributable" about a message that is right there; the
    platform id is who said it as far as the room is concerned."""
    store, engine, _ = sql_sink
    await _seed_message(engine, mid=41, person_id=None, ts=NOW - 60_000)

    ids = await store.propose(
        key=WS, proposals=(_candidate(message_ids=(41,)),), now_ms=NOW,
    )

    row = await store.candidate_by_id(workspace=WS, candidate_id=ids[0])
    assert row is not None
    assert (row.asserted_by, row.asserted_at) == ("sender-41", NOW - 60_000)


async def test_a_message_the_join_cannot_find_attributes_nothing(sql_sink):
    """`prune_all` deletes messages by conversation on a schedule while
    `observed_in` survives it. A claim whose evidence is gone writes NULL for
    BOTH fields — the join finding nothing is never an invented asserter.
    The row itself is the positive control: the candidate IS written, only
    its attribution is empty."""
    store, _engine, _ = sql_sink

    ids = await store.propose(
        key=WS, proposals=(_candidate(message_ids=(424242,)),), now_ms=NOW,
    )

    row = await store.candidate_by_id(workspace=WS, candidate_id=ids[0])
    assert row is not None
    assert (row.asserted_by, row.asserted_at) == (None, None)


async def test_a_re_sighting_does_not_move_the_attribution(sql_sink):
    """INSERT-only, beside `first_seen_at` and the provenance trio: the pair
    records the FIRST assertion, and a later sighting — even one from a
    different sender — did not assert the claim first. `occurrence_count`
    moving is the positive control: the re-sighting landed, and only the
    attribution stayed where the insert put it."""
    store, engine, _ = sql_sink
    await _seed_message(engine, mid=41, person_id="miles", ts=NOW - 60_000)
    await _seed_message(engine, mid=42, person_id="grant", ts=NOW - 30_000)
    ids = await store.propose(
        key=WS, proposals=(_candidate(message_ids=(41,)),), now_ms=NOW,
    )

    again = await store.propose(
        key=WS, proposals=(_candidate(message_ids=(42,)),), now_ms=NOW + 1,
    )

    assert again == ids
    row = await store.candidate_by_id(workspace=WS, candidate_id=ids[0])
    assert row is not None
    assert row.occurrence_count == 2
    assert (row.asserted_by, row.asserted_at) == ("miles", NOW - 60_000)
