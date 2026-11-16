"""The profile update on real PostgreSQL — the `managed_person.display_name`
projection persists, the journal row survives a "restart" (a freshly
constructed service over a new engine), and `reconcile` finishes exactly
the recorded effect. Kratos stays a fake here — no real Ory service exists
in this checkout — so the registry proves the projection ordering, not
Kratos itself.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa

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
    assert outcome.status is OutcomeStatus.UNAVAILABLE
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
