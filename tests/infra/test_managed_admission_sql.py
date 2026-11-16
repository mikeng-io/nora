"""The initial file admission against real PostgreSQL — the target probe sees
the rows the port reads hide, an interrupted import resumes on a fresh
engine without duplicating a single effect, and the durable publication
boundaries hold under independent readers: a person is never readable
unheld, and complete-plus-released never lands half-visible."""

import asyncio
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
from nora.testing.managed import FakeAuthorityBackend
from tests.fakes import FakeAdmissionKratos

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


def admission(engine, backend=None, kratos=None):
    return FileAdmission(
        bindings=SqlBindingStore(engine),
        backend=backend or FakeAuthorityBackend(),
        kratos=kratos or FakeAdmissionKratos(),
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
    kratos = FakeAdmissionKratos()   # Kratos persists — only the process/engine restarts
    service = admission(engine, backend, kratos)
    identity_path, policy_path = sources
    first = await service.apply(identity_path, policy_path)
    assert first.status is OutcomeStatus.UNAVAILABLE
    store = SqlBindingStore(engine)
    assert await store.identity_held("miles")
    await engine.dispose()
    restarted = create_sql_engine(database)
    try:
        backend.writes_ok = True
        resumed = admission(restarted, backend, kratos)
        outcome = await resumed.apply(identity_path, policy_path)
        assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
        store = SqlBindingStore(restarted)
        assert len(await store.accounts_for("miles")) == 2
        assert not await store.identity_held("miles")
        journal = await store.operation(plan(identity_path, policy_path).import_id)
        assert journal is not None
        assert journal.state is OperationJournalState.COMPLETE
        identity_path.write_text("changed after import")
        policy_path.write_text("changed after import")
        assert "sources" in journal.snapshot
        assert journal.snapshot["sources"] == {
            "identity_toml": IDENTITY, "policy_toml": POLICY,
        }
    finally:
        await restarted.dispose()


class _DropFirstCorrelate:
    """The real `SqlBindingStore` behind a one-shot fault — the first
    correlation write reports the process loss the remote create outlived."""

    def __init__(self, store: SqlBindingStore) -> None:
        self._store = store
        self.dropped = False

    def __getattr__(self, name):
        return getattr(self._store, name)

    async def correlate_kratos(self, *, person_id, kratos_identity_id):
        if not self.dropped:
            self.dropped = True
            return unavailable("process-lost")
        return await self._store.correlate_kratos(
            person_id=person_id, kratos_identity_id=kratos_identity_id)


async def test_fresh_engine_recovers_an_owned_create_after_process_loss(
    engine, database, sources,
):
    """The create committed remotely; the process died before its SQL
    correlation. A new engine, store and seam read the durable marker,
    adopt exactly that record and finish — one identity, no rewrite."""
    store = SqlBindingStore(engine)
    backend = FakeAuthorityBackend()
    identities: dict = {}
    identity_path, policy_path = sources
    service = FileAdmission(
        bindings=_DropFirstCorrelate(store), backend=backend,
        kratos=FakeAdmissionKratos(identities), probe=SqlAdmissionProbe(engine))
    first = await service.apply(identity_path, policy_path)
    assert first.status is OutcomeStatus.UNAVAILABLE
    created = identities["ada"]
    ada = await store.person("ada")
    assert ada is not None and ada.kratos_identity_id is None
    await engine.dispose()
    restarted = create_sql_engine(database)
    try:
        resumed = FileAdmission(
            bindings=SqlBindingStore(restarted), backend=backend,
            kratos=FakeAdmissionKratos(identities),
            probe=SqlAdmissionProbe(restarted))
        outcome = await resumed.apply(identity_path, policy_path)
        assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
        store = SqlBindingStore(restarted)
        ada = await store.person("ada")
        assert ada is not None and ada.kratos_identity_id == created["id"]
        assert set(identities) == {"ada", "listed_only", "miles"}
        assert identities["miles"]["traits"]["social"] is False
        journal = await store.operation(
            plan(identity_path, policy_path).import_id)
        assert journal is not None
        assert journal.state is OperationJournalState.COMPLETE
    finally:
        await restarted.dispose()


_PUBLISH_LOCK = 770001
_COMPLETE_LOCK = 770002


async def _install_trigger(engine, *, name, event, body):
    ret = "NEW" if event == "INSERT" else "OLD"
    async with engine.begin() as conn:
        await conn.execute(sa.text(f"""
            CREATE OR REPLACE FUNCTION test_{name}() RETURNS trigger AS $$
            BEGIN {body} RETURN {ret};
            END; $$ LANGUAGE plpgsql"""))
        await conn.execute(sa.text(
            f"CREATE TRIGGER {name} BEFORE {event} ON managed_identity_hold"
            f" FOR EACH ROW EXECUTE FUNCTION test_{name}()"))


async def _drop_trigger(engine, name):
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            f"DROP TRIGGER IF EXISTS {name} ON managed_identity_hold"))
        await conn.execute(sa.text(
            f"DROP FUNCTION IF EXISTS test_{name}"))


async def _blocked_on_advisory(engine) -> None:
    """Wait until a session waits on an advisory lock — the writer's open transaction."""
    for _ in range(200):
        async with engine.connect() as conn:
            waiting = await conn.scalar(sa.text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                " AND NOT granted AND database = ("
                "  SELECT oid FROM pg_database"
                "  WHERE datname = current_database())"))
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the writer never reached the advisory lock")


async def test_an_independent_reader_never_sees_an_unheld_person(
    engine, database,
):
    """The publication transaction is open past the person and account
    inserts but cannot commit — the independent reader sees NO part of
    that person; once the commit lands, person and hold appear together."""
    await _install_trigger(
        engine, name="gate_hold_insert", event="INSERT",
        body=f"PERFORM pg_advisory_lock({_PUBLISH_LOCK});"
             f" PERFORM pg_advisory_unlock({_PUBLISH_LOCK});")
    try:
        controller = create_sql_engine(database)
        reader = create_sql_engine(database)
        try:
            async with controller.connect() as conn:
                await conn.execute(sa.text(
                    f"SELECT pg_advisory_lock({_PUBLISH_LOCK})"))
                probe = SqlAdmissionProbe(engine)
                person = ManagedPerson(
                    person_id="blocked", lifecycle=PersonLifecycle.ACTIVE)
                account = ManagedAccount(
                    account_id="00000000-0000-0000-0000-0000000000aa",
                    person_id="blocked", platform="signal",
                    identifiers=frozenset(
                        {Identifier("signal", "phone", "+85200000007")}),
                    state=AccountState.BOUND)
                writer = asyncio.ensure_future(probe.publish_held_person(
                    person=person, accounts=(account,),
                    operation_id="blocked-import"))
                await _blocked_on_advisory(reader)
                store = SqlBindingStore(reader)
                assert await store.person("blocked") is None
                assert await store.resolve("signal", "+85200000007") is None
                assert not await store.identity_held("blocked")
                await conn.execute(sa.text(
                    f"SELECT pg_advisory_unlock({_PUBLISH_LOCK})"))
                outcome = await writer
                assert outcome.status is OutcomeStatus.COMPLETE
                assert await store.person("blocked") is not None
                assert await store.identity_held("blocked")
        finally:
            await reader.dispose()
            await controller.dispose()
    finally:
        await _drop_trigger(engine, "gate_hold_insert")


async def test_complete_and_release_become_visible_together(
    engine, database, sources,
):
    """An apply blocked INSIDE its completion commit leaves the outside
    world at incomplete-plus-held; released, it lands complete-plus-
    unheld — never one without the other."""
    await _install_trigger(
        engine, name="gate_hold_delete", event="DELETE",
        body=f"PERFORM pg_advisory_lock({_COMPLETE_LOCK});"
             f" PERFORM pg_advisory_unlock({_COMPLETE_LOCK});")
    try:
        controller = create_sql_engine(database)
        reader = create_sql_engine(database)
        try:
            async with controller.connect() as conn:
                await conn.execute(sa.text(
                    f"SELECT pg_advisory_lock({_COMPLETE_LOCK})"))
                identity_path, policy_path = sources
                service = admission(engine)
                writer = asyncio.ensure_future(
                    service.apply(identity_path, policy_path))
                await _blocked_on_advisory(reader)
                store = SqlBindingStore(reader)
                journal = await store.operation(
                    plan(identity_path, policy_path).import_id)
                assert journal is not None
                assert journal.state is OperationJournalState.INCOMPLETE
                assert await store.person("miles") is not None
                assert all([
                    await store.identity_held(pid)
                    for pid in ("miles", "ada", "listed_only")
                ])
                await conn.execute(sa.text(
                    f"SELECT pg_advisory_unlock({_COMPLETE_LOCK})"))
                outcome = await writer
                assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
                journal = await store.operation(
                    plan(identity_path, policy_path).import_id)
                assert journal is not None
                assert journal.state is OperationJournalState.COMPLETE
                assert not any([
                    await store.identity_held(pid)
                    for pid in ("miles", "ada", "listed_only")
                ])
        finally:
            await reader.dispose()
            await controller.dispose()
    finally:
        await _drop_trigger(engine, "gate_hold_delete")


async def test_a_completion_commit_failure_leaves_incomplete_and_held(
    engine, sources,
):
    """A failure inside the completion transaction reports unavailable —
    never complete — leaves journal and holds exactly as before, and the
    own retry still lands complete-plus-unheld."""
    await _install_trigger(
        engine, name="fail_hold_delete", event="DELETE",
        body="RAISE EXCEPTION 'injected hold-delete failure';")
    try:
        identity_path, policy_path = sources
        service = admission(engine)
        outcome = await service.apply(identity_path, policy_path)
        assert outcome.status is OutcomeStatus.UNAVAILABLE
        store = SqlBindingStore(engine)
        journal = await store.operation(
            plan(identity_path, policy_path).import_id)
        assert journal is not None
        assert journal.state is OperationJournalState.INCOMPLETE
        assert all([
            await store.identity_held(pid)
            for pid in ("miles", "ada", "listed_only")
        ])
    finally:
        await _drop_trigger(engine, "fail_hold_delete")
    outcome = await service.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    journal = await store.operation(
        plan(identity_path, policy_path).import_id)
    assert journal is not None
    assert journal.state is OperationJournalState.COMPLETE
    assert not any([
        await store.identity_held(pid)
        for pid in ("miles", "ada", "listed_only")
    ])
