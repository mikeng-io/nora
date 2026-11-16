"""The initial file admission against real PostgreSQL — the target probe sees
the rows the port reads hide, and an interrupted import resumes on a fresh
engine without duplicating a single effect."""

from types import MappingProxyType

import pytest
import sqlalchemy as sa

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    OperationJournalState,
    OperationRecord,
    OutcomeStatus,
    PersonLifecycle,
    room_scope_id,
    unavailable,
)
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people.adapters.managed_handoff import SqlAdmissionProbe
from nora.people.app.managed_admission import FileAdmission, plan
from nora.testing.managed import FakeAuthorityBackend, FakeIdentityRegistry

IDENTITY = """\
[persons.miles]
name = "Miles"
aliases = ["generated-nick"]
former_person_ids = ["miles_old"]
social = false

[[persons.miles.signal]]
phone = "+85200000001"
label = "personal"

[[persons.miles.telegram]]
user_id = "800001"

[persons.ada]
name = "Ada"

[[persons.ada.signal]]
phone = "+85200000003"
"""

POLICY = """\
default = "deny"

[[conversations]]
platform = "signal"
id = "generated-room-a"
unit = "family"
label = "Room A"
persons = ["miles"]

[[conversations]]
platform = "discord"
id = "generated-g1/c1"
unit = "family"
label = "Room C"
persons = ["ada", "listed_only"]

[units.family]
workspace = "home"
"""


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
            " managed_link_decision, managed_link_request, managed_link_delivery"
            " CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def sources(tmp_path):
    identity_path = tmp_path / "identity.toml"
    policy_path = tmp_path / "policy.toml"
    identity_path.write_text(IDENTITY)
    policy_path.write_text(POLICY)
    return identity_path, policy_path


def admission(engine, backend=None, registry=None):
    return FileAdmission(
        bindings=SqlBindingStore(engine),
        backend=backend or FakeAuthorityBackend(),
        registry=registry or FakeIdentityRegistry(),
        probe=SqlAdmissionProbe(engine),
    )


class _NoWrites(FakeAuthorityBackend):
    """Reads answer; relation writes report an outage — the authority step
    fails mid-journal while the claim probe still passes."""

    def __init__(self) -> None:
        super().__init__()
        self.writes_ok = False

    async def put_relation(self, *, scope, relation, person_id):
        if not self.writes_ok:
            return unavailable("outage")
        return await super().put_relation(
            scope=scope, relation=relation, person_id=person_id)


async def _foreign_account(store):
    await store.register(
        person=ManagedPerson(person_id="foreign", lifecycle=PersonLifecycle.ACTIVE),
        account=ManagedAccount(
            account_id="00000000-0000-0000-0000-0000000000ff",
            person_id="foreign", platform="signal",
            identifiers=frozenset({Identifier("signal", "phone", "+85200999999")}),
            state=AccountState.BOUND),
    )


async def test_probe_sees_everything_the_port_hides(engine, sources):
    """A target holding a suspended person, a revoked account, a foreign hold
    and a foreign journal is not empty — the port reads would report it so."""
    store = SqlBindingStore(engine)
    await _foreign_account(store)
    await store.set_lifecycle(person_id="foreign", lifecycle=PersonLifecycle.SUSPENDED)
    await store.revoke_binding(
        account=Identifier("signal", "phone", "+85200999999"))
    await store.hold_identity(person_id="foreign", operation_id="other-op")
    await store.record_operation(OperationRecord(
        "other-op", "grant_access", OperationJournalState.INCOMPLETE,
        MappingProxyType({}), snapshot=MappingProxyType({})))
    assert not await store.persons()          # the active roster hides all of it
    identity_path, policy_path = sources
    outcome = await admission(engine).apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "foreign-journal"


async def test_apply_persists_people_accounts_aliases_and_memberships(
    engine, sources,
):
    identity_path, policy_path = sources
    outcome = await admission(engine).apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    store = SqlBindingStore(engine)
    miles = await store.person("miles")
    assert miles is not None and miles.kratos_identity_id is not None
    bound = await store.record_for(
        account=Identifier("signal", "phone", "+85200000001"))
    assert bound is not None and bound.person_id == "miles" and bound.label == "personal"
    assert len(await store.accounts_for("miles")) == 2
    assert await store.canonical_person("miles_old") == "miles"
    only = await store.person("listed_only")
    assert only is not None and not await store.accounts_for("listed_only")
    members = await store.members_of(
        Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a")))
    assert len(members) == 1 and members[0].person_id == "miles"
    assert members[0].sources == {"admission"}
    assert not await store.identity_held("miles")
    journal = await store.operation(plan(identity_path, policy_path).import_id)
    assert journal is not None and journal.state is OperationJournalState.COMPLETE


async def test_fresh_engine_resumes_the_interrupted_import(engine, database, sources):
    """An outage mid-run leaves the journaled steps; a fresh engine and
    service finish exactly the missing work."""
    backend = _NoWrites()
    registry = FakeIdentityRegistry()   # Kratos persists — only the process/engine restarts
    service = admission(engine, backend, registry)
    identity_path, policy_path = sources
    first = await service.apply(identity_path, policy_path)
    assert first.status is OutcomeStatus.UNAVAILABLE
    store = SqlBindingStore(engine)
    assert await store.identity_held("miles")
    await engine.dispose()
    restarted = create_sql_engine(database)
    try:
        backend.writes_ok = True
        resumed = admission(restarted, backend, registry)
        outcome = await resumed.apply(identity_path, policy_path)
        assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
        store = SqlBindingStore(restarted)
        assert len(await store.accounts_for("miles")) == 2
        assert not await store.identity_held("miles")
        journal = await store.operation(plan(identity_path, policy_path).import_id)
        assert journal is not None
        assert journal.state is OperationJournalState.COMPLETE
    finally:
        await restarted.dispose()
