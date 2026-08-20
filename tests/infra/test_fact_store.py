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

from nora.domain.facts import Fact, FactVerdict
from nora.domain.ports import Proposal
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.facts import SqlFactStore
from nora.infra.sql.migrations_runner import run_migrations
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
