"""The managed-to-file export against real PostgreSQL — the SQL-aware probe
sees the rows the port reads hide, and a bundle rebuilt from real binding
rows carries the archived authored metadata back into a valid file pair."""

import uuid
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
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    complete,
    denied,
    unavailable,
)
from nora.infra.ory import KratosIdentity
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people.adapters.file_backend import FileTopologySource
from nora.people.adapters.managed_export import SqlExportProbe
from nora.people.adapters.managed_handoff import SqlAdmissionProbe
from nora.people.app.managed import ManagedServices
from nora.people.app.managed_admission import FileAdmission
from nora.people.app.managed_export import ManagedExport
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeIdentityRegistry,
    FakeLinkRequests,
)

IDENTITY = f"""\
[persons.miles]
name = "Miles"
aliases = ["generated-nick"]
former_person_ids = ["miles_old"]
social = false

[[persons.miles.signal]]
phone = "+85200000001"
uuid = "{uuid.uuid5(uuid.NAMESPACE_DNS, 'nora-export-sql')}"
label = "personal"

[[persons.miles.telegram]]
user_id = "800001"
username = "miles_tg"

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
platform = "signal"
id = "generated-room-b"
unit = "work"
label = "Room B"
persons = ["miles", "ada"]

[[conversations]]
platform = "discord"
id = "generated-g1/c1"
unit = "family"
label = "Room C"
persons = ["ada", "listed_only"]

[units.family]
workspace = "home"

[units.work]
workspace = "work"
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


class _FakeKratos:
    """`get_identity` over the fake registry's records — the same read-only
    by-id proof `KratosClient` performs for the real probe. The fake
    registry cannot import `nora.infra` (sibling layers), so the test
    supplies this reader."""

    def __init__(self, registry: FakeIdentityRegistry) -> None:
        self._registry = registry

    async def get_identity(
        self, identity_id: str
    ) -> tuple[Outcome, KratosIdentity | None]:
        if self._registry.down:
            return unavailable("down"), None
        for external_id, ident in self._registry.identities.items():
            if ident["id"] == identity_id:
                return complete(), KratosIdentity(
                    identity_id=identity_id, external_id=external_id,
                    state=ident["state"], traits=dict(ident["traits"]))
        return denied("absent"), None


def _export(engine, backend, registry, policy):
    store = SqlBindingStore(engine)
    topology = FileTopologySource(policy)
    services = ManagedServices(
        store, backend, topology, registry, FakeLinkRequests())
    return ManagedExport(
        bindings=store, backend=backend, topology=topology,
        access=services.access,
        probe=SqlExportProbe(engine, _FakeKratos(registry)))


async def _admit(engine, sources):
    """A real admission — the binding/journal state the export reads back."""
    backend = FakeAuthorityBackend()
    registry = FakeIdentityRegistry()
    admission = FileAdmission(
        bindings=SqlBindingStore(engine), backend=backend, registry=registry,
        probe=SqlAdmissionProbe(engine))
    outcome = await admission.apply(*sources)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    return backend, registry


async def test_export_rebuilds_the_bundle_from_real_sql(engine, sources):
    """Admit through the real SQL store, export back out through the real
    probe — the generated pair loads, verdicts agree, retired ids fold,
    and archived metadata returns."""
    _identity_path, policy_path = sources
    backend, registry = await _admit(engine, sources)
    export = _export(engine, backend, registry, load_policy(policy_path))
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.COMPLETE, report.refusals
    bundle = report.bundle
    assert bundle is not None

    out = policy_path.parent / "generated"
    out.mkdir()
    (out / "identity.toml").write_text(bundle.identity_toml)
    (out / "policy.toml").write_text(bundle.policy_toml)
    persons = load_persons(out / "identity.toml")
    generated_policy = load_policy(out / "policy.toml")

    assert persons.subject_id_for("miles_old") == "miles"
    assert persons.account_for("signal", "+85200000001").person_id == "miles"
    assert persons.account_for("telegram", "800001").person_id == "miles"
    assert persons.social_off("miles")
    # The archived admission source restores authored metadata on the same
    # verified binding — the telegram username returns.
    import tomllib
    emitted = tomllib.loads(bundle.identity_toml)
    assert emitted["persons"]["miles"]["telegram"][0]["username"] == "miles_tg"
    # The listed rooms carry exactly what the managed verdicts allow.
    room_a = generated_policy.conversation("signal", "generated-room-a")
    room_b = generated_policy.conversation("signal", "generated-room-b")
    room_c = generated_policy.conversation("discord", "generated-g1/c1")
    assert room_a.persons == frozenset({"miles"})
    assert room_b.persons == frozenset({"ada", "miles"})
    assert room_c.persons == frozenset({"ada", "listed_only"})


async def test_export_sees_what_the_port_reads_hide(engine, sources):
    """A suspended person and a revoked binding disappear from the roster
    reads — the export still completes, keeps both out of every grant and
    names them in the private manifest."""
    _identity_path, policy_path = sources
    backend, registry = await _admit(engine, sources)
    store = SqlBindingStore(engine)
    await store.set_lifecycle(
        person_id="ada", lifecycle=PersonLifecycle.SUSPENDED)
    # Real suspension flips the Kratos record to inactive — the fake
    # mirrors that so the correlation check sees the same lifecycle.
    registry.identities["ada"]["state"] = "inactive"
    await store.revoke_binding(
        account=Identifier("signal", "phone", "+85200000003"))
    assert not any(p.person_id == "ada" for p in await store.persons())

    policy = load_policy(policy_path)
    export = _export(engine, backend, registry, policy)
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.COMPLETE, report.refusals
    bundle = report.bundle
    # The suspended ada and her revoked identifier grant nothing in the
    # emitted pair — denied in managed, absent from every room list and
    # unresolvable in the identity mapping.
    room_b = load_policy_from_text(bundle.policy_toml)\
        .conversation("signal", "generated-room-b")
    room_c = load_policy_from_text(bundle.policy_toml)\
        .conversation("discord", "generated-g1/c1")
    assert room_b.persons == frozenset({"miles"})
    assert room_c.persons == frozenset({"listed_only"})
    assert "+85200000003" not in bundle.identity_toml
    # ...but nothing is lost: both stay private provenance in the manifest —
    # ada is marked suspended, her revoked account's identifiers are kept.
    import json
    manifest = json.loads(bundle.manifest_json)
    ada = next(p for p in manifest["people"] if p["person_id"] == "ada")
    assert ada["suspended"] is True
    assert "+85200000003" in {
        value for account in manifest["revoked_accounts"]
        for _kind, value in account["identifiers"]
    }


def load_policy_from_text(text):
    """The emitted policy through the real loader, from its own text."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as hold:
        path = Path(hold) / "policy.toml"
        path.write_text(text)
        return load_policy(path)


async def test_export_refuses_an_incomplete_journal_on_real_sql(
    engine, sources,
):
    _identity_path, policy_path = sources
    backend, registry = await _admit(engine, sources)
    store = SqlBindingStore(engine)
    await store.record_operation(OperationRecord(
        "other-op", "grant_access", OperationJournalState.INCOMPLETE,
        MappingProxyType({}), snapshot=MappingProxyType({})))
    export = _export(
        engine, backend, registry, load_policy(policy_path))
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("incomplete-publication:operation")
               for r in report.refusals)


async def test_export_refuses_a_foreign_identifier_collision_on_real_sql(
    engine, sources,
):
    """The same (platform, value) claimed under two kinds on two accounts —
    ambiguous in managed's own flat resolve — refuses the whole export."""
    _identity_path, policy_path = sources
    backend, registry = await _admit(engine, sources)
    store = SqlBindingStore(engine)
    # A second account on ada whose uuid literal collides with miles' phone.
    await store.register(
        person=ManagedPerson(
            person_id="eve", lifecycle=PersonLifecycle.ACTIVE),
        account=ManagedAccount(
            account_id="00000000-0000-0000-0000-0000000000e1",
            person_id="eve", platform="signal",
            identifiers=frozenset(
                {Identifier("signal", "uuid", "+85200000001")}),
            state=AccountState.BOUND),
    )
    registry.identities["eve"] = {
        "id": "kid-eve", "state": "active",
        "traits": {"display_name": "Eve", "social": True},
    }
    await store.correlate_kratos(
        person_id="eve", kratos_identity_id="kid-eve")
    # The store itself permits the distinct-kind insert; managed resolve
    # answers limit-1 ambiguously — the export refuses to choose.
    export = _export(
        engine, backend, registry, load_policy(policy_path))
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("conflicting-ownership")
               for r in report.refusals)
