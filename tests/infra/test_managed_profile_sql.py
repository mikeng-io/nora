"""The profile update on real PostgreSQL — the `managed_person.display_name`
projection persists, the journal row survives a "restart" (a freshly
constructed service over a new engine), and `reconcile` finishes exactly
the recorded effect. Kratos stays a fake here — no real Ory service exists
in this checkout — so the registry proves the projection ordering, not
Kratos itself.
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.domain.authentication import AccountAuthentication, SessionBinding
from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    OperationJournalState,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.domain.management import (
    Actor,
    ActorKind,
    Operation,
    ProfileEdit,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)

PERSON = "generated-person-sql"
ACCT = Identifier("discord", "user_id", "generated-account-sql")
ACCOUNT_ID = str(uuid4())
NEW_NAME = "Generated Sql Name"
OLDER_NAME = "Generated Sql Original"


@pytest.fixture
def database(clean_pg_url):
    return clean_pg_url


@pytest.fixture
async def store(database):
    engine = create_sql_engine(database)
    try:
        yield SqlBindingStore(engine)
    finally:
        await engine.dispose()


def _services(store, registry):
    return ManagedServices(
        store, FakeAuthorityBackend(), FakeTopologySource(), registry,
        FakeLinkRequests())


async def _enroll(store, registry):
    outcome = await store.register(
        person=ManagedPerson(PERSON, PersonLifecycle.ACTIVE,
                             display_name=OLDER_NAME),
        account=ManagedAccount(ACCOUNT_ID, PERSON, "discord",
                               frozenset({ACCT}), AccountState.BOUND))
    assert outcome.status is OutcomeStatus.COMPLETE
    outcome, identity_id = await registry.ensure_identity(
        external_id=PERSON,
        traits={"display_name": OLDER_NAME, "social": False})
    assert outcome.status is OutcomeStatus.COMPLETE
    await store.correlate_kratos(person_id=PERSON, kratos_identity_id=identity_id)


async def _authentication(store):
    record = await store.record_for(account=ACCT)
    person = await store.person(record.person_id)
    return AccountAuthentication(
        issuer="generated-portal", session_id="generated-session",
        account=ACCT, method="discord_oauth",
        proof_issuer="generated-client", authenticated_at=datetime.now(UTC),
        purpose="session",
        binding=SessionBinding(person.person_id, str(person.revision),
                               record.account_id, str(record.revision)))


async def test_the_projected_name_persists_and_reads_back(store):
    registry = FakeIdentityRegistry()
    services = _services(store, registry)
    await _enroll(store, registry)
    before = await store.person(PERSON)

    outcome = await services.profile.update_display_name(
        await _authentication(store),
        operation_id="generated-sql-op-1", display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.COMPLETE

    person = await store.person(PERSON)
    assert person is not None and person.display_name == NEW_NAME
    assert person.revision == before.revision
    assert registry.identities[PERSON]["traits"]["social"] is False
    # The private consumer reads the same persisted projection.
    ref = await services.access.person_ref(PERSON)
    assert ref is not None and ref.name == NEW_NAME


async def test_a_reconstructed_service_resumes_the_journaled_update(
    store, database
):
    registry = FakeIdentityRegistry()
    services = _services(store, registry)
    await _enroll(store, registry)

    # The kratos step lands, the projection dies — a crash-shaped fault.
    original = SqlBindingStore.set_display_name
    calls = {"n": 0}

    async def fail_once(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("generated projection outage")
        return await original(self, **kwargs)

    try:
        SqlBindingStore.set_display_name = fail_once
        outcome = await services.profile.update_display_name(
            await _authentication(store),
            operation_id="generated-sql-op-2", display_name=NEW_NAME)
    finally:
        SqlBindingStore.set_display_name = original
    assert outcome.status is OutcomeStatus.INCOMPLETE
    record = await store.operation("generated-sql-op-2")
    assert record is not None and record.steps["kratos"] == "done"
    assert registry.identities[PERSON]["traits"]["display_name"] == NEW_NAME
    assert (await store.person(PERSON)).display_name == OLDER_NAME

    # A "restarted" service — new engine, new store, same database —
    # resumes the journaled effect, nothing else.
    engine2 = create_sql_engine(database)
    try:
        recovered = _services(SqlBindingStore(engine2), registry)
        finished = await recovered.profile.reconcile()
    finally:
        await engine2.dispose()
    assert [row.operation_id for row in finished] == ["generated-sql-op-2"]
    person = await store.person(PERSON)
    assert person is not None and person.display_name == NEW_NAME
    record = await store.operation("generated-sql-op-2")
    assert record.state is OperationJournalState.COMPLETE


ROOM = Scope(ScopeKind.ROOM, "generated-sql-room")
OPERATOR = "generated-sql-operator"
OP_ACCT = Identifier("discord", "user_id", "generated-operator-sql")
NICK = "Generated Sql Nickname"


def _scoped_services(store, registry, backend=None):
    return ManagedServices(
        store, backend or FakeAuthorityBackend(),
        FakeTopologySource(rooms={ROOM.id: ("generated-sql-unit", False)},
                           units={"generated-sql-unit": "generated-sql-ws"},
                           workspaces={"generated-sql-ws"}),
        registry, FakeLinkRequests())


async def _enroll_operator(store):
    outcome = await store.register(
        person=ManagedPerson(OPERATOR, PersonLifecycle.ACTIVE),
        account=ManagedAccount(str(uuid4()), OPERATOR, "discord",
                               frozenset({OP_ACCT}), AccountState.BOUND))
    assert outcome.status is OutcomeStatus.COMPLETE


def _profile_op(revision, op_id="generated-sql-op-3", *, nickname=NICK,
                scope=ROOM):
    return Operation(
        operation_id=op_id, kind="update_profile",
        actor=Actor(ActorKind.OPERATOR, person_id=OPERATOR, scope=scope),
        target=Target(TargetKind.PERSON, person_id=PERSON),
        payload=ProfileEdit(nickname=nickname, scope=scope),
        observed_revisions={"identity": revision, "authority": "a"})


async def test_a_scoped_nickname_persists_without_touching_the_global_name(
    store,
):
    registry = FakeIdentityRegistry()
    backend = FakeAuthorityBackend()
    services = _scoped_services(store, registry, backend)
    await _enroll(store, registry)
    await _enroll_operator(store)
    await backend.put_relation(scope=ROOM, relation="admin",
                               person_id=OPERATOR)

    person = await store.person(PERSON)
    outcome = await services.scoped_profile.apply(
        _profile_op(str(person.revision)))
    assert outcome.status is OutcomeStatus.COMPLETE

    # The row is (canonical person, exact scope) metadata; the global
    # projection and the Kratos trait are untouched by the local edit.
    assert await store.scoped_name_for(person_id=PERSON, scope=ROOM) == NICK
    assert await store.scoped_name_for(
        person_id=PERSON,
        scope=Scope(ScopeKind.UNIT, "generated-sql-unit")) is None
    person = await store.person(PERSON)
    assert person is not None and person.display_name == OLDER_NAME
    assert registry.identities[PERSON]["traits"]["display_name"] == OLDER_NAME
    ref = await services.access.person_ref(PERSON, scope=ROOM)
    assert ref is not None and ref.name == NICK
    record = await store.operation("generated-sql-op-3")
    assert record is not None
    assert record.state is OperationJournalState.COMPLETE


async def test_a_reconstructed_service_resumes_the_journaled_scoped_edit(
    store, database
):
    registry = FakeIdentityRegistry()
    backend = FakeAuthorityBackend()
    await _enroll(store, registry)
    await _enroll_operator(store)
    await backend.put_relation(scope=ROOM, relation="admin",
                               person_id=OPERATOR)

    person = await store.person(PERSON)
    flaky = _scoped_services(_FailOnceScopedStore(store._engine), registry, backend)
    outcome = await flaky.scoped_profile.apply(_profile_op(str(person.revision)))
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    record = await store.operation("generated-sql-op-3")
    assert record is not None
    assert record.state is not OperationJournalState.COMPLETE
    assert await store.scoped_name_for(person_id=PERSON, scope=ROOM) is None

    # A "restarted" service — new engine, same database — finishes the
    # journaled nickname, nothing else.
    engine2 = create_sql_engine(database)
    try:
        recovered = _scoped_services(
            SqlBindingStore(engine2), registry, backend)
        finished = await recovered.scoped_profile.reconcile()
    finally:
        await engine2.dispose()
    assert [row.operation_id for row in finished] == ["generated-sql-op-3"]
    assert await store.scoped_name_for(person_id=PERSON, scope=ROOM) == NICK
    person = await store.person(PERSON)
    assert person is not None and person.display_name == OLDER_NAME
    record = await store.operation("generated-sql-op-3")
    assert record.state is OperationJournalState.COMPLETE


class _FailOnceScopedStore(SqlBindingStore):
    def __init__(self, engine):
        super().__init__(engine)
        self.fail_write = True

    async def set_scoped_name(self, **kwargs):
        if self.fail_write:
            self.fail_write = False
            raise RuntimeError("generated scoped write outage")
        return await super().set_scoped_name(**kwargs)


async def test_sql_restart_does_not_retarget_a_pending_former_person_edit(store, database):
    registry = FakeIdentityRegistry()
    backend = FakeAuthorityBackend()
    await _enroll(store, registry)
    await _enroll_operator(store)
    await backend.put_relation(scope=ROOM, relation="admin", person_id=OPERATOR)
    services = _scoped_services(_FailOnceScopedStore(store._engine), registry, backend)
    person = await store.person(PERSON)
    pending = await services.scoped_profile.apply(_profile_op(str(person.revision)))
    assert pending.status is OutcomeStatus.UNAVAILABLE
    survivor = "generated-sql-survivor"
    await store.register(person=ManagedPerson(survivor, PersonLifecycle.ACTIVE),
        account=ManagedAccount(str(uuid4()), survivor, "discord", frozenset({
            Identifier("discord", "user_id", "generated-survivor-account")}), AccountState.BOUND))
    merged = await store.consolidate(survivor=survivor, former=PERSON)
    assert merged.status is OutcomeStatus.COMPLETE
    current = await store.person(survivor)
    newer = replace(_profile_op(
        str(current.revision), "generated-sql-newer", nickname="Generated Newer"),
        target=Target(TargetKind.PERSON, person_id=survivor))
    active = _scoped_services(store, registry, backend)
    assert (await active.scoped_profile.apply(newer)).status is OutcomeStatus.COMPLETE
    engine2 = create_sql_engine(database)
    try:
        recovered = _scoped_services(SqlBindingStore(engine2), registry, backend)
        finished = await recovered.scoped_profile.reconcile()
    finally:
        await engine2.dispose()
    assert await store.scoped_name_for(person_id=survivor, scope=ROOM) == "Generated Newer"
    assert finished == ()
    record = await store.operation("generated-sql-op-3")
    assert record.outcome == "denied" and record.detail == "profile-target-changed"
