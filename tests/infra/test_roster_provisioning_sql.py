"""A new process graph resumes a bulk roster from its durable PostgreSQL journal."""

import asyncio
import time
from dataclasses import replace
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
    AccountObservation,
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
from nora.people.app.managed_authority import AuthorityService
from nora.testing.managed import FakeAuthorityBackend, FakeIdentityRegistry, FakeTopologySource

UNIT = Scope(ScopeKind.UNIT, "generated-unit")
ACTOR = Actor(ActorKind.SERVICE, service_id="generated-bridge", sources={"discord"})
MEMBER = Identifier("discord", "user_id", "generated-member")


@pytest.fixture
def database(clean_pg_url):
    return clean_pg_url


@pytest.fixture
async def engine(database):
    engine = create_sql_engine(database)
    yield engine
    await engine.dispose()


def services(engine, registry, backend):
    graph = ManagedServices(SqlBindingStore(engine), backend,
        FakeTopologySource(units={UNIT.id: "generated-workspace"},
                           workspaces={"generated-workspace"}),
        registry, SqlLinkRequests(engine))
    return graph, RosterProvisioning(graph.bindings, registry, graph.topology,
                                     graph.registration, graph.authority)


@pytest.mark.parametrize("classification", ["person", "bot", "unknown"])
async def test_fresh_engine_resumes_bulk_registration_without_duplicate_or_registration_grants(
    engine, database, classification,
):
    registry, backend = FakeIdentityRegistry(), FakeAuthorityBackend()
    graph, application = services(engine, registry, backend)
    account = Identifier("discord", "user_id", "generated-account")
    request = {"operation_id": "generated-snapshot", "actor": ACTOR, "scope": UNIT,
               "collection": "generated-guild",
               "members": (ObservedMember(frozenset({account}), classification),),
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


async def test_independent_stores_arbitrate_observations_with_the_postgres_lock(engine, database):
    marked, release, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class PausedMark(SqlBindingStore):
        async def record_observation(self, observation):
            result = await super().record_observation(observation)
            marked.set()
            await release.wait()
            return result

    graph, _ = services(engine, FakeIdentityRegistry(), FakeAuthorityBackend())
    person_id = await _seed_member(graph)
    newer = AuthorityService(PausedMark(engine), graph.backend, graph.topology)
    other_engine = create_sql_engine(database)
    older, _ = services(other_engine, FakeIdentityRegistry(), graph.backend)
    waiter_pids = []

    def before_execute(connection, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            waiter_pids.append(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
            attempted.set()

    sa.event.listen(other_engine.sync_engine, "before_cursor_execute", before_execute)
    now = int(time.time() * 1000)
    new_task = asyncio.create_task(newer.apply(_sync("two-store-new", now, {MEMBER})))
    old_task = None
    try:
        await asyncio.wait_for(marked.wait(), timeout=10)
        old_task = asyncio.create_task(older.authority.apply(_sync("two-store-old", now - 1)))
        await asyncio.wait_for(attempted.wait(), timeout=10)
        async with engine.connect() as observer:
            started = time.monotonic()
            blocked = False
            while not blocked and time.monotonic() - started < 15:
                blocked = await observer.scalar(sa.text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                    "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted)"
                ), {"pid": waiter_pids[0]})
                if not blocked:
                    await asyncio.sleep(0.02)
            assert blocked, (
                "the independent store never contended on the database lock"
                f" (pid {waiter_pids[0]},"
                f" waited {time.monotonic() - started:.1f}s)")
        assert not old_task.done()
        release.set()
        assert (await new_task).status is OutcomeStatus.COMPLETE
        refused = await old_task
        assert refused.status is OutcomeStatus.UNAVAILABLE
        assert refused.detail == "observation-superseded"
        assert await older.bindings.operation("two-store-old") is None
        mark = await older.bindings.observation_for(
            service="generated-bridge", collection="generated-guild", scope=UNIT)
        assert mark is not None and mark.operation_id == "two-store-new"
        assert [m.person_id for m in await older.bindings.members_of(UNIT)] == [person_id]
    finally:
        release.set()
        await asyncio.gather(new_task, *([old_task] if old_task is not None else []),
                             return_exceptions=True)
        sa.event.remove(other_engine.sync_engine, "before_cursor_execute", before_execute)
        await other_engine.dispose()


async def test_concurrent_roster_and_sender_observations_keep_one_sql_binding(engine, database):
    registry, backend = FakeIdentityRegistry(), FakeAuthorityBackend()
    graph, provisioning = services(engine, registry, backend)
    other_engine = create_sql_engine(database)
    try:
        other, _ = services(other_engine, registry, backend)
        sender = Operation("generated-concurrent-sender", "observe_account", ACTOR,
            Target(TargetKind.ACCOUNT, account=MEMBER),
            AccountObservation(frozenset({MEMBER})), {"ownership": "0"})
        roster_result, sender_result = await asyncio.gather(
            provisioning.apply(operation_id="generated-concurrent-roster",
                actor=ACTOR, scope=UNIT, collection="generated-guild",
                members=(ObservedMember(frozenset({MEMBER}), "unknown"),),
                complete_roster=True, observed_ms=int(time.time() * 1000)),
            other.registration.apply(sender))
        assert roster_result.status is OutcomeStatus.COMPLETE
        bound = await graph.bindings.record_for(account=MEMBER)
        assert bound is not None
        if sender_result.status is OutcomeStatus.DENIED:
            assert sender_result.detail == "stale-ownership-revision"
        else:
            assert sender_result.status is OutcomeStatus.COMPLETE
        refreshed = replace(sender, observed_revisions={"ownership": str(bound.revision)})
        assert (await other.registration.apply(refreshed)).status is OutcomeStatus.COMPLETE
        reread = await other.bindings.record_for(account=MEMBER)
        assert reread.account_id == bound.account_id and reread.person_id == bound.person_id
        async with other_engine.connect() as conn:
            counts = (await conn.execute(sa.text(
                "SELECT (SELECT count(*) FROM managed_person), "
                "(SELECT count(*) FROM managed_account)"))).one()
        assert tuple(counts) == (1, 1)
        assert len(registry.identities) == 1
        members = await graph.bindings.members_of(UNIT)
        assert [m.person_id for m in members] == [bound.person_id]
        assert members[0].sources == {'sync:["generated-bridge","generated-guild"]'}
        assert not (await graph.authority.check(scope=UNIT,
            person_id=bound.person_id, permit="manage_members")).allowed
    finally:
        await other_engine.dispose()
