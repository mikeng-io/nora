"""The initial file-to-managed admission — plan refusal, the claim probe,
journaled resume and the fail-closed read-back, on the managed fakes."""

import hashlib
import uuid
from pathlib import Path
from types import MappingProxyType

import pytest

from nora.domain.managed import (
    CheckResult,
    ManagedPerson,
    OperationJournalState,
    OperationRecord,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
    allowed,
    complete,
    room_scope_id,
    unavailable,
)
from nora.domain.managed_ports import ScopedRelation
from nora.domain.management import MANAGEMENT_PERMITS, Scope, ScopeKind
from nora.infra.ory.keto import RelationTuple
from nora.people.adapters.file_backend import FileTopologySource
from nora.people.adapters.keto_authority import KetoAuthorityBackend
from nora.people.app.managed import ManagedServices
from nora.people.app.managed_admission import (
    ADMISSION_SOURCE,
    IMPORT_KIND,
    AdmissionFacts,
    FileAdmission,
    _prepare,
    plan,
)
from nora.persons import load_persons
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
persons = ["miles", "ada", "listed_only"]

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


async def test_admission_refuses_dynamic_membership_before_any_write(tmp_path, graph):
    store, backend, registry, admission = graph
    dynamic = POLICY.replace(
        'persons = ["miles", "ada", "listed_only"]', 'persons = "*"',
    )
    identity, policy = _sources(tmp_path, policy=dynamic)
    assert any("unsupported-input:dynamic-membership" in reason
               for reason in plan(identity, policy).refused)
    result = await admission.apply(identity, policy)
    assert result.status is OutcomeStatus.DENIED
    assert "unsupported-input:dynamic-membership" in result.detail
    assert not store.people and not store.operations and not backend.tuples
    assert not registry.identities
    policy.write_text(POLICY)
    assert (await admission.apply(identity, policy)).status is OutcomeStatus.COMPLETE
    assert store.people and store.operations and backend.tuples


def test_admission_hashes_the_same_source_bytes_the_loaders_parse(tmp_path):
    identity_path, policy_path = _sources(tmp_path)
    changed = IDENTITY.replace('name = "Miles"', 'name = "Generated Changed"')

    class ChangingPath(type(tmp_path)):
        def read_bytes(self):
            self.write_text(changed)
            return super().read_bytes()

    prepared = _prepare(ChangingPath(identity_path), policy_path)
    assert not isinstance(prepared, Outcome)
    source = Path(identity_path).read_bytes()
    assert prepared.snapshot["identity_sha256"] == hashlib.sha256(source).hexdigest()
    expected = "Generated Changed" if source.decode() == changed else "Miles"
    assert prepared.people["miles"].display_name == expected


async def test_admission_retains_authored_sources_for_faithful_export(tmp_path, graph):
    store, _, _, admission = graph
    identity_path, policy_path = _sources(tmp_path)
    import_id = plan(identity_path, policy_path).import_id
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE
    identity_path.write_text('changed after import')
    policy_path.write_text('changed after import')
    record = await store.operation(import_id)
    assert record is not None
    assert "sources" in record.snapshot
    assert record.snapshot["sources"] == {"identity_toml": IDENTITY, "policy_toml": POLICY}


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


async def test_apply_preserves_the_file_predicates_retired_id_semantics(
    tmp_path, graph,
):
    """A room listing ONLY a retired id denies the canonical person in file
    mode — `is_allowed` checks the canonical sender against the AUTHORED
    list, so the import must not canonicalize that list into a grant."""
    _, backend, _, admission = graph
    retired_only = POLICY.replace(
        'persons = ["miles"]', 'persons = ["miles_old"]')
    identity_path, policy_path = _sources(tmp_path, policy=retired_only)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    room_a = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
    room_b = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-b"))
    for checked in ("miles", "miles_old"):
        for permit in ("speak", "view"):
            verdict = await backend.check(
                scope=room_a, person_id=checked, permit=permit)
            assert verdict.verdict is Verdict.DENIED
    # Positive control: the authored-canonical room-b grant still lands.
    allowed = await backend.check(scope=room_b, person_id="miles", permit="speak")
    assert allowed.verdict is Verdict.ALLOWED


async def test_resulting_verdicts_equal_the_file_predicate_id_by_id(
    tmp_path, graph,
):
    """Every checked id — canonical, retired, listed-only, unmapped — gets
    the managed answer `is_allowed` gives the file, in every room."""
    store, backend, _, admission = graph
    retired_mixed = POLICY.replace(
        'persons = ["miles"]', 'persons = ["miles_old", "miles"]')
    identity_path, policy_path = _sources(tmp_path, policy=retired_mixed)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    persons = load_persons(identity_path)
    policy = load_policy(policy_path)
    for convo in policy.rooms():
        for cid in convo.ids:
            scope = Scope(ScopeKind.ROOM, room_scope_id(convo.platform, cid))
            for checked in ("miles", "miles_old", "ada", "listed_only", "unmapped"):
                expected = policy.is_allowed(
                    platform=convo.platform, conversation_id=cid,
                    person_id=persons.subject_id_for(checked))
                canonical = await store.canonical_person(checked) or checked
                for permit in ("speak", "view"):
                    verdict = await backend.check(
                        scope=scope, person_id=canonical, permit=permit)
                    assert (verdict.verdict is Verdict.ALLOWED) == expected, (
                        checked, scope, permit)
                for permit in MANAGEMENT_PERMITS:
                    verdict = await backend.check(
                        scope=scope, person_id=canonical, permit=permit)
                    assert verdict.verdict is Verdict.DENIED, (
                        checked, scope, permit)


async def test_apply_publishes_the_intended_containment_and_nothing_else(
    tmp_path, graph,
):
    """The complete typed read on a declared room shows this import's member
    plus its authored parent link — the same proof the read-back runs."""
    _, backend, _, admission = graph
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    room_a = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
    unit = Scope(ScopeKind.UNIT, "family")
    assert await backend.scope_relations_on(scope=room_a) == frozenset({
        ScopedRelation("member", "miles"),
        ScopedRelation("unit", unit),
    })
    assert await backend.scope_relations_on(scope=unit) == frozenset({
        ScopedRelation("workspace", Scope(ScopeKind.WORKSPACE, "home")),
    })


async def test_apply_refuses_foreign_subject_set_containment_before_writes(
    tmp_path, graph,
):
    """A parent link the import does not own is invisible to `relations_on`'s
    person-only view — the complete read refuses it before any journal,
    person, hold or Kratos/Keto write, on assess and apply alike."""
    store, backend, registry, admission = graph
    room_a = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
    await backend.publish_containment(
        scope=room_a, parent=Scope(ScopeKind.UNIT, "generated-foreign-unit"))
    identity_path, policy_path = _sources(tmp_path)
    assert (await admission.assess(identity_path, policy_path)).detail == (
        "foreign-authority-state")
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "foreign-authority-state"
    assert not store.operations and not store.people
    assert not registry.identities and not store.identity_holds


async def test_apply_reports_unavailable_for_a_subject_shape_it_cannot_name(
    tmp_path, graph,
):
    """A row whose subject names a namespace outside the scope vocabulary is
    unrepresentable — unavailable before any write, never silently skipped."""
    store, backend, registry, admission = graph
    room_a = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
    backend.tuples.add(
        ("Room", room_a.id, "member", ("subjectset", "Foreign", "x")))
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert not store.operations and not store.people and not registry.identities


class _StubKeto:
    """A `KetoClient` stub answering `tuples_on` from canned rows — the
    adapter's typed-subject refusal paths without a live service."""

    def __init__(self, rows) -> None:
        self._rows = rows

    async def tuples_on(self, *, namespace, object_id):
        return self._rows


async def test_the_adapter_refuses_a_tuple_whose_subject_it_cannot_name():
    """A subject-set naming a namespace outside the scope vocabulary, and a
    row with no subject at all, are `unavailable` — never silently dropped."""
    room = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
    foreign = RelationTuple(
        namespace="Room", object=room.id, relation="member",
        subject_set=("Foreign", "x"))
    subjectless = RelationTuple(
        namespace="Room", object=room.id, relation="member")
    for rows in ((foreign,), (subjectless,)):
        backend = KetoAuthorityBackend(_StubKeto(list(rows)))
        result = await backend.scope_relations_on(scope=room)
        assert isinstance(result, CheckResult)
        assert result.verdict is Verdict.UNAVAILABLE


async def test_apply_reports_unavailable_when_the_authority_read_cannot_answer(
    tmp_path, graph,
):
    """An outage on the empty-or-owned proof is unavailable before the
    journal exists — nothing is claimed and nothing is written."""
    store, backend, registry, admission = graph
    backend.down = True
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert not store.operations and not store.people and not registry.identities


class _DropContainment(FakeAuthorityBackend):
    """A backend whose containment publish silently never lands — the member
    rows agree but the intended parent link is missing."""

    async def publish_containment(self, *, scope, parent):
        return complete()


async def test_readback_fails_closed_on_a_missing_containment_link(
    tmp_path, graph,
):
    """Person-only `relations_on` cannot see containment at all; the complete
    read-back catches the missing parent row instead of passing the import."""
    store, _, registry, _ = graph
    backend = _DropContainment()
    admission = FileAdmission(
        bindings=store, backend=backend, registry=registry, probe=_Probe(store),
    )
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "read-back-mismatch"
    journal = next(iter(store.operations.values()))
    assert journal.state is OperationJournalState.INCOMPLETE


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("scope", [
    Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a")),
    Scope(ScopeKind.UNIT, "family"),
    Scope(ScopeKind.WORKSPACE, "home"),
])
async def test_foreign_containment_on_every_scope_refuses_fresh_and_resume(
    tmp_path, graph, scope, resume,
):
    store, backend, registry, admission = graph
    identity, policy = _sources(tmp_path)
    prepared = _prepare(identity, policy)
    assert not isinstance(prepared, Outcome)
    if resume:
        await store.record_operation(OperationRecord(
            operation_id=prepared.import_id, kind=IMPORT_KIND,
            state=OperationJournalState.INCOMPLETE,
            steps=MappingProxyType({}), snapshot=prepared.snapshot,
        ))
    before = dict(store.operations)
    namespace = {ScopeKind.ROOM: "Room", ScopeKind.UNIT: "Unit", ScopeKind.WORKSPACE: "Workspace"}
    backend.tuples.add((namespace[scope.kind], scope.id, "foreign", (
        "subjectset", "Workspace", "generated-foreign-workspace")))
    before_tuples = set(backend.tuples)
    result = await admission.apply(identity, policy)
    assert result.status is OutcomeStatus.DENIED
    assert result.detail == "foreign-authority-state"
    assert store.operations == before
    assert not store.people and not store.accounts and not store.identity_holds
    assert not registry.identities and backend.tuples == before_tuples
    backend.tuples = set()
    result = await admission.apply(identity, policy)
    assert result.status is OutcomeStatus.COMPLETE, result.detail
    assert store.people and registry.identities


class _OvergrantOutsideFile(FakeAuthorityBackend):
    def __init__(self, leak):
        super().__init__()
        self.leak = leak

    async def check(self, *, scope, person_id, permit):
        if permit == "view" and (
            (self.leak == "unknown" and person_id.endswith(":unknown"))
            or (self.leak == "ancestor" and scope.kind is ScopeKind.UNIT)
        ):
            return allowed()
        return await super().check(scope=scope, person_id=person_id, permit=permit)


@pytest.mark.parametrize("leak", ["unknown", "ancestor"])
async def test_readback_refuses_access_outside_the_file_predicate(tmp_path, graph, leak):
    store, _, registry, _ = graph
    backend = _OvergrantOutsideFile(leak)
    admission = FileAdmission(
        bindings=store, backend=backend, registry=registry, probe=_Probe(store))
    identity, policy = _sources(tmp_path)
    outcome = await admission.apply(identity, policy)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "read-back-mismatch"
    assert next(iter(store.operations.values())).state is OperationJournalState.INCOMPLETE
