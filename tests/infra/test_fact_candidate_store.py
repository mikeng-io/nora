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
    import uuid

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
    §6.4). Before migration 0038 a promoted object made `object_literal`
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
