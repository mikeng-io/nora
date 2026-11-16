"""The admission publication seam on its fake and production SQL adapter.

`publish_held_person` commits a person, its accounts and the import's
identity hold in one durable transaction; `complete_import` commits the
COMPLETE journal and the release of all and only that import's holds in
one. Both implementations are held to the same contract here — the fake
used by the unit suite is only honest if it cannot differ from Postgres.
"""

from types import MappingProxyType

import pytest
import sqlalchemy as sa

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    IdentifierConflictError,
    ManagedAccount,
    ManagedPerson,
    OperationJournalState,
    OperationRecord,
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
)
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people.adapters.managed_handoff import SqlAdmissionProbe
from nora.people.app.managed_authority import AuthorityService
from nora.testing.managed import FakeAuthorityBackend, FakeBindingStore, FakeTopologySource
from tests.fakes import FakeAdmissionProbe

OPERATION = "generated-import"


@pytest.fixture(scope="module")
def database(pg_module_url):
    return pg_module_url


@pytest.fixture(params=["fake", "sql"])
async def seam(request, database):
    if request.param == "fake":
        store = FakeBindingStore()
        yield store, FakeAdmissionProbe(store)
        return
    engine = create_sql_engine(database)
    async with engine.begin() as connection:
        await connection.execute(sa.text(
            "TRUNCATE managed_identifier, managed_account, managed_identity_hold,"
            " managed_operation, managed_person CASCADE"))
    try:
        store = SqlBindingStore(engine)
        yield store, SqlAdmissionProbe(engine)
    finally:
        await engine.dispose()


def _person(person_id: str = "generated-person") -> ManagedPerson:
    return ManagedPerson(person_id=person_id, lifecycle=PersonLifecycle.ACTIVE)


def _account(
    account_id: str, person_id: str, identifiers: frozenset[Identifier],
    platform: str = "signal",
) -> ManagedAccount:
    return ManagedAccount(
        account_id=account_id, person_id=person_id, platform=platform,
        identifiers=identifiers, state=AccountState.BOUND)


async def test_publication_commits_person_accounts_and_the_import_hold(seam):
    store, probe = seam
    person = _person()
    accounts = (
        _account("00000000-0000-0000-0000-000000000001", person.person_id,
                 frozenset({Identifier("signal", "phone", "+85200111111")})),
        _account("00000000-0000-0000-0000-000000000002", person.person_id,
                 frozenset({Identifier("telegram", "user_id", "900001")}),
                 platform="telegram"),
    )
    outcome = await probe.publish_held_person(
        person=person, accounts=accounts, operation_id=OPERATION)
    assert outcome.status is OutcomeStatus.COMPLETE
    found = await store.person(person.person_id)
    assert found is not None
    assert (found.person_id, found.lifecycle, found.display_name) == (
        person.person_id, person.lifecycle, person.display_name)
    assert len(await store.accounts_for(person.person_id)) == 2
    bound = await store.record_for(
        account=Identifier("telegram", "user_id", "900001"))
    assert bound is not None and bound.person_id == person.person_id
    assert await store.identity_held(person.person_id)


async def test_a_listed_person_without_accounts_is_still_published_held(seam):
    store, probe = seam
    person = _person("account-less")
    outcome = await probe.publish_held_person(
        person=person, accounts=(), operation_id=OPERATION)
    assert outcome.status is OutcomeStatus.COMPLETE
    found = await store.person("account-less")
    assert found is not None and found.lifecycle is PersonLifecycle.ACTIVE
    assert not await store.accounts_for("account-less")
    assert await store.identity_held("account-less")


async def test_a_pre_commit_failure_leaves_no_part_of_the_person(seam):
    """A conflict raised inside the publication transaction rolls the
    person, its first account and the hold back together."""
    store, probe = seam
    await store.register(person=_person("foreign"), account=_account(
        "00000000-0000-0000-0000-000000000003", "foreign",
        frozenset({Identifier("signal", "phone", "+85200999999")})))
    person = _person("collider")
    with pytest.raises(IdentifierConflictError):
        await probe.publish_held_person(
            person=person,
            accounts=(
                _account("00000000-0000-0000-0000-000000000004", "collider",
                         frozenset({Identifier("signal", "phone", "+85200444444")})),
                _account("00000000-0000-0000-0000-000000000005", "collider",
                         frozenset({Identifier("signal", "phone", "+85200999999")})),
            ),
            operation_id=OPERATION)
    assert await store.person("collider") is None
    assert not await store.identity_held("collider")
    bound = await store.record_for(
        account=Identifier("signal", "phone", "+85200444444"))
    assert bound is None


async def test_republication_is_idempotent(seam):
    store, probe = seam
    person = _person()
    account = _account("00000000-0000-0000-0000-000000000006", person.person_id,
                       frozenset({Identifier("signal", "phone", "+85200555555")}))
    for _ in range(2):
        outcome = await probe.publish_held_person(
            person=person, accounts=(account,), operation_id=OPERATION)
        assert outcome.status is OutcomeStatus.COMPLETE
    assert len(await store.accounts_for(person.person_id)) == 1
    assert await store.identity_held(person.person_id)


async def test_completion_commits_the_journal_and_only_own_release(seam):
    store, probe = seam
    record = OperationRecord(
        OPERATION, "import", OperationJournalState.INCOMPLETE,
        MappingProxyType({"people": "done"}), snapshot=MappingProxyType({}))
    assert (await store.record_operation(record)).status is OutcomeStatus.COMPLETE
    for person_id in ("held-one", "held-two"):
        assert (await probe.publish_held_person(
            person=_person(person_id), accounts=(),
            operation_id=OPERATION)).status is OutcomeStatus.COMPLETE
    await store.hold_identity(person_id="held-one", operation_id="foreign-op")
    finished = OperationRecord(
        OPERATION, "import", OperationJournalState.COMPLETE,
        record.steps, outcome="complete", detail="people=2")
    outcome = await probe.complete_import(finished, person_ids=("held-one", "held-two"))
    assert outcome.status is OutcomeStatus.COMPLETE
    journal = await store.operation(OPERATION)
    assert journal is not None
    assert journal.state is OperationJournalState.COMPLETE
    assert journal.outcome == "complete" and journal.detail == "people=2"
    assert await store.identity_held("held-one")      # the foreign hold remains
    assert not await store.identity_held("held-two")
    room = Scope(ScopeKind.ROOM, "signal/generated-room")
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={room.id: ("generated-unit", False)},
        units={"generated-unit": "generated-workspace"}, workspaces={"generated-workspace"})
    authority = AuthorityService(store, backend, topology)
    for pid in ("held-one", "held-two"):
        assert (await backend.put_relation(
            scope=room, relation="member", person_id=pid)).status is OutcomeStatus.COMPLETE
        assert (await backend.check(
            scope=room, person_id=pid, permit="view")).verdict is Verdict.ALLOWED
    protected = await authority.check(scope=room, person_id="held-one", permit="view")
    assert protected.verdict is Verdict.UNAVAILABLE
    assert protected.detail == "identity-publication-pending"
    assert (await authority.check(
        scope=room, person_id="held-two", permit="view")).verdict is Verdict.ALLOWED


async def test_a_journal_the_completion_cannot_land_is_refused(seam):
    store, probe = seam
    await probe.publish_held_person(
        person=_person("held"), accounts=(), operation_id=OPERATION)
    record = OperationRecord(
        "absent-journal", "import", OperationJournalState.COMPLETE,
        MappingProxyType({}), outcome="complete", detail="people=1")
    outcome = await probe.complete_import(record, person_ids=("held",))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "absent-operation"
    assert await store.identity_held("held")
