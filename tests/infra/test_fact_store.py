"""Fact-tier-specific behaviour `tests/contract/test_dream_sink_contract.py`
deliberately does not assert — that file is `DreamSink`'s shared shape, and
these are the FACT TIER's own rules: `SqlFactStore`'s own docstring names
three of them explicitly (the `isinstance` refusal, `not_covered` writing
nothing, the null-`replaced_by` refusal), and `nora.domain.facts.Fact`'s own
CHECK-shaped invariants give the rest (workspace scoping, a superseded row
staying readable by id, `standing_facts` excluding it).

Parametrized over BOTH fact-tier implementations —
`nora.infra.sql.facts.SqlFactStore` and `nora.testing.InMemoryFactStore` —
because `InMemoryFactStore`'s own docstring commits to "the SAME three
refusals `SqlFactStore` does", and a refusal asserted against only one
implementation is a claim about that implementation, not about the tier.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from nora.domain.facts import (
    FACT_PREDICATE_CEILING_CHARS,
    FACT_TERM_CEILING_CHARS,
    Fact,
    FactAsserter,
    FactVerdict,
)
from nora.domain.ports import Proposal
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.entities import SqlEntityStore
from nora.infra.sql.facts import SqlFactStore, oversized_terms
from nora.infra.sql.migrations_runner import run_migrations
from nora.obs.metrics import Metrics
from nora.testing import InMemoryFactStore, fact_proposal_factory

NOW = 1_755_100_000_000


def _verdict(*, subject="e1", predicate="has", object="v1", status="supported",
             addresses_claim=True, source_url="https://x.test/a",
             source_excerpt="the excerpt", supersedes=None) -> FactVerdict:
    return FactVerdict(
        subject=subject, predicate=predicate, object=object, status=status,
        addresses_claim=addresses_claim, source_url=source_url,
        source_excerpt=source_excerpt, supersedes=supersedes,
    )


def _proposal(
    verdict: FactVerdict, *, established_by="run-1", candidate_id=None,
) -> Proposal:
    return Proposal(
        payload=verdict, instruction_sha256="a" * 64,
        model="ollamacloud/deepseek-v4-flash", established_by=established_by,
        candidate_id=candidate_id,
    )


@pytest.fixture(params=["memory", "sql"])
async def store(request):
    if request.param == "memory":
        yield InMemoryFactStore()
        return
    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield SqlFactStore(engine)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# workspace isolation
# ---------------------------------------------------------------------------


async def test_a_fact_never_surfaces_in_a_sibling_workspace(store):
    """`workspace` is `Conversation.workspace` (DEC-105) — the same key R-49
    refuses to relay across. A fact from workspace A rendering into B's
    `standing_facts` would be exactly that leak, one layer down."""
    a_ids = await store.propose(
        key="workspace-a", proposals=(_proposal(_verdict(subject="shared-subject")),),
        now_ms=NOW,
    )

    b_facts = await store.standing_facts(
        workspace="workspace-b", subject_ids=["shared-subject"], limit=10,
    )
    a_facts = await store.standing_facts(
        workspace="workspace-a", subject_ids=["shared-subject"], limit=10,
    )

    assert b_facts == ()
    assert len(a_facts) == 1
    assert a_facts[0].id == a_ids[0]


async def test_fact_by_id_is_scoped_to_its_own_workspace(store):
    """The same isolation on the by-id read: naming a real id from the WRONG
    workspace must not leak the row, the way a URL guessed from another
    tenant must not leak a record in any multi-tenant store."""
    ids = await store.propose(
        key="workspace-a", proposals=(_proposal(_verdict()),), now_ms=NOW,
    )

    assert await store.fact_by_id(workspace="workspace-b", fact_id=ids[0]) is None
    assert await store.fact_by_id(workspace="workspace-a", fact_id=ids[0]) is not None


# ---------------------------------------------------------------------------
# the null-replaced_by refusal — SqlFactStore.supersede's own docstring (3)
# ---------------------------------------------------------------------------


async def test_supersede_refuses_a_null_successor(store):
    """`fact`'s CHECK is `(superseded_by is null) = (superseded_at is null)`
    — a biconditional. This store refuses the null itself, before ever
    reaching the database, and that is a FACT-TIER rule (this CHECK), not a
    `DreamSink` rule — the port itself permits a null `replaced_by`."""
    ids = await store.propose(
        key="workspace-a", proposals=(_proposal(_verdict()),), now_ms=NOW,
    )

    with pytest.raises(ValueError):
        await store.supersede(
            key="workspace-a", target=ids[0], replaced_by=None, because="stale",
            source=None, now_ms=NOW + 1,
        )


async def test_supersede_refuses_a_malformed_replaced_by(request):
    """`nora.infra.sql.facts.SqlFactStore`-specific (2026-08-21 review
    fix): a `replaced_by` that is non-null but not UUID-shaped is a CALLER
    BUG, not an absent row — before this fix it was swallowed by the same
    `except ValueError: return 0` guard that legitimately covers an unknown
    `target`, making a caller mistake indistinguishable from "already
    superseded" or "no such row" (an ERROR silently surfacing as
    `nothing_found`, R-27's forbidden shape). A well-formed but nonexistent
    `replaced_by` already raises `IntegrityError` via the `superseded_by`
    FK; this proves the malformed case raises too, rather than returning 0
    depending only on whether the string happens to parse as a UUID.

    Not parametrized over `InMemoryFactStore`: UUID-shape is a `pg.UUID`
    column concern (`fact.id`/`fact.superseded_by`), and the in-memory store
    has no analogous column type to violate — its ids are plain strings by
    construction, so "malformed" has no meaning there.
    """
    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    store = SqlFactStore(engine)
    try:
        ids = await store.propose(
            key="workspace-a", proposals=(_proposal(_verdict()),), now_ms=NOW,
        )

        with pytest.raises(ValueError):
            await store.supersede(
                key="workspace-a", target=ids[0], replaced_by="not-a-uuid",
                because="wrong", source=None, now_ms=NOW + 1,
            )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# the isinstance refusal — SqlFactStore.propose's own docstring (1)
# ---------------------------------------------------------------------------


async def test_propose_refuses_a_payload_that_is_not_a_fact_verdict(store):
    """`payload: object` at the port means nothing upstream can catch a wrong
    value — this store is where it is caught. Raises, does not silently skip
    the bad row: a skip would make a caller's `len(proposals) !=
    len(written)` look identical to the ordinary `not_covered` case below,
    which is a legitimate, silent, no-error skip. A type error is not."""
    foreign = Proposal(
        payload="not-a-fact-verdict", instruction_sha256="a" * 64,
        model="m", established_by="run-1",
    )

    with pytest.raises(TypeError):
        await store.propose(key="workspace-a", proposals=(foreign,), now_ms=NOW)


# ---------------------------------------------------------------------------
# not_covered writes nothing — SqlFactStore.propose's own docstring (2)
# ---------------------------------------------------------------------------


async def test_a_not_covered_verdict_writes_no_row(store):
    """R-53 one layer down: a source that does not reach the question has
    not denied it, so adjudicating it `not_covered` must add nothing. Checked
    two ways — the returned id sequence is empty, AND nothing is visible
    afterward through `standing_facts` — because an id-count check alone
    would not catch a store that wrote the row anyway and simply forgot to
    report its id."""
    verdict = _verdict(
        subject="never-written", status="not_covered", addresses_claim=False,
        source_url=None, source_excerpt=None,
    )

    ids = await store.propose(
        key="workspace-a", proposals=(_proposal(verdict),), now_ms=NOW,
    )

    assert ids == ()
    assert await store.standing_facts(
        workspace="workspace-a", subject_ids=["never-written"], limit=10,
    ) == ()


async def test_a_not_covered_verdict_among_others_does_not_block_its_siblings(store):
    """One bad-shaped item in a batch must not fail the rest — `not_covered`
    is a legitimate finding, not an error, and the batch's other proposals
    are unrelated adjudications that happened to arrive together."""
    covered = _verdict(subject="written", object="v1")
    not_covered = _verdict(
        subject="not-written", status="not_covered", addresses_claim=False,
        source_url=None, source_excerpt=None,
    )

    ids = await store.propose(
        key="workspace-a",
        proposals=(_proposal(covered), _proposal(not_covered)),
        now_ms=NOW,
    )

    assert len(ids) == 1
    assert await store.standing_facts(
        workspace="workspace-a", subject_ids=["written"], limit=10,
    ) != ()


# ---------------------------------------------------------------------------
# supersession — the CURRENCY axis, and history staying readable
# ---------------------------------------------------------------------------


async def test_a_superseded_fact_is_still_readable_by_id(store):
    """History reads never disappear — `fact_by_id` is explicitly NOT
    filtered on `superseded_at`, unlike `standing_facts`. Superseding a fact
    is invalidation, not deletion (`DreamSink`'s own load-bearing claim, one
    layer up)."""
    old = await store.propose(
        key="workspace-a", proposals=(_proposal(_verdict(object="v1")),), now_ms=NOW,
    )
    new = await store.propose(
        key="workspace-a", proposals=(_proposal(_verdict(object="v2")),), now_ms=NOW + 1,
    )
    await store.supersede(
        key="workspace-a", target=old[0], replaced_by=new[0], because="wrong",
        source=None, now_ms=NOW + 2,
    )

    fact = await store.fact_by_id(workspace="workspace-a", fact_id=old[0])

    assert fact is not None
    assert isinstance(fact, Fact)
    assert fact.is_standing is False
    assert fact.superseded_by == new[0]


async def test_standing_facts_excludes_a_superseded_row(store):
    """The one read on the turn's critical path must never render history as
    current — `idx_fact_standing` is partial on exactly this predicate for
    the same reason."""
    old = await store.propose(
        key="workspace-a",
        proposals=(_proposal(_verdict(subject="e1", object="v1")),), now_ms=NOW,
    )
    new = await store.propose(
        key="workspace-a",
        proposals=(_proposal(_verdict(subject="e1", object="v2")),), now_ms=NOW + 1,
    )
    await store.supersede(
        key="workspace-a", target=old[0], replaced_by=new[0], because="wrong",
        source=None, now_ms=NOW + 2,
    )

    standing = await store.standing_facts(
        workspace="workspace-a", subject_ids=["e1"], limit=10,
    )

    assert [f.id for f in standing] == [new[0]]


async def test_standing_facts_with_a_negative_limit_clamps_to_empty(store):
    """2026-08-21 review: a negative `limit` diverged between
    implementations — Postgres errors on `LIMIT -1` (caught by
    `SqlFactStore`'s own broad `except Exception` and degraded to `()`),
    while `InMemoryFactStore.standing_facts` used to compute
    `matches[:limit]`, and Python's `list[:-1]` silently keeps every fact
    but the last rather than returning nothing. Both now clamp a negative
    (or zero) `limit` to `()` before doing any work, so the two
    implementations agree instead of one raising-then-degrading and the
    other quietly answering a different question."""
    await store.propose(
        key="workspace-a", proposals=(_proposal(_verdict(subject="e1")),), now_ms=NOW,
    )

    assert await store.standing_facts(
        workspace="workspace-a", subject_ids=["e1"], limit=-1,
    ) == ()


# ---------------------------------------------------------------------------
# fact_proposal_factory — moved from tests/contract/test_dream_sink_contract.py
# (2026-08-21 review): a fact-tier assertion about fact_proposal_factory's
# own output has no business living in the shared DreamSink suite.
# ---------------------------------------------------------------------------


def test_fact_verdict_is_a_real_payload_not_a_placeholder():
    """Guards `test_a_foreign_payload_is_refused_not_duck_typed` in the
    shared contract suite: if `fact_proposal_factory` ever stopped building
    a real `FactVerdict`, that test would still pass for the wrong reason
    (any old object is 'foreign' to `SqlFactStore`) and nothing would
    notice."""
    p = fact_proposal_factory()
    assert isinstance(p.payload, FactVerdict)


# ---------------------------------------------------------------------------
# `asserters()` — the READ, on BOTH implementations
# ---------------------------------------------------------------------------
#
# CLAUDE.md: a port never gains a second implementation — including a test fake
# — without a shared contract suite both pass. `FactReader.asserters` gained
# two, and the double's version was reachable from nothing: `seed_asserters`
# and `asserters` were dead code, and its docstring's claim to "match
# production on a malformed id and on ordering" was self-attestation. These
# four tests are that claim, measured.
#
# The JOIN stays SQL-only below — it walks two tables the double does not hold
# — but the read is shared, and the read is what a renderer calls.


@pytest.fixture(params=["memory", "sql"])
async def reader(request):
    """Both implementations plus a per-implementation `seed` verb, the shape
    `tests/infra/test_fact_candidate_store.py::_World` uses: anything the
    shared body needs that is not on the port is supplied by the entry, never
    hard-coded to one backing."""
    if request.param == "memory":
        store = InMemoryFactStore()

        async def seed(fact_id, rows):
            # The FACT too, not only its asserters. The SQL half inserts a real
            # `fact` row because `fact_asserter_fact_id_fkey` refuses an
            # orphan; a double that recorded only the junction would answer
            # `facts_asserted_by` with nothing while production answered with
            # the row — a divergence exactly where the two are supposed to
            # agree, and one this suite found the first time it asked.
            store.seed_fact(
                Fact(
                    id=fact_id, workspace="ws", subject_id="s", predicate="p",
                    object_entity_id=None, object_literal="o",
                    source_url="https://x.test/a", source_excerpt="x",
                    valid_from=1, created_at=1,
                    instruction_sha256="a" * 64, model="m",
                    established_by="run",
                )
            )
            store.seed_asserters(*[
                FactAsserter(
                    fact_id=fact_id, person_id=p, message_id=m, asserted_at=t,
                )
                for p, m, t in rows
            ])

        yield store, seed
        return

    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    store = SqlFactStore(engine)

    async def seed(fact_id, rows):
        async with engine.begin() as conn:
            # A real fact first: `fact_asserter_fact_id_fkey` refuses an
            # orphan, and a double that let one through would be permissive
            # where production is not.
            await conn.execute(
                sa.text(
                    "insert into fact (id, workspace, subject_id, predicate, "
                    "object_literal, source_url, source_excerpt, valid_from, "
                    "created_at, instruction_sha256, model, established_by) "
                    "values (:fid, 'ws', 's', 'p', 'o', 'https://x.test/a', "
                    "'x', 1, 1, :sha, 'm', 'run') on conflict do nothing"
                ),
                {"fid": fact_id, "sha": "a" * 64},
            )
            for person_id, message_id, asserted_at in rows:
                await conn.execute(
                    sa.text(
                        "insert into fact_asserter (fact_id, person_id, "
                        "message_id, asserted_at) values (:f, :p, :m, :t)"
                    ),
                    {"f": fact_id, "p": person_id, "m": message_id,
                     "t": asserted_at},
                )

    try:
        yield store, seed
    finally:
        await engine.dispose()


FACT_A = "0191f2c4-0000-7000-8000-00000000000a"
FACT_B = "0191f2c4-0000-7000-8000-00000000000b"


async def test_asserters_reads_back_what_was_written(reader):
    store, seed = reader
    await seed(FACT_A, [("miles", 41, 10)])

    rows = await store.asserters(fact_id=FACT_A)

    assert [(a.person_id, a.message_id, a.asserted_at) for a in rows] == [
        ("miles", 41, 10),
    ]
    assert all(a.fact_id == FACT_A for a in rows)


async def test_asserters_orders_by_time_then_message(reader):
    """The ordering the double claimed to match and nothing checked. Seeded out
    of order, with the alphabetical order reversed against the intended one so
    an unsorted read or a sort on `person_id` cannot pass."""
    store, seed = reader
    await seed(FACT_A, [("zoe", 90, 10), ("alice", 10, 10), ("miles", 50, 5)])

    rows = await store.asserters(fact_id=FACT_A)

    assert [a.person_id for a in rows] == ["miles", "alice", "zoe"]


async def test_asserters_is_scoped_to_its_fact(reader):
    """`== ()` is also true of a store that returns nothing for everything, so
    the positive control is the sibling fact returning its own row."""
    store, seed = reader
    await seed(FACT_A, [("miles", 41, 10)])
    await seed(FACT_B, [("grant", 42, 10)])

    assert [a.person_id for a in await store.asserters(fact_id=FACT_A)] == ["miles"]
    assert [a.person_id for a in await store.asserters(fact_id=FACT_B)] == ["grant"]


async def test_asserters_of_an_unknown_or_malformed_id_is_empty_on_both(reader):
    """A read beside the render: a malformed id is an unknown row, not a reason
    to take a turn down. The double must degrade the way the real store does,
    or a test that passes against it fails against Postgres."""
    store, seed = reader
    await seed(FACT_A, [("miles", 41, 10)])

    assert await store.asserters(fact_id="not-a-uuid") == ()
    assert await store.asserters(
        fact_id="0191f2c4-0000-7000-8000-0000000000ff") == ()
    # Positive control: the store is not simply empty.
    assert await store.asserters(fact_id=FACT_A) != ()


async def test_the_double_refuses_a_malformed_id_its_column_type_would():
    """The double's UUID guard, made reachable — and it is the reason it
    exists.

    Against SQL the guard is unfalsifiable: `fact.id` is a `uuid` column, so a
    row keyed on `"not-a-uuid"` cannot be stored and the query would return
    nothing anyway. The double is a plain list and WILL hold one, so without
    the guard it answers a question production cannot be asked — a double
    quietly more permissive than the store it stands in for, which is how a
    green test becomes a claim about the double.
    """
    store = InMemoryFactStore()
    store.seed_asserters(
        FactAsserter(
            fact_id="not-a-uuid", person_id="miles", message_id=1, asserted_at=1,
        )
    )

    assert await store.asserters(fact_id="not-a-uuid") == ()


# ---------------------------------------------------------------------------
# the attribution join — SQL only, because the join IS the behaviour
# ---------------------------------------------------------------------------
#
# `InMemoryFactStore` deliberately does not perform it (its `asserters`
# docstring says so): the join walks `fact_candidate.observed_in ->
# messages.person_id`, two tables the double does not hold, and a double that
# reproduced an unmapped sender or a missing candidate subtly wrong would make
# every green test below a claim about the double. So these run against
# Postgres, and what the shared suite carries is the READ.


@pytest.fixture
async def sql_store(pg_url):
    """A `SqlFactStore` WITH metrics — the join's three states are the point,
    and a store built without them counts nothing to read back."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    metrics = Metrics()
    try:
        yield SqlFactStore(engine, metrics=metrics), engine, metrics
    finally:
        await engine.dispose()


async def _seed_message(engine, *, mid: int, person_id: str | None) -> None:
    """One `messages` row. `person_id` NULL is the unmapped sender — resolved
    at ingest or not at all, never by a follow-up UPDATE, which is why a claim
    from one is unattributable rather than attributable-later."""
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "insert into messages (id, platform, conversation_id, "
                "conversation_type, sender_id, ts, body, person_id) values "
                "(:id, 'signal', 'g1', 'group', :s, :ts, 'x', :p)"
            ),
            # `ts` is INT8 and `id` INT4; one bind for both makes asyncpg
            # deduce two types for one parameter.
            {"id": mid, "ts": mid, "s": f"sender-{mid}", "p": person_id},
        )


async def _seed_candidate(engine, *, observed_in: list[str],
                          subject="e1", predicate="has", object="v1") -> str:
    """Returns the candidate id, because the attribution join takes it by
    IDENTITY — the adaptor stamps `Proposal.candidate_id` from what the run
    showed the model, and the store never re-derives it from the triple."""
    async with engine.begin() as conn:
        return str((await conn.execute(
            sa.text(
                "insert into fact_candidate (id, workspace, subject_id, "
                "predicate, object, first_seen_at, last_seen_at, observed_in, "
                "status, established_by, instruction_sha256, model) values "
                "(gen_random_uuid(), 'ws', :s, :p, :o, 1, 1, :oi, 'pending', "
                "'run-seed', :sha, 'm') returning id::text"
            ),
            {"s": subject, "p": predicate, "o": object, "oi": observed_in,
             "sha": "a" * 64},
        )).scalar_one())


async def test_the_asserters_of_a_fact_are_the_people_who_said_it(sql_store):
    """21 §7's whole answer, end to end: the fact records WHO claimed it, by
    joining the candidate's message ids through to `messages.person_id` — a
    join in code, with the prompt and the objective untouched and the model
    still never shown a name."""
    store, engine, metrics = sql_store
    await _seed_message(engine, mid=41, person_id="miles")
    await _seed_message(engine, mid=42, person_id="grant")
    cid = await _seed_candidate(engine, observed_in=["41", "42"])

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict(), candidate_id=cid),), now_ms=NOW,
    )

    rows = await store.asserters(fact_id=ids[0])
    assert sorted((a.person_id, a.message_id) for a in rows) == [
        ("grant", 42), ("miles", 41),
    ]
    assert all(a.asserted_at == NOW for a in rows)
    assert metrics.get("nora_fact_asserter_total", status="ok") == 1


async def test_asserters_come_back_in_the_order_the_room_said_it(sql_store):
    """Every asserter of one fact shares `now_ms`, so `message_id` is what
    actually orders them — and message ids are monotonic per conversation, so
    this is "in the order the room said it". Seeded out of order and with the
    alphabetical order deliberately REVERSED against the id order, so a sort
    on `person_id` (or no sort at all) cannot pass."""
    store, engine, _ = sql_store
    await _seed_message(engine, mid=90, person_id="alice")
    await _seed_message(engine, mid=10, person_id="zoe")
    await _seed_message(engine, mid=50, person_id="miles")
    cid = await _seed_candidate(engine, observed_in=["90", "10", "50"])

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict(), candidate_id=cid),), now_ms=NOW,
    )

    rows = await store.asserters(fact_id=ids[0])
    assert [a.message_id for a in rows] == [10, 50, 90]
    assert [a.person_id for a in rows] == ["zoe", "miles", "alice"]


async def test_three_people_once_is_not_one_person_three_times(sql_store):
    """The distinction the table exists to preserve, and the reason the grain
    is (fact, person, message) rather than anything coarser. `occurrence_count`
    reads 3 for both of these; the asserter rows do not."""
    store, engine, _ = sql_store
    for mid, person in ((1, "miles"), (2, "grant"), (3, "vic")):
        await _seed_message(engine, mid=mid, person_id=person)
    for mid in (4, 5, 6):
        await _seed_message(engine, mid=mid, person_id="miles")
    consensus_cid = await _seed_candidate(engine, observed_in=["1", "2", "3"])
    emphasis_cid = await _seed_candidate(
        engine, observed_in=["4", "5", "6"], subject="e2",
    )

    consensus = await store.propose(
        key="ws",
        proposals=(_proposal(_verdict(), candidate_id=consensus_cid),),
        now_ms=NOW,
    )
    emphasis = await store.propose(
        key="ws",
        proposals=(
            _proposal(_verdict(subject="e2"), candidate_id=emphasis_cid),
        ),
        now_ms=NOW,
    )

    consensus_rows = await store.asserters(fact_id=consensus[0])
    emphasis_rows = await store.asserters(fact_id=emphasis[0])

    assert len(consensus_rows) == len(emphasis_rows) == 3
    assert len({a.person_id for a in consensus_rows}) == 3
    assert len({a.person_id for a in emphasis_rows}) == 1


async def test_an_unmapped_sender_is_nothing_found_not_a_silent_zero(sql_store):
    """An unmapped sender has no `person_id` at ingest, by design. A claim
    only they asserted is genuinely unattributable — `nothing_found`, a real
    finding about the room — and must NOT read as `unavailable`, which is
    reserved for a wiring gap."""
    store, engine, metrics = sql_store
    await _seed_message(engine, mid=41, person_id=None)
    cid = await _seed_candidate(engine, observed_in=["41"])

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict(), candidate_id=cid),), now_ms=NOW,
    )

    assert await store.asserters(fact_id=ids[0]) == ()
    # The positive control comes FIRST: `unavailable == 0` below is also true
    # of a store that never ran the join at all, so the reading that proves it
    # ran is what separates the two states.
    assert metrics.get("nora_fact_asserter_total", status="nothing_found") == 1
    assert metrics.get("nora_fact_asserter_total", status="unavailable") == 0


async def test_no_candidate_to_join_through_is_unavailable(sql_store):
    """The other half, and the one that must never be reported as
    `nothing_found`. No candidate row means «I could not check who claimed
    this» — a wiring gap — not «nobody claimed it»."""
    store, engine, metrics = sql_store
    await _seed_message(engine, mid=41, person_id="miles")

    # No `candidate_id`: the adaptor could not map this verdict back to
    # anything it showed, which is exactly the state that must read as «I could
    # not check», never as «nobody said it».
    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict()),), now_ms=NOW,
    )

    assert await store.asserters(fact_id=ids[0]) == ()
    assert metrics.get(
        "nora_fact_asserter_total", status="unavailable") == 1
    assert metrics.get(
        "nora_fact_asserter_total", status="nothing_found") == 0


async def test_a_message_id_too_large_for_int4_does_not_lose_the_fact(sql_store):
    """`messages.id` is INT4 and `observed_in` holds model-derived digits of
    unbounded length — Python ints have no width, so the extractor can emit
    one. `'99999999999'::int` raises «out of range» INSIDE the fact's own
    transaction and rolls the fact back with it, and the `~ '^[0-9]+$'` shape
    guard does not help because it matches any length. The cast is `::bigint`.

    The positive control is the sibling id: the fact survives AND the
    attribution that could be made still was."""
    store, engine, metrics = sql_store
    await _seed_message(engine, mid=41, person_id="miles")
    cid = await _seed_candidate(engine, observed_in=["41", "99999999999"])

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict(), candidate_id=cid),),
        now_ms=NOW,
    )

    assert await store.fact_by_id(workspace="ws", fact_id=ids[0]) is not None
    assert [a.person_id for a in await store.asserters(fact_id=ids[0])] == ["miles"]
    assert metrics.get("nora_fact_asserter_total", status="ok") == 1


async def test_a_blank_person_id_does_not_lose_the_fact(sql_store):
    """`messages.person_id` has no non-blank CHECK; `fact_asserter.person_id`
    does. A blank one would raise a CheckViolation inside the fact's
    transaction and take the write down — an attribution problem destroying the
    thing it was only meant to describe."""
    store, engine, metrics = sql_store
    await _seed_message(engine, mid=41, person_id="   ")
    cid = await _seed_candidate(engine, observed_in=["41"])

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict(), candidate_id=cid),),
        now_ms=NOW,
    )

    assert await store.fact_by_id(workspace="ws", fact_id=ids[0]) is not None
    assert await store.asserters(fact_id=ids[0]) == ()
    # A blank is not a person, so this is the unmapped case, not a wiring gap.
    assert metrics.get(
        "nora_fact_asserter_total", status="nothing_found") == 1


async def test_a_blank_person_beside_a_real_one_does_not_lose_the_fact(sql_store):
    """The case that actually reaches the INSERT's filter. When EVERY sender is
    blank the count guard short-circuits to `nothing_found` and the insert
    never runs — so a blank-only claim does not exercise it, and the first
    version of this pair proved nothing. A claim asserted by one real person
    and one blank does: the insert runs, and without its own filter the blank
    row raises `fact_asserter_person_is_named_check` and rolls the FACT back
    with it."""
    store, engine, metrics = sql_store
    await _seed_message(engine, mid=41, person_id="miles")
    await _seed_message(engine, mid=42, person_id="  ")
    cid = await _seed_candidate(engine, observed_in=["41", "42"])

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict(), candidate_id=cid),),
        now_ms=NOW,
    )

    assert await store.fact_by_id(workspace="ws", fact_id=ids[0]) is not None
    assert [a.person_id for a in await store.asserters(fact_id=ids[0])] == ["miles"]
    assert metrics.get("nora_fact_asserter_total", status="ok") == 1


async def test_a_pruned_conversation_is_unavailable_not_nothing_found(sql_store):
    """`prune_all` deletes messages by conversation on a schedule while
    `observed_in` survives it, so a pruned conversation leaves a candidate
    whose evidence no longer exists. «The evidence is gone, I could not check»
    is `unavailable`; reporting it as «nobody claimed this» is R-27's forbidden
    inversion, and this module's own help text asserted it before it was
    measured."""
    store, engine, metrics = sql_store
    await _seed_message(engine, mid=41, person_id="miles")
    cid = await _seed_candidate(engine, observed_in=["41"])
    async with engine.begin() as conn:
        await conn.execute(sa.text("delete from messages where id = 41"))

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict(), candidate_id=cid),),
        now_ms=NOW,
    )

    assert await store.asserters(fact_id=ids[0]) == ()
    assert metrics.get(
        "nora_fact_asserter_total", status="unavailable") == 1
    assert metrics.get(
        "nora_fact_asserter_total", status="nothing_found") == 0


async def test_a_settled_candidate_does_not_donate_its_asserters(sql_store):
    """The join is by IDENTITY, never by the triple. A triple match against the
    whole table would hand this already-settled candidate's asserters to a
    later fact that has nothing to do with it — and `not_covered` candidates,
    which by construction produced no fact at all, sit in that table forever as
    the negative cache."""
    store, engine, metrics = sql_store
    await _seed_message(engine, mid=41, person_id="miles")
    await _seed_candidate(engine, observed_in=["41"])   # same triple, NOT named

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict()),), now_ms=NOW,
    )

    assert await store.asserters(fact_id=ids[0]) == ()
    assert metrics.get(
        "nora_fact_asserter_total", status="unavailable") == 1


async def test_a_failed_attribution_leaves_no_counter_behind(sql_store):
    """Counting inside the transaction asserts attributions the database never
    got: a later proposal in the same batch failing rolls the whole boundary
    back, and the earlier ones' counters would already have moved.

    Driven by a real rollback rather than by a mock, because the property under
    test is the ORDER of a commit and a counter.

    The rollback needs a failure at the FLUSH, not at the write check — the
    check refuses before anything is attributed. So this store is built with an
    operating cap ABOVE the DDL ceiling, a combination config refuses and the
    store does not, which leaves the ceiling as the only thing that fires."""
    _configured, engine, metrics = sql_store
    await _seed_message(engine, mid=41, person_id="miles")
    cid = await _seed_candidate(engine, observed_in=["41"])
    store = SqlFactStore(
        engine, max_term_chars=FACT_TERM_CEILING_CHARS + 100, metrics=metrics,
    )

    # `IntegrityError` specifically: a blind `Exception` would also pass on a
    # connection failure, which proves nothing about a rollback.
    with pytest.raises(sa.exc.IntegrityError):
        await store.propose(
            key="ws",
            proposals=(
                _proposal(_verdict(), candidate_id=cid),
                # subject over the DDL ceiling: refused by Postgres at flush,
                # after the first proposal's attribution has already run.
                _proposal(
                    _verdict(subject="x" * (FACT_TERM_CEILING_CHARS + 1)),
                    candidate_id=cid,
                ),
            ),
            now_ms=NOW,
        )

    assert metrics.get("nora_fact_asserter_total", status="ok") == 0
    # The positive control: the same batch WITHOUT the poisoned row does move
    # the counter, so the zero above is a rollback and not a join that never
    # runs.
    await store.propose(
        key="ws", proposals=(_proposal(_verdict(), candidate_id=cid),),
        now_ms=NOW,
    )
    assert metrics.get("nora_fact_asserter_total", status="ok") == 1


async def test_a_pruned_message_does_not_unmake_the_attribution(sql_store):
    """No FOREIGN KEY on `message_id`, deliberately: `prune_all` deletes
    messages by conversation, and who said a thing is a fixed historical fact.
    The message is the evidence; losing the evidence does not unmake the
    assertion."""
    store, engine, _ = sql_store
    await _seed_message(engine, mid=41, person_id="miles")
    cid = await _seed_candidate(engine, observed_in=["41"])
    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict(), candidate_id=cid),), now_ms=NOW,
    )
    assert len(await store.asserters(fact_id=ids[0])) == 1

    async with engine.begin() as conn:
        await conn.execute(sa.text("delete from messages where id = 41"))

    rows = await store.asserters(fact_id=ids[0])
    assert [(a.person_id, a.message_id) for a in rows] == [("miles", 41)]


async def test_a_malformed_observed_in_entry_does_not_lose_the_fact(sql_store):
    """`observed_in` is `text[]` and every writer here puts `str(int)` in it.
    A cast that raised would abort the transaction and lose the FACT, not just
    its attribution — so the guard is about which failure you get, and the
    positive control is that the sibling id still resolves."""
    store, engine, metrics = sql_store
    await _seed_message(engine, mid=41, person_id="miles")
    cid = await _seed_candidate(engine, observed_in=["41", "not-a-number"])

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict(), candidate_id=cid),), now_ms=NOW,
    )

    assert await store.fact_by_id(workspace="ws", fact_id=ids[0]) is not None
    rows = await store.asserters(fact_id=ids[0])
    assert [a.person_id for a in rows] == ["miles"]
    assert metrics.get("nora_fact_asserter_total", status="ok") == 1


async def test_asserters_of_an_unknown_or_malformed_id_is_empty(sql_store):
    """A read beside the render: a malformed id is an unknown row, not a
    reason to take a turn down."""
    store, _engine, _ = sql_store
    assert await store.asserters(fact_id="not-a-uuid") == ()
    assert await store.asserters(
        fact_id="0191f2c4-0000-7000-8000-0000000000ff") == ()


# ---------------------------------------------------------------------------
# the caps — two layers, and only the ceiling is DDL
# ---------------------------------------------------------------------------


async def test_a_term_over_the_ceiling_is_refused_by_the_database(pg_url):
    """The CEILING is the backstop under every size decision an operator can
    make — a config typo or a future writer nobody has thought of. It is DDL,
    so it is asserted here through a raw INSERT rather than through a store
    that would refuse first."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            with pytest.raises(sa.exc.IntegrityError):
                await conn.execute(
                    sa.text(
                        "insert into fact (id, workspace, subject_id, "
                        "predicate, object_literal, source_url, "
                        "source_excerpt, valid_from, created_at, "
                        "instruction_sha256, model, established_by) values "
                        "(gen_random_uuid(), 'ws', :s, 'has', 'v', "
                        "'https://x.test/a', 'x', 1, 1, :sha, 'm', 'run')"
                    ),
                    {"s": "x" * (FACT_TERM_CEILING_CHARS + 1), "sha": "a" * 64},
                )
    finally:
        await engine.dispose()


async def test_a_candidate_term_over_the_ceiling_is_refused_too(pg_url):
    """`fact_candidate` carries the same ceilings, because a candidate becomes
    a fact. Without them the refusal lands at adjudication — after the research
    loop has already been spent on the claim."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            with pytest.raises(sa.exc.IntegrityError):
                await conn.execute(
                    sa.text(
                        "insert into fact_candidate (id, workspace, "
                        "subject_id, predicate, object, first_seen_at, "
                        "last_seen_at, status, established_by, "
                        "instruction_sha256, model) values "
                        "(gen_random_uuid(), 'ws', 's', :p, 'v', 1, 1, "
                        "'pending', 'run', :sha, 'm')"
                    ),
                    {"p": "p" * (FACT_PREDICATE_CEILING_CHARS + 1),
                     "sha": "a" * 64},
                )
    finally:
        await engine.dispose()


async def test_the_store_refuses_a_term_over_the_operating_cap(pg_url):
    """30 §5: "every writer validates against the CURRENT operating value".

    The caps were enforced only at PARSE, inside the two adaptors — so any
    other `DreamSink` caller wrote straight past them, and the startup refusal
    that names rows above a lowered cap was guarding a state the write path
    could still create. Production was safe only because those two adaptors
    happened to be the only writers, which is a fact about today rather than a
    property of the design.

    RAISES rather than dropping: a verdict this long reached the store past an
    adaptor that should have refused it, which is a caller bug. Dropping it
    would be indistinguishable from `not_covered` — a fact silently not written
    while the tick reports success."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        store = SqlFactStore(engine, max_term_chars=200)
        with pytest.raises(ValueError, match="operating cap"):
            await store.propose(
                key="ws",
                proposals=(_proposal(_verdict(subject="x" * 201)),),
                now_ms=NOW,
            )
        # The positive control: one character under the cap writes.
        assert len(await store.propose(
            key="ws",
            proposals=(_proposal(_verdict(subject="x" * 200)),),
            now_ms=NOW,
        )) == 1
    finally:
        await engine.dispose()


async def test_the_store_refuses_a_predicate_over_its_own_operating_cap(pg_url):
    """Asserted separately from the term cap: one check covering two fields
    passes with either half deleted."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        store = SqlFactStore(engine, max_predicate_chars=10)
        with pytest.raises(ValueError, match="operating cap"):
            await store.propose(
                key="ws",
                proposals=(_proposal(_verdict(predicate="p" * 11)),),
                now_ms=NOW,
            )
    finally:
        await engine.dispose()


async def test_lowering_a_cap_below_existing_rows_names_them(pg_url):
    """30 §5: refusing a config above its ceiling is not enough. Lowering the
    OPERATING cap below rows that already exist leaves them satisfying the
    CHECK — which is the ceiling, not the knob — while quietly defeating the
    prompt budget the cap was lowered to protect."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        # Written under a HIGHER cap, because that is the scenario: the row was
        # legal when it landed and the operator lowered the knob afterwards. A
        # store at today's cap refuses it at the write (30 §5), so this is the
        # only way the state exists — in the test and in production alike.
        past = SqlFactStore(engine, max_term_chars=400)
        await past.propose(
            key="ws",
            proposals=(_proposal(_verdict(subject="x" * 300)),),
            now_ms=NOW,
        )

        offending = await oversized_terms(
            engine, max_term_chars=200, max_predicate_chars=100,
        )

        assert len(offending) == 1
        assert "fact " in offending[0]
        assert "term is 300 characters" in offending[0]
        # The positive control: the same read at the cap the row was written
        # under finds nothing, so the assertion above is about the CAP and not
        # about a query that always returns a row.
        assert await oversized_terms(
            engine, max_term_chars=800, max_predicate_chars=200,
        ) == ()
    finally:
        await engine.dispose()


async def test_an_oversized_candidate_is_named_too(pg_url):
    """Both tables, because a candidate becomes a fact and an operator who
    lowered the cap wants to know about the queue as well as the store."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "insert into fact_candidate (id, workspace, subject_id, "
                    "predicate, object, first_seen_at, last_seen_at, status, "
                    "established_by, instruction_sha256, model) values "
                    "(gen_random_uuid(), 'ws', 's', :p, 'v', 1, 1, 'pending', "
                    "'run', :sha, 'm')"
                ),
                {"p": "p" * 150, "sha": "a" * 64},
            )

        offending = await oversized_terms(
            engine, max_term_chars=200, max_predicate_chars=100,
        )

        assert [o for o in offending if o.startswith("fact_candidate ")]
        assert "predicate is 150 characters" in offending[0]
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# the sender slice and the extent — 19 §6, on BOTH implementations
# ---------------------------------------------------------------------------


async def test_the_sender_slice_is_what_this_person_asserted(reader):
    """The first read the asserter junction exists for. It grounds «you told
    us…», which is why the sender slice exists at all."""
    store, seed = reader
    await seed(FACT_A, [("miles", 41, 10)])
    await seed(FACT_B, [("grant", 42, 10)])

    mine = await store.facts_asserted_by(
        workspace="ws", person_id="miles", limit=10,
    )

    assert [f.id for f in mine] == [FACT_A]
    # The positive control: the other person's fact IS reachable, so the single
    # result above is a filter and not an empty store.
    theirs = await store.facts_asserted_by(
        workspace="ws", person_id="grant", limit=10,
    )
    assert [f.id for f in theirs] == [FACT_B]


async def test_the_sender_slice_clamps_a_negative_limit_to_empty(reader):
    """Postgres errors on a negative LIMIT, so the double must degrade the same
    way rather than answering a different question the way `list[:-1]`
    silently does."""
    store, seed = reader
    await seed(FACT_A, [("miles", 41, 10)])

    assert await store.facts_asserted_by(
        workspace="ws", person_id="miles", limit=-1,
    ) == ()


async def test_the_sender_slice_never_crosses_a_workspace(reader):
    store, seed = reader
    await seed(FACT_A, [("miles", 41, 10)])

    assert await store.facts_asserted_by(
        workspace="other", person_id="miles", limit=10,
    ) == ()


async def test_the_extent_counts_standing_rows(reader):
    """Counts, never rows (19 §6, 23 §3). Without it the block is a subset
    presented as the whole, which is the defect the recency window had."""
    store, seed = reader
    await seed(FACT_A, [("miles", 41, 10)])
    await seed(FACT_B, [("grant", 42, 10)])

    assert await store.standing_count(workspace="ws") == 2
    assert await store.standing_count(workspace="other") == 0


# ---------------------------------------------------------------------------
# the `disputed` state machine — 31 §7, on BOTH implementations
# ---------------------------------------------------------------------------


@pytest.fixture
async def disputable(reader):
    """A store with one standing fact, and its id."""
    store, seed = reader
    await seed(FACT_A, [("miles", 41, 10)])
    return store, FACT_A


async def test_a_disputed_fact_is_still_standing(disputable):
    """THE REVERSAL, and the property everything else rests on. A dispute MARKS
    a row; it does not mute it — 30 §2.1, 29 §5 and 19 §4 all decide a
    three-state render and only step 4's shorthand collapsed it to two.

    Folding `disputed_at` into `is_standing` would be the mute this design
    refuses, hidden in a property nobody would think to read."""
    store, fact_id = disputable

    assert await store.mark_disputed(
        workspace="ws", fact_id=fact_id, person_id="grant",
        reason="it's a video game", tag_id="tag-9", now_ms=NOW,
    ) == "ok"

    rows = await store.disputed_facts(workspace="ws", limit=10)
    assert [f.id for f in rows] == [fact_id]
    assert rows[0].is_standing is True
    assert rows[0].is_disputed is True
    assert rows[0].disputed_by == "grant"
    assert rows[0].disputed_reason == "it's a video game"
    assert rows[0].disputed_tag_id == "tag-9"


async def test_a_second_dispute_is_a_no_op_and_says_which_kind(disputable):
    """`already_disputed` and `already_superseded` are different facts about
    the row, and a single CAS cannot tell them apart. Reporting the friendlier
    of the two is how a second member's honest dispute comes to look like one
    that was already handled."""
    store, fact_id = disputable
    await store.mark_disputed(
        workspace="ws", fact_id=fact_id, person_id="grant", reason=None,
        tag_id="tag-9", now_ms=NOW,
    )

    assert await store.mark_disputed(
        workspace="ws", fact_id=fact_id, person_id="vic", reason=None,
        tag_id="tag-10", now_ms=NOW + 1,
    ) == "already_disputed"

    # The first asserter is the one that holds. The second is recorded on the
    # candidate it proposes, which is where asserters live.
    rows = await store.disputed_facts(workspace="ws", limit=10)
    assert rows[0].disputed_by == "grant"


async def test_a_dispute_on_a_superseded_row_is_already_superseded(reader):
    """That row has already stopped rendering as a standing claim, so there is
    nothing left to mark — and it is reported ahead of `already_disputed`
    because it is the more informative of the two."""
    store, seed = reader
    await seed(FACT_A, [("miles", 41, 10)])
    await seed(FACT_B, [("miles", 42, 10)])
    await store.supersede(
        key="ws", target=FACT_A, replaced_by=FACT_B, because="wrong",
        source=None, now_ms=NOW,
    )

    assert await store.mark_disputed(
        workspace="ws", fact_id=FACT_A, person_id="grant", reason=None,
        tag_id="tag-9", now_ms=NOW + 1,
    ) == "already_superseded"


async def test_a_dispute_on_an_unknown_row_is_unknown(disputable):
    """Never `ok`. A mark that affected nothing reported as success is a
    recogniser that believes it acted."""
    store, _fact_id = disputable

    assert await store.mark_disputed(
        workspace="ws", fact_id="0191f2c4-0000-7000-8000-0000000000ff",
        person_id="g", reason=None, tag_id="t", now_ms=NOW,
    ) == "unknown"
    assert await store.mark_disputed(
        workspace="other", fact_id=FACT_A, person_id="g", reason=None,
        tag_id="t", now_ms=NOW,
    ) == "unknown"


async def test_clearing_is_compare_and_swap_on_what_was_read(disputable):
    """31 §7's clear half, and 30 §4's supersession CAS one column over. A
    second member's dispute landing mid-dismissal changes `disputed_at`, the
    CAS fails, and the dismissal does NOT clear — the adjudication becomes a
    candidate carrying both rather than silently discarding the newer one."""
    store, fact_id = disputable
    await store.mark_disputed(
        workspace="ws", fact_id=fact_id, person_id="grant", reason=None,
        tag_id="tag-9", now_ms=NOW,
    )

    # A dismissal that read a stale value clears nothing.
    assert await store.clear_disputed(
        workspace="ws", fact_id=fact_id, expected_at=NOW - 1,
    ) is False
    assert await store.disputed_facts(workspace="ws", limit=10) != ()

    # The positive control: the value that WAS read does clear it.
    assert await store.clear_disputed(
        workspace="ws", fact_id=fact_id, expected_at=NOW,
    ) is True
    assert await store.disputed_facts(workspace="ws", limit=10) == ()


async def test_supersession_clears_the_dispute(reader):
    """A superseded row does not render as a standing claim, so a dispute on it
    is moot — and leaving the flag set would make the open-dispute gauge count
    resolved rows forever, which is the reading the whole no-auto-clear
    argument rests on."""
    store, seed = reader
    await seed(FACT_A, [("miles", 41, 10)])
    await seed(FACT_B, [("miles", 42, 10)])
    await store.mark_disputed(
        workspace="ws", fact_id=FACT_A, person_id="grant", reason=None,
        tag_id="tag-9", now_ms=NOW,
    )
    assert await store.disputed_facts(workspace="ws", limit=10) != ()

    await store.supersede(
        key="ws", target=FACT_A, replaced_by=FACT_B, because="wrong",
        source=None, now_ms=NOW + 1,
    )

    # Asserted on the ROW, not through `disputed_facts` — that read already
    # excludes superseded rows, so it is satisfied whether or not the column
    # was cleared, and the first version of this test passed with the clearing
    # deleted outright. The column is the property: a flag left set makes the
    # open-dispute gauge count resolved rows forever.
    row = await store.fact_by_id(workspace="ws", fact_id=FACT_A)
    assert row is not None
    assert row.disputed_at is None
    assert row.disputed_by is None
    assert row.disputed_reason is None
    assert row.disputed_tag_id is None
    assert await store.disputed_facts(workspace="ws", limit=10) == ()


async def test_the_backlog_is_oldest_dispute_first(reader):
    """An aged dispute is the one that most needs adjudicating, and oldest
    first makes the queue fair rather than last-in-first-out."""
    store, seed = reader
    await seed(FACT_A, [("miles", 41, 10)])
    await seed(FACT_B, [("miles", 42, 10)])
    await store.mark_disputed(
        workspace="ws", fact_id=FACT_B, person_id="g", reason=None,
        tag_id="t", now_ms=NOW + 100,
    )
    await store.mark_disputed(
        workspace="ws", fact_id=FACT_A, person_id="g", reason=None,
        tag_id="t", now_ms=NOW,
    )

    rows = await store.disputed_facts(workspace="ws", limit=10)

    assert [f.id for f in rows] == [FACT_A, FACT_B]


async def test_writing_a_fact_creates_its_subject_entity_in_the_same_transaction(
    pg_url,
):
    """**`21-substrate-contract.md` §4 was three comments and no code.**

    "The sink upserts the entities an edge implies, in the same transaction
    that writes the edge" was asserted in the entity store's module docstring,
    in `resolve_or_create`'s own docstring, and in the pass-6 wiring comment —
    while `resolve_or_create`'s only caller was `promote` and `resolve` had no
    caller at all. The document and the store said different things and the
    document lost silently, which is the failure this bundle exists to
    document.

    The SUBJECT end only. §6 writes the object as a literal and derives
    entity-hood later from degree (R-78), so resolving the object here would
    mint a node for every string the extractor produced — exactly what that
    rule prevents. §4 and §6 read as a contradiction until you notice they
    are about different ends of the same edge.

    The RAW spelling lands as an alias beside the normalised canonical name,
    which is what lets `pending()`'s `subject_resolves` see a candidate
    written `Big Walk` against a canonical `big walk`.
    """
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        entities = SqlEntityStore(engine)
        store = SqlFactStore(engine, entities=entities)

        await store.propose(
            key="ws-main",
            proposals=[
                _proposal(_verdict(subject="Big Walk", object="a walk")),
                # The same thing spelled another way, in the same batch: one
                # entity, both spellings recorded.
                _proposal(_verdict(subject="big  walk", predicate="made_by",
                                   object="House House")),
            ],
            now_ms=1_000,
        )

        async with engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "select canonical_name, aliases from fact_entity "
                "where workspace = 'ws-main'"
            ))).all()
    finally:
        await engine.dispose()

    assert len(rows) == 1, f"expected one entity, got {rows}"
    canonical, aliases = rows[0]
    assert canonical == "big walk"
    assert set(aliases) >= {"Big Walk", "big  walk"}


async def test_a_fact_store_without_an_entity_store_still_writes_the_fact(pg_url):
    """The positive control for the test above, and the degradation the
    optional collaborator buys: a store built from a bare engine — which is
    what a contract fixture does — writes its edge and mints no endpoint. If
    the resolve were mandatory this would raise, and if it were silently
    skipped for every store the test above would be satisfied by a resolve
    that never runs at all."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        store = SqlFactStore(engine)

        ids = await store.propose(
            key="ws-main",
            proposals=[_proposal(_verdict(subject="Big Walk"))],
            now_ms=1_000,
        )

        async with engine.connect() as conn:
            entities = (await conn.execute(sa.text(
                "select count(*) from fact_entity where workspace = 'ws-main'"
            ))).scalar_one()
    finally:
        await engine.dispose()

    assert len(ids) == 1
    assert entities == 0


async def test_both_stores_break_a_created_at_tie_the_same_way(store):
    """The DOUBLE was deterministic while production was not, which is worse
    than both being non-deterministic: `list.sort` is stable, so the in-memory
    store returned insertion order among ties and a contract test could pin an
    ordering the real store does not have. That is the double diverging
    exactly where the real path branches.

    Runs against both implementations through the shared fixture, so the
    property is the PORT's and not one store's.
    """
    ids = await store.propose(
        key="ws-main",
        proposals=[
            _proposal(_verdict(subject="e1", predicate=f"p{i}", object=f"v{i}"))
            for i in range(8)
        ],
        now_ms=1_000,
    )
    assert len(ids) == 8

    facts = await store.standing_facts(
        workspace="ws-main", subject_ids=("e1",), limit=8,
    )

    assert [f.id for f in facts] == sorted(ids, reverse=True)
