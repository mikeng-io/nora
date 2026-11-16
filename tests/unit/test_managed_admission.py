"""The initial file-to-managed admission — plan refusal, the claim probe,
journaled resume and the fail-closed read-back, on the managed fakes."""

import uuid
from types import MappingProxyType

import pytest

from nora.domain.managed import (
    ManagedPerson,
    OperationJournalState,
    OperationRecord,
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
    complete,
    room_scope_id,
    unavailable,
)
from nora.domain.management import Scope, ScopeKind
from nora.people.adapters.file_backend import FileTopologySource
from nora.people.app.managed import ManagedServices
from nora.people.app.managed_admission import (
    ADMISSION_SOURCE,
    IMPORT_KIND,
    AdmissionFacts,
    FileAdmission,
    plan,
)
from nora.policy import load_policy
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
)

IDENTITY = """\
[persons.miles]
name = "Miles"
aliases = ["generated-nick"]
former_person_ids = ["miles_old"]
social = false

[[persons.miles.signal]]
phone = "+85200000001"
uuid = "{signal_uuid}"
label = "personal"

[[persons.miles.signal]]
phone = "+85200000002"
label = "business"

[[persons.miles.telegram]]
user_id = "800001"
username = "miles_tg"

[persons.ada]
name = "Ada"

[[persons.ada.signal]]
phone = "+85200000003"
""".format(signal_uuid=str(uuid.uuid5(uuid.NAMESPACE_DNS, "nora-test-signal")))

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
persons = "*"

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


def _sources(tmp_path, identity=IDENTITY, policy=POLICY):
    identity_path = tmp_path / "identity.toml"
    policy_path = tmp_path / "policy.toml"
    identity_path.write_text(identity)
    policy_path.write_text(policy)
    return identity_path, policy_path


class _Probe:
    """The AdmissionProbe seam over `FakeBindingStore`'s internals — including
    the rows the port reads hide (holds, journals, revoked, suspended)."""

    def __init__(self, store: FakeBindingStore) -> None:
        self._store = store

    async def facts(self) -> AdmissionFacts:
        store = self._store
        return AdmissionFacts(
            persons=frozenset(
                (p.person_id, p.lifecycle.value, p.kratos_identity_id is not None)
                for p in store.people.values()
            ),
            accounts=frozenset(
                (a.account_id, a.person_id, a.state.value)
                for a in store.accounts.values()
            ),
            identifiers=frozenset(
                (i.platform, i.kind, i.value) for i in store.owners
            ),
            aliases=frozenset(store.aliases.items()),
            memberships=frozenset(
                (pid, scope.kind.value, scope.id, m.state.value)
                for (pid, scope), m in store.memberships.items()
            ),
            supports=frozenset(
                (pid, scope.kind.value, scope.id, source)
                for (pid, scope), sources in store.supports.items()
                for source in sources
            ),
            operations=frozenset(
                (r.operation_id, r.kind, r.state.value)
                for r in store.operations.values()
            ),
            holds=frozenset(store.identity_holds),
            blocks=len(store.blocks),
            link_rows=0,
        )

    async def insert_person(self, person):
        self._store.people.setdefault(person.person_id, person)
        return complete()


@pytest.fixture
def graph():
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    registry = FakeIdentityRegistry()
    return store, backend, registry, FileAdmission(
        bindings=store, backend=backend, registry=registry, probe=_Probe(store),
    )


def test_plan_counts_the_exact_effect(tmp_path):
    identity_path, policy_path = _sources(tmp_path)
    result = plan(identity_path, policy_path)
    assert not result.refused
    assert result.import_id.startswith("file-admission:")
    assert dict(result.counts) == {
        "people": 3, "accounts": 4, "identifiers": 5, "aliases": 1,
        "memberships": 6, "room_scopes": 3, "containment": 5,
    }
    again = plan(identity_path, policy_path)
    assert again.import_id == result.import_id


def test_plan_refuses_a_guild_conversation(tmp_path):
    identity_path, policy_path = _sources(
        tmp_path,
        policy=POLICY + '\n[[conversations]]\nplatform = "discord"\n'
        'guild = "100000000000000203"\nunit = "family"\n'
        'label = "Guild"\npersons = "*"\n',
    )
    result = plan(identity_path, policy_path)
    assert any(r.startswith("unsupported-input:guild") for r in result.refused)


def test_plan_refuses_a_dm_admission_list(tmp_path):
    identity_path, policy_path = _sources(
        tmp_path, policy=POLICY + '\n[dm]\npersons = ["miles"]\n',
    )
    result = plan(identity_path, policy_path)
    assert any(r.startswith("unsupported-input:dm-admission") for r in result.refused)


def test_plan_refuses_an_invalid_identity_file(tmp_path):
    """The same matchable identifier claimed by two people is ambiguous
    ownership — the loader refuses before any import exists."""
    identity_path, policy_path = _sources(
        tmp_path,
        identity=IDENTITY + '\n[persons.eve]\nname = "Eve"\n'
        '[[persons.eve.signal]]\nphone = "+85200000001"\n',
    )
    result = plan(identity_path, policy_path)
    assert any(r.startswith("invalid-identity-file") for r in result.refused)


async def test_apply_imports_people_accounts_aliases_and_grants(tmp_path, graph):
    store, backend, registry, admission = graph
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail

    # Every typed identifier resolves to the exact intended account/person.
    bound = await store.account_for("signal", "phone", "+85200000001")
    assert bound is not None and bound.person_id == "miles" and bound.label == "personal"
    second = await store.account_for("signal", "phone", "+85200000002")
    assert second is not None and second.person_id == "miles" and second.label == "business"
    assert len(await store.accounts_for("miles")) == 3
    assert {i.kind for i in bound.identifiers} == {"phone", "uuid"}
    # Former-person alias resolves canonically; the listed-only person exists.
    assert await store.canonical_person("miles_old") == "miles"
    only = await store.person("listed_only")
    assert only is not None and not await store.accounts_for("listed_only")
    # Kratos traits carry descriptive state — social opt-out preserved.
    outcome, traits = await registry.identity_traits(external_id="miles")
    assert traits == {"display_name": "Miles", "social": False}
    # The verdict matrix equals the file predicate: miles in room-a but not
    # room-c (sibling unit), everyone in the `*` room, listed-only granted.
    room_a = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
    room_b = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-b"))
    room_c = Scope(ScopeKind.ROOM, room_scope_id("discord", "generated-g1/c1"))
    expected = {
        ("miles", room_a): True, ("miles", room_b): True, ("miles", room_c): False,
        ("ada", room_a): False, ("ada", room_b): True, ("ada", room_c): True,
        ("listed_only", room_a): False, ("listed_only", room_b): True,
        ("listed_only", room_c): True,
    }
    for (person_id, scope), want in expected.items():
        for permit in ("speak", "view"):
            verdict = await backend.check(scope=scope, person_id=person_id, permit=permit)
            assert (verdict.verdict is Verdict.ALLOWED) == want, (person_id, scope, permit)
    # Provenance + lifecycle: admission source, holds released, journal closed.
    members = {m.scope: m for m in await store.memberships_for("miles")}
    assert members[room_a].sources == {ADMISSION_SOURCE}
    assert not store.identity_holds
    journal = next(iter(store.operations.values()))
    assert journal.state is OperationJournalState.COMPLETE
    assert journal.outcome == "complete"


async def test_apply_refuses_a_target_that_is_not_empty(tmp_path, graph):
    """Managed state the import does not own — including a SUSPENDED person the
    active-only `persons()` roster would hide — refuses the claim."""
    store, _, _, admission = graph
    foreign = ManagedPerson(person_id="foreign", lifecycle=PersonLifecycle.SUSPENDED)
    store.people["foreign"] = foreign
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "foreign-target-state"


async def test_apply_refuses_a_foreign_journal(tmp_path, graph):
    store, _, _, admission = graph
    store.operations["other-op"] = OperationRecord(
        "other-op", "grant_access", OperationJournalState.COMPLETE,
        MappingProxyType({}), snapshot=MappingProxyType({}),
    )
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "foreign-journal"


async def test_apply_refuses_existing_target_scope_authority(tmp_path, graph):
    """Authority rows already on a declared room are target state the import
    does not own — refused before any write or journal."""
    store, backend, _, admission = graph
    room_a = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
    await backend.put_relation(scope=room_a, relation="member", person_id="foreign")
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "foreign-authority-state"
    assert not store.operations


async def test_apply_refuses_a_changed_source_on_retry(tmp_path, graph):
    _, _, _, admission = graph
    identity_path, policy_path = _sources(tmp_path)
    first = await admission.apply(identity_path, policy_path)
    assert first.status is OutcomeStatus.COMPLETE
    identity_path.write_text(IDENTITY + '\n[persons.eve]\nname = "Eve"\n'
                             '[[persons.eve.signal]]\nphone = "+85200000009"\n')
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "different-import"


async def test_apply_refuses_a_kratos_correlation_on_a_fresh_claim(tmp_path, graph):
    store, _, registry, admission = graph
    registry.identities["miles"] = {"id": "kratos-foreign", "state": "active", "traits": {}}
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "kratos-correlation-present"
    assert not registry.identities.get("ada")
    assert not store.operations


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


async def test_interrupted_import_resumes_without_duplicate_effects(tmp_path, graph):
    """A backend outage mid-run leaves an incomplete journal; a fresh service
    over the same stores finishes the SAME import — no new people, accounts
    or grants."""
    store, _, registry, _ = graph
    backend = _NoWrites()
    admission = FileAdmission(
        bindings=store, backend=backend, registry=registry, probe=_Probe(store),
    )
    identity_path, policy_path = _sources(tmp_path)
    first = await admission.apply(identity_path, policy_path)
    assert first.status is OutcomeStatus.UNAVAILABLE
    journal = next(iter(store.operations.values()))
    assert journal.kind == IMPORT_KIND and journal.state is OperationJournalState.INCOMPLETE
    assert all([
        await store.identity_held(pid) for pid in ("miles", "ada", "listed_only")
    ])
    people_before = len(store.people)
    backend.writes_ok = True
    restarted = FileAdmission(
        bindings=store, backend=backend, registry=registry, probe=_Probe(store),
    )
    outcome = await restarted.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    assert len(store.people) == people_before
    assert len(store.accounts) == 4
    assert not store.identity_holds
    replay = await restarted.apply(identity_path, policy_path)
    assert replay.status is OutcomeStatus.COMPLETE
    assert len(store.accounts) == 4


async def test_dependent_access_is_unavailable_until_publication_completes(
    tmp_path, graph,
):
    """While the journal is incomplete the holds answer `unavailable` for
    dependent managed access — never a partial grant."""
    store, _, registry, _ = graph
    backend = _NoWrites()
    admission = FileAdmission(
        bindings=store, backend=backend, registry=registry, probe=_Probe(store),
    )
    identity_path, policy_path = _sources(tmp_path)
    await admission.apply(identity_path, policy_path)
    policy = load_policy(policy_path)
    services = ManagedServices(
        store, backend, FileTopologySource(policy), registry, FakeLinkRequests(),
    )
    held = await services.access.check(
        scope=Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a")),
        person_id="miles",
        permit="speak",
    )
    assert held.verdict is Verdict.UNAVAILABLE


class _DropOne(FakeAuthorityBackend):
    """A backend whose first `member` write silently never lands — the
    published authority then disagrees with the file predicate."""

    def __init__(self) -> None:
        super().__init__()
        self.skipped = False

    async def put_relation(self, *, scope, relation, person_id):
        if relation == "member" and person_id == "ada" and not self.skipped:
            self.skipped = True
            return complete()
        return await super().put_relation(
            scope=scope, relation=relation, person_id=person_id)


async def test_readback_fails_closed_on_a_mismatched_verdict(tmp_path, graph):
    """When the published authority disagrees with the file predicate the
    import reports unavailable and stays incomplete — never complete."""
    store, _, registry, _ = graph
    backend = _DropOne()
    admission = FileAdmission(
        bindings=store, backend=backend, registry=registry, probe=_Probe(store),
    )
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "read-back-mismatch"
    journal = next(iter(store.operations.values()))
    assert journal.state is OperationJournalState.INCOMPLETE
