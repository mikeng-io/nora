"""Executed PostgreSQL work and concurrent community roster provisioning.

The upstream identity/authority collaborators are shared fakes. These probes
measure SQL statement count, not live Ory requests or PostgreSQL row work.
"""

import asyncio
import json
from collections import Counter
from time import perf_counter

import pytest
import sqlalchemy as sa

from nora.domain.identity import Identifier
from nora.domain.managed import ObservedMember, OutcomeStatus
from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.infra.sql.account_links import SqlLinkRequests
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people.app.managed import ManagedServices
from nora.people.app.roster_provisioning import RosterProvisioning
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeIdentityRegistry,
    FakeTopologySource,
)

UNIT = Scope(ScopeKind.UNIT, "generated-scale-unit")
ACTOR = Actor(ActorKind.SERVICE, service_id="generated-scale-bridge", sources={"discord"})
TABLES = (
    "managed_link_decision, managed_link_request, managed_membership_support,"
    " managed_membership, managed_alias, managed_identifier, managed_account,"
    " managed_identity_hold, managed_authority_block, managed_operation, managed_person"
)


@pytest.fixture(scope="module")
def database(pg_module_url):
    return pg_module_url


@pytest.fixture
async def engine(database):
    engine = create_sql_engine(database)
    await _clear(engine)
    yield engine
    await engine.dispose()


async def _clear(engine):
    async with engine.begin() as connection:
        await connection.execute(sa.text(f"TRUNCATE {TABLES} CASCADE"))


def _application(engine, registry, backend):
    graph = ManagedServices(
        SqlBindingStore(engine), backend,
        FakeTopologySource(units={UNIT.id: "generated-scale-workspace"},
                           workspaces={"generated-scale-workspace"}),
        registry, SqlLinkRequests(engine),
    )
    return graph, RosterProvisioning(
        graph.bindings, registry, graph.topology, graph.registration, graph.authority,
    )


def _members(size):
    return tuple(
        ObservedMember(frozenset({Identifier("discord", "user_id", f"generated-scale-{i}")}),
                       "person")
        for i in range(size)
    )


def _request(size, collection="generated-scale-collection"):
    return {"operation_id": f"generated-scale-{size}-{collection}", "actor": ACTOR,
            "scope": UNIT, "collection": collection, "members": _members(size),
            "complete_roster": True}


async def test_initial_roster_sql_statement_work_is_linear_at_community_sizes(engine):
    measured = {}
    for size in (20, 100):
        await _clear(engine)
        registry, backend = FakeIdentityRegistry(), FakeAuthorityBackend()
        graph, application = _application(engine, registry, backend)
        statements = Counter()

        def record(connection, cursor, statement, parameters, context, executemany,
                   counter=statements):
            counter[statement.split(None, 1)[0].upper()] += 1

        sa.event.listen(engine.sync_engine, "before_cursor_execute", record)
        started = perf_counter()
        try:
            outcome = await application.apply(**_request(size))
        finally:
            sa.event.remove(engine.sync_engine, "before_cursor_execute", record)
        assert outcome.status is OutcomeStatus.COMPLETE
        assert len(await graph.bindings.persons()) == size
        assert len(await graph.bindings.members_of(UNIT)) == size
        measured[size] = sum(statements.values())
        print(json.dumps({"accounts": size, "sql_statements": dict(statements),
                          "total": measured[size], "seconds": perf_counter() - started}))
    # Five times the accounts may add fixed setup work, not a quadratic query loop.
    assert measured[20] > 0
    assert measured[100] <= measured[20] * 5 + 40


async def test_two_concurrent_community_sources_share_registrations_and_keep_independent_supports(
    engine, database,
):
    other_engine = create_sql_engine(database)
    registry, backend = FakeIdentityRegistry(), FakeAuthorityBackend()
    first_graph, first = _application(engine, registry, backend)
    _, second = _application(other_engine, registry, backend)
    ready = asyncio.Event()

    async def apply(application, collection):
        await ready.wait()
        return await application.apply(**_request(150, collection))

    tasks = [asyncio.create_task(apply(first, "generated-source-a")),
             asyncio.create_task(apply(second, "generated-source-b"))]
    ready.set()
    started = perf_counter()
    try:
        outcomes = await asyncio.gather(*tasks)
        assert [outcome.status for outcome in outcomes] == [OutcomeStatus.COMPLETE] * 2
        people = await first_graph.bindings.persons()
        assert len(people) == 150
        owners = [await first_graph.bindings.record_for(account=next(iter(member.identifiers)))
                  for member in _members(150)]
        assert all(owner is not None for owner in owners)
        assert len({owner.account_id for owner in owners}) == 150
        assert len({owner.person_id for owner in owners}) == 150
        membership = await first_graph.bindings.members_of(UNIT)
        supports = {'sync:["generated-scale-bridge","generated-source-a"]',
                    'sync:["generated-scale-bridge","generated-source-b"]'}
        assert len(membership) == 150
        assert all(member.sources == supports for member in membership)
        departure = {**_request(150, "generated-source-a"),
                     "operation_id": "generated-source-a-departure", "members": ()}
        assert (await first.apply(**departure)).status is OutcomeStatus.COMPLETE
        retained = await first_graph.bindings.members_of(UNIT)
        assert len(retained) == 150
        assert all(member.sources == {'sync:["generated-scale-bridge","generated-source-b"]'}
                   for member in retained)
        assert len(await first_graph.bindings.persons()) == 150
        print(json.dumps({"concurrent_sources": 2, "accounts_per_source": 150,
                          "canonical_people": 150, "seconds": perf_counter() - started}))
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await other_engine.dispose()
