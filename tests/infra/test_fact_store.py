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

from nora.domain.facts import Fact, FactVerdict
from nora.domain.ports import Proposal
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.facts import SqlFactStore
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


def _proposal(verdict: FactVerdict, *, established_by="run-1") -> Proposal:
    return Proposal(
        payload=verdict, instruction_sha256="a" * 64,
        model="ollamacloud/deepseek-v4-flash", established_by=established_by,
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
                          subject="e1", predicate="has", object="v1") -> None:
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "insert into fact_candidate (id, workspace, subject_id, "
                "predicate, object, first_seen_at, last_seen_at, observed_in, "
                "status, established_by, instruction_sha256, model) values "
                "(gen_random_uuid(), 'ws', :s, :p, :o, 1, 1, :oi, 'pending', "
                "'run-seed', :sha, 'm')"
            ),
            {"s": subject, "p": predicate, "o": object, "oi": observed_in,
             "sha": "a" * 64},
        )


async def test_the_asserters_of_a_fact_are_the_people_who_said_it(sql_store):
    """21 §7's whole answer, end to end: the fact records WHO claimed it, by
    joining the candidate's message ids through to `messages.person_id` — a
    join in code, with the prompt and the objective untouched and the model
    still never shown a name."""
    store, engine, metrics = sql_store
    await _seed_message(engine, mid=41, person_id="miles")
    await _seed_message(engine, mid=42, person_id="grant")
    await _seed_candidate(engine, observed_in=["41", "42"])

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict()),), now_ms=NOW,
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
    await _seed_candidate(engine, observed_in=["90", "10", "50"])

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict()),), now_ms=NOW,
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
    await _seed_candidate(engine, observed_in=["1", "2", "3"])
    await _seed_candidate(
        engine, observed_in=["4", "5", "6"], subject="e2",
    )

    consensus = await store.propose(
        key="ws", proposals=(_proposal(_verdict()),), now_ms=NOW,
    )
    emphasis = await store.propose(
        key="ws", proposals=(_proposal(_verdict(subject="e2")),), now_ms=NOW,
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
    await _seed_candidate(engine, observed_in=["41"])

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict()),), now_ms=NOW,
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

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict()),), now_ms=NOW,
    )

    assert await store.asserters(fact_id=ids[0]) == ()
    assert metrics.get(
        "nora_fact_asserter_total", status="unavailable") == 1
    assert metrics.get(
        "nora_fact_asserter_total", status="nothing_found") == 0


async def test_a_pruned_message_does_not_unmake_the_attribution(sql_store):
    """No FOREIGN KEY on `message_id`, deliberately: `prune_all` deletes
    messages by conversation, and who said a thing is a fixed historical fact.
    The message is the evidence; losing the evidence does not unmake the
    assertion."""
    store, engine, _ = sql_store
    await _seed_message(engine, mid=41, person_id="miles")
    await _seed_candidate(engine, observed_in=["41"])
    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict()),), now_ms=NOW,
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
    await _seed_candidate(engine, observed_in=["41", "not-a-number"])

    ids = await store.propose(
        key="ws", proposals=(_proposal(_verdict()),), now_ms=NOW,
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
