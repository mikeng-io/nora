"""A new process graph resumes a bulk roster from its durable PostgreSQL journal."""

import asyncio
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    ObservedMember,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.domain.management import (
    Actor,
    ActorKind,
    Operation,
    RosterSync,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
)
from nora.infra.sql.account_links import SqlLinkRequests
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people import RosterProvisioning
from nora.people.app.managed import ManagedServices
from nora.testing.managed import FakeAuthorityBackend, FakeIdentityRegistry, FakeTopologySource

UNIT = Scope(ScopeKind.UNIT, "generated-unit")
ACTOR = Actor(ActorKind.SERVICE, service_id="generated-bridge", sources={"discord"})
MEMBER = Identifier("discord", "user_id", "generated-member")


@pytest.fixture(scope="module")
def database(pg_module_url):
    return pg_module_url


@pytest.fixture
async def engine(database):
    engine = create_sql_engine(database)
    async with engine.begin() as connection:
        await connection.execute(sa.text(
            "TRUNCATE managed_membership_support, managed_membership, managed_alias,"
            " managed_identifier, managed_account, managed_identity_hold,"
            " managed_authority_block, managed_operation, managed_person,"
            " managed_link_decision, managed_link_request,"
            " managed_observation CASCADE"))
    yield engine
    await engine.dispose()


def services(engine, registry, backend):
    graph = ManagedServices(SqlBindingStore(engine), backend,
        FakeTopologySource(units={UNIT.id: "generated-workspace"},
                           workspaces={"generated-workspace"}),
        registry, SqlLinkRequests(engine))
    return graph, RosterProvisioning(graph.bindings, registry, graph.topology,
                                     graph.registration, graph.authority)


async def test_fresh_engine_resumes_bulk_registration_without_duplicate_or_registration_grants(
    engine, database,
):
    registry, backend = FakeIdentityRegistry(), FakeAuthorityBackend()
    graph, application = services(engine, registry, backend)
    account = Identifier("discord", "user_id", "generated-account")
    request = {"operation_id": "generated-snapshot", "actor": ACTOR, "scope": UNIT,
               "collection": "generated-guild",
               "members": (ObservedMember(frozenset({account})),),
               "complete_roster": True,
               "observed_ms": int(time.time() * 1000)}
    registry.down = True
    assert (await application.apply(**request)).status is OutcomeStatus.INCOMPLETE
    before = await graph.bindings.record_for(account=account)
    assert before is not None
    assert not await graph.bindings.members_of(UNIT)
    restarted = create_sql_engine(database)
    try:
        fresh_graph, fresh = services(restarted, registry, backend)
        registry.down = False
        assert (await fresh.apply(**request)).status is OutcomeStatus.COMPLETE
        after = await fresh_graph.bindings.record_for(account=account)
        assert after.person_id == before.person_id
        assert after.account_id == before.account_id
        members = await fresh_graph.bindings.members_of(UNIT)
        assert len(members) == 1
        assert members[0].sources == {'sync:["generated-bridge","generated-guild"]'}
        assert (await fresh.apply(**request)).status is OutcomeStatus.COMPLETE
        left = await fresh.apply(**{**request,
            "operation_id": "generated-leave", "members": set(),
            # A real producer stamps each fresh read — reusing the earlier
            # instant with different content is a conflict, not a leave.
            "observed_ms": request["observed_ms"] + 1})
        assert left.status is OutcomeStatus.COMPLETE
        historical = await fresh_graph.bindings.memberships_for(before.person_id)
        assert historical[0].state.value == "left"
    finally:
        await restarted.dispose()


def _sync(operation_id: str, observed_ms: int, members=(), *, complete=True):
    return Operation(
        operation_id=operation_id, kind="sync_membership", actor=ACTOR,
        target=Target(kind=TargetKind.SCOPE, scope=UNIT),
        payload=RosterSync(
            members=frozenset(members), complete=complete,
            collection="generated-guild", observed_ms=observed_ms),
        observed_revisions={"authority": "0"})


async def _seed_member(graph):
    person_id = "person_generated"
    await graph.bindings.register(
        person=ManagedPerson(
            person_id=person_id, lifecycle=PersonLifecycle.ACTIVE),
        account=ManagedAccount(
            account_id=str(uuid4()), person_id=person_id,
            platform="discord", identifiers=frozenset({MEMBER}),
            state=AccountState.BOUND),
        membership=None)
    return person_id


async def test_the_observation_mark_orders_across_a_fresh_engine(engine, database):
    """The accepted instant is durable state: a brand-new engine and
    service graph read the same mark, so an older complete roster cannot
    depart after a process restart — and concurrent submissions serialize
    to exactly one accepted instant."""
    graph, _ = services(engine, FakeIdentityRegistry(), FakeAuthorityBackend())
    person_id = await _seed_member(graph)
    now = int(time.time() * 1000)
    assert (await graph.authority.apply(
        _sync("generated-newer", now, {MEMBER}))).status is OutcomeStatus.COMPLETE
    restarted = create_sql_engine(database)
    try:
        fresh, _ = services(restarted, FakeIdentityRegistry(),
                            FakeAuthorityBackend())
        older = await fresh.authority.apply(
            _sync("generated-older", now - 1_000))
        assert older.status is OutcomeStatus.UNAVAILABLE
        assert older.detail == "observation-superseded"
        members = await fresh.bindings.members_of(UNIT)
        assert [m.person_id for m in members] == [person_id]
        # Two submissions racing different instants under the store's
        # serialization: both complete honestly — the newer marks, the
        # older one is refused by it, in whichever order they land.
        first, second = await asyncio.gather(
            fresh.authority.apply(
                _sync("generated-race-new", now + 2_000, {MEMBER})),
            fresh.authority.apply(
                _sync("generated-race-old", now + 1_000, {MEMBER})),
        )
        outcomes = {first.status, second.status}
        assert first.status is not OutcomeStatus.INCOMPLETE
        assert second.status is not OutcomeStatus.INCOMPLETE
        assert OutcomeStatus.COMPLETE in outcomes
        mark = await fresh.bindings.observation_for(
            service="generated-bridge", collection="generated-guild",
            scope=UNIT)
        assert mark is not None and mark.observed_ms == now + 2_000
    finally:
        await restarted.dispose()
