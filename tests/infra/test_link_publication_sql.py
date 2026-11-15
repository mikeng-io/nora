"""Real PostgreSQL proves the consolidation is atomic, durable and
restart-resumable — the same executor the unit suite drives on fakes runs
here against `SqlBindingStore`/`SqlLinkRequests` with live Keto/Kratos
fakes only where no real service exists in this checkout.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from nora.domain.account_linking import ConsentProof, ConsentSurface, LinkState, PendingLink
from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.domain.management import (
    Actor,
    ActorKind,
    LinkApproval,
    LinkRequest,
    LinkSide,
    Operation,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
)
from nora.infra.sql.account_links import SqlLinkRequests
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people.app.link_publication import LinkPublicationService, _journal_id
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeIdentityRegistry,
    FakeTopologySource,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PERSON_A = "synthetic-person-a"
PERSON_B = "synthetic-person-b"
ACCT_I = Identifier("discord", "user_id", "synthetic-initiating")
ACCT_R = Identifier("signal", "uuid", "synthetic-receiving")
NONCE = {LinkSide.INITIATING: "gen-init-nonce", LinkSide.RECEIVING: "gen-recv-nonce"}
ISSUER = {LinkSide.INITIATING: "gen-discord-surface", LinkSide.RECEIVING: "gen-signal-surface"}
SURFACE = {LinkSide.INITIATING: ConsentSurface.DISCORD_REAUTH,
           LinkSide.RECEIVING: ConsentSurface.SIGNAL_PRIVATE}
ROOM = Scope(ScopeKind.ROOM, "signal/synthetic-room")
UNIT = Scope(ScopeKind.UNIT, "synthetic-unit")
WS = Scope(ScopeKind.WORKSPACE, "synthetic-workspace")


@pytest.fixture(scope="module")
def database(pg_module_url):
    return pg_module_url


@pytest.fixture
async def store(database):
    engine = create_sql_engine(database)
    async with engine.begin() as connection:
        await connection.execute(sa.text(
            "TRUNCATE managed_membership_support, managed_membership, managed_alias,"
            " managed_identifier, managed_account, managed_identity_hold,"
            " managed_authority_block, managed_operation, managed_person,"
            " managed_link_decision, managed_link_request CASCADE"
        ))
    try:
        yield SqlBindingStore(engine), SqlLinkRequests(engine), engine
    finally:
        await engine.dispose()


async def _person(store, registry, person_id, ident, scope=None):
    account = ManagedAccount(
        account_id=str(uuid4()), person_id=person_id,
        platform=ident.platform, identifiers=frozenset({ident}),
        state=AccountState.BOUND,
    )
    membership = (
        ManagedMembership(person_id=person_id, scope=scope,
                          state=MembershipState.MEMBER, source="registration")
        if scope is not None else None
    )
    outcome = await store.register(
        person=ManagedPerson(person_id=person_id, lifecycle=PersonLifecycle.ACTIVE),
        account=account, membership=membership,
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    await registry.ensure_identity(external_id=person_id, traits={})


def _actor(side, person):
    return Actor(ActorKind.HOLDER, person_id=person,
                 account=ACCT_I if side is LinkSide.INITIATING else ACCT_R,
                 proof_issuer=ISSUER[side])


async def _approved(svc, links):
    observation = await svc.observe(
        initiating=ACCT_I, receiving=ACCT_R, surviving_person=PERSON_A)
    op = Operation(
        "synthetic-request", "request_link", _actor(LinkSide.INITIATING, PERSON_A),
        Target(TargetKind.ACCOUNT, account=ACCT_R), LinkRequest(PERSON_A),
        dict(observation.revisions),
    )
    link = await links.create(PendingLink.request(
        op, receiving_person=observation.receiving_person,
        initiating_nonce=NONCE[LinkSide.INITIATING],
        receiving_nonce=NONCE[LinkSide.RECEIVING],
        created_at=NOW, expires_at=NOW + timedelta(minutes=5)))
    for side, person in ((LinkSide.INITIATING, PERSON_A), (LinkSide.RECEIVING, PERSON_B)):
        decision = Operation(
            f"gen-approve-{side.value}", "approve_link", _actor(side, person),
            Target(TargetKind.ACCOUNT, account=ACCT_R),
            LinkApproval(request_id="synthetic-request", side=side,
                         pair=link.pair, surviving_person=PERSON_A),
            dict(link.revisions),
        )
        proof = ConsentProof(
            account=ACCT_I if side is LinkSide.INITIATING else ACCT_R,
            issuer=ISSUER[side], surface=SURFACE[side],
            authenticated_at=NOW + timedelta(seconds=1),
            request_id="synthetic-request", nonce=NONCE[side],
        )
        link = await links.decide(decision, proof, now=NOW + timedelta(seconds=2))
    return link


async def _world(store_tuple):
    store, links, engine = store_tuple
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: (UNIT.id, False)}, units={UNIT.id: WS.id},
        workspaces={WS.id},
    )
    registry = FakeIdentityRegistry()
    svc = LinkPublicationService(store, backend, topology, registry, links)
    await _person(store, registry, PERSON_A, ACCT_I)
    await _person(store, registry, PERSON_B, ACCT_R)
    return store, backend, topology, registry, links, svc, engine


async def test_sql_consolidation_is_atomic_alias_and_support_merging(store):
    store, backend, _topology, _registry, links, svc, engine = await _world(store)
    await store.set_membership(person_id=PERSON_B, scope=ROOM, member=True, source="sync")
    await backend.put_relation(scope=ROOM, relation="member", person_id=PERSON_B)
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=PERSON_B)
    await _approved(svc, links)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.COMPLETE
    # A fresh engine reads back exactly what committed.
    other = create_sql_engine(engine.url.render_as_string(hide_password=False))
    try:
        readback = SqlBindingStore(other)
        assert await readback.canonical_person(PERSON_B) == PERSON_A
        survivor = await readback.person(PERSON_A)
        assert survivor is not None
        receiver = await readback.record_for(account=ACCT_R)
        assert receiver is not None and receiver.person_id == PERSON_A
        memberships = await readback.memberships_for(PERSON_A)
        room = {m for m in memberships if m.scope == ROOM}
        assert {frozenset(m.sources) for m in room} == {frozenset({"sync"})}
        assert await readback.memberships_for(PERSON_B) == memberships
    finally:
        await other.dispose()
    rows = await backend.relations_on(scope=ROOM)
    assert ("member", PERSON_A) in rows and ("grant_view", PERSON_A) in rows
    assert not any(s == PERSON_B for _, s in rows)


async def test_sql_restart_resumes_the_incomplete_commit_under_new_engines(store):
    store, backend, topology, registry, links, svc, engine = await _world(store)
    await _approved(svc, links)
    # Kill the effect mid-flight: consolidate done, Kratos unreachable.
    class _DeadRegistry:
        def __init__(self, inner):
            self._inner = inner
        def __getattr__(self, name):
            return getattr(self._inner, name)
        async def set_state(self, **kw):
            from nora.domain.managed import unavailable
            return unavailable("kratos-down")
    svc._registry = _DeadRegistry(registry)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert await store.identity_held(PERSON_A) and await store.identity_held(PERSON_B)
    # A wholly new stack — the restart case — resumes and finishes.
    new_engine = create_sql_engine(engine.url.render_as_string(hide_password=False))
    try:
        new_store, new_links = SqlBindingStore(new_engine), SqlLinkRequests(new_engine)
        recovered = LinkPublicationService(new_store, backend, topology, registry, new_links)
        finished = await recovered.reconcile()
        assert [r.operation_id for r in finished] == [_journal_id("synthetic-request")]
        assert not await new_store.identity_held(PERSON_A)
        assert (await new_links.get("synthetic-request")).state is LinkState.COMMITTED
        assert (await registry.identity_state(external_id=PERSON_B)).verdict.value == "denied"
        assert await new_store.canonical_person(PERSON_B) == PERSON_A
    finally:
        await new_engine.dispose()


async def test_sql_concurrent_link_attempts_serialize_on_one_winner(store):
    store, _backend, _topology, _registry, links, svc, engine = await _world(store)
    await _approved(svc, links)
    import asyncio
    outcomes = await asyncio.gather(
        svc.commit("synthetic-request", now=NOW + timedelta(seconds=3)),
        svc.commit("synthetic-request", now=NOW + timedelta(seconds=3)),
        svc.commit("synthetic-request", now=NOW + timedelta(seconds=3)),
    )
    assert all(o.status is OutcomeStatus.COMPLETE for o in outcomes)
    async with engine.connect() as connection:
        persons = await connection.scalar(sa.text(
            "SELECT count(*) FROM managed_person"))
        aliases = await connection.scalar(sa.text(
            "SELECT count(*) FROM managed_alias"))
    assert persons == 1 and aliases == 1


async def test_sql_supports_survive_consolidation_for_both_people(store):
    store, _backend, _topology, _registry, links, svc, _engine = await _world(store)
    # BOTH people independently support the same room membership.
    await store.set_membership(person_id=PERSON_A, scope=ROOM, member=True, source="delegation")
    await store.set_membership(person_id=PERSON_B, scope=ROOM, member=True, source="sync")
    await _approved(svc, links)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.COMPLETE
    merged = {m for m in await store.memberships_for(PERSON_A) if m.scope == ROOM}
    assert len(merged) == 1
    assert merged.pop().sources == {"delegation", "sync"}
    # A roster sync that forgets the room retracts ONLY its own support.
    outcome = await store.drop_membership_support(
        person_id=PERSON_A, scope=ROOM, source="sync")
    assert outcome.status is OutcomeStatus.COMPLETE
    remaining = {m for m in await store.memberships_for(PERSON_A) if m.scope == ROOM}
    assert remaining.pop().state is MembershipState.MEMBER
    # And retracting the last support takes the standing down.
    await store.drop_membership_support(person_id=PERSON_A, scope=ROOM, source="delegation")
    assert {m for m in await store.memberships_for(PERSON_A)
            if m.scope == ROOM}.pop().state is MembershipState.LEFT
