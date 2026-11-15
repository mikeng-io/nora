"""A new process graph resumes a bulk roster from its durable PostgreSQL journal."""

import pytest
import sqlalchemy as sa

from nora.domain.identity import Identifier
from nora.domain.managed import OutcomeStatus
from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.infra.sql.account_links import SqlLinkRequests
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people import RosterProvisioning
from nora.people.app.managed import ManagedServices
from nora.testing.managed import FakeAuthorityBackend, FakeIdentityRegistry, FakeTopologySource

UNIT = Scope(ScopeKind.UNIT, "generated-unit")
ACTOR = Actor(ActorKind.SERVICE, service_id="generated-bridge", sources={"discord"})


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
            " managed_link_decision, managed_link_request CASCADE"))
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
               "collection": "generated-guild", "members": {account}, "complete_roster": True}
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
        left = await fresh.apply(**{**request, "operation_id": "generated-leave", "members": set()})
        assert left.status is OutcomeStatus.COMPLETE
        historical = await fresh_graph.bindings.memberships_for(before.person_id)
        assert historical[0].state.value == "left"
    finally:
        await restarted.dispose()
