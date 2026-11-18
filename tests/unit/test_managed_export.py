"""The managed-to-file export — supported and refused shapes, stable
references, generated-file validation and the no-widening comparison, on
the managed fakes (`nora/testing/managed.py`, whose authority fake runs the
committed OPL's evaluator)."""

import json
import tomllib
import uuid
from pathlib import Path
from types import MappingProxyType

import pytest

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    AuthorityBlock,
    ManagedAccount,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    OperationJournalState,
    OperationRecord,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    complete,
    denied,
    room_scope_id,
    unavailable,
)
from nora.domain.management import Scope, ScopeKind
from nora.people.adapters.file_backend import FileTopologySource
from nora.people.app.managed import ManagedServices
from nora.people.app.managed_export import ManagedExport
from nora.people.app.managed_export_prepare import (
    ExportInventory,
    IdentitySnapshot,
    InventoryAccount,
    InventoryBlock,
    InventoryIdentifier,
    InventoryMembership,
    InventoryOperation,
    InventoryPerson,
    InventoryScopedName,
)
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
)

SIGNAL_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "nora-export-test"))

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
persons = ["ada"]

[units.family]
workspace = "home"

[units.work]
workspace = "work"
"""

OPEN_POLICY = """\
default = "deny"

[[conversations]]
platform = "signal"
id = "generated-open"
label = "Open"
persons = "*"
"""

ROOM_A = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
ROOM_B = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-b"))
OPEN_ROOM = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-open"))


def _policy_file(tmp_path, text=POLICY):
    path = tmp_path / "policy.toml"
    path.write_text(text)
    return path


class _Probe:
    """The ExportProbe seam over `FakeBindingStore`'s internals — including
    the rows the port reads hide (holds, journals, revoked, suspended) —
    plus the read-only correlated-Kratos snapshot the export verifies."""

    def __init__(self, store: FakeBindingStore,
                 registry: FakeIdentityRegistry | None = None,
                 *, links=(), archive=None) -> None:
        self._store = store
        self._registry = registry
        self._links = links
        self._archive = archive

    async def inventory(self) -> ExportInventory:
        store = self._store
        return ExportInventory(
            persons=tuple(
                InventoryPerson(
                    p.person_id, p.lifecycle.value, p.kratos_identity_id,
                    p.display_name)
                for p in store.people.values()
            ),
            accounts=tuple(
                InventoryAccount(
                    a.account_id, a.person_id, a.platform, a.state.value,
                    a.label)
                for a in store.accounts.values()
            ),
            identifiers=tuple(
                InventoryIdentifier(i.platform, i.kind, i.value, account_id)
                for i, account_id in store.owners.items()
            ),
            aliases=tuple(store.aliases.items()),
            memberships=tuple(
                InventoryMembership(
                    pid, scope, m.state.value,
                    frozenset(store.supports.get((pid, scope), ())))
                for (pid, scope), m in store.memberships.items()
            ),
            operations=tuple(
                InventoryOperation(r.operation_id, r.kind, r.state.value, "")
                for r in store.operations.values()
            ),
            holds=tuple(store.identity_holds),
            blocks=tuple(
                InventoryBlock(b.person_id, b.scope, b.reason)
                for b in store.blocks.values()
            ),
            link_states=self._links,
            scoped_names=tuple(
                InventoryScopedName(pid, scope, nickname)
                for (pid, scope), (nickname, _op) in store.scoped_names.items()
            ),
            archive=self._archive,
        )

    async def identity_snapshot(
        self, identity_id: str
    ) -> tuple[Outcome, IdentitySnapshot | None]:
        """The correlated Kratos record looked up BY identity id — the same
        by-id proof `SqlExportProbe` takes from `KratosClient.get_identity`."""
        registry = self._registry
        if registry is None or registry.down:
            return unavailable("no-identity-reader"), None
        for external_id, ident in registry.identities.items():
            if ident["id"] == identity_id:
                return complete(), IdentitySnapshot(
                    identity_id=identity_id,
                    external_id=external_id,
                    state=ident["state"],
                    traits=dict(ident["traits"]),
                )
        return denied("absent"), None


def _person(store, registry, person_id, *, name="", social=True,
            suspended=False, kratos=None):
    store.people[person_id] = ManagedPerson(
        person_id=person_id,
        lifecycle=PersonLifecycle.SUSPENDED if suspended else PersonLifecycle.ACTIVE,
        kratos_identity_id=kratos if kratos is not None else (
            None if suspended else f"kid-{person_id}"),
        display_name=name)
    if not suspended:
        registry.identities[person_id] = {
            "id": f"kid-{person_id}", "state": "active",
            "traits": {"display_name": name, "social": social},
        }


_ACCOUNT_COUNTER = 0


def _account(store, person_id, platform, identifiers, *, label=None,
             revoked=False):
    global _ACCOUNT_COUNTER
    _ACCOUNT_COUNTER += 1
    account = ManagedAccount(
        account_id=f"00000000-0000-0000-0000-{_ACCOUNT_COUNTER:012d}",
        person_id=person_id, platform=platform,
        identifiers=frozenset(
            Identifier(platform=platform, kind=kind, value=value)
            for kind, value in identifiers),
        state=AccountState.REVOKED if revoked else AccountState.BOUND,
        label=label)
    store.accounts[account.account_id] = account
    for ident in account.identifiers:
        store.owners[ident] = account.account_id
    return account


def _member(store, backend, person_id, scope):
    store.memberships[(person_id, scope)] = ManagedMembership(
        person_id=person_id, scope=scope, state=MembershipState.MEMBER,
        source="registration", sources=frozenset({"registration"}))
    store.supports.setdefault((person_id, scope), set()).add("registration")
    backend.tuples.add(("Room", scope.id, "member", person_id))


def _grant(backend, person_id, scope, permit):
    backend.tuples.add(("Room", scope.id, f"grant_{permit}", person_id))


def _deny(backend, person_id, scope, permit):
    backend.tuples.add(("Room", scope.id, f"deny_{permit}", person_id))


def _export_for(store, backend, registry, policy_path, *, probe=None):
    topology = FileTopologySource(load_policy(policy_path))
    services = ManagedServices(
        store, backend, topology, registry, FakeLinkRequests())
    return ManagedExport(
        bindings=store, backend=backend, topology=topology,
        access=services.access,
        probe=probe or _Probe(store, registry))


@pytest.fixture
def graph(tmp_path):
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    registry = FakeIdentityRegistry()
    policy_path = _policy_file(tmp_path)
    export = _export_for(store, backend, registry, policy_path)
    return store, backend, registry, policy_path, export


def _seed(graph):
    """miles (signal+telegram, one retired id) in room A; ada (signal) in room B."""
    store, backend, registry, _, _ = graph
    _person(store, registry, "miles", name="Miles", social=False)
    _account(store, "miles", "signal",
             [("phone", "+85200000001"), ("uuid", SIGNAL_UUID)],
             label="personal")
    _account(store, "miles", "telegram",
             [("user_id", "800001")], label="main")
    store.aliases["miles_old"] = "miles"
    _person(store, registry, "ada", name="Ada")
    _account(store, "ada", "signal", [("phone", "+85200000003")])
    _member(store, backend, "miles", ROOM_A)
    _member(store, backend, "ada", ROOM_B)
    return store, backend, registry


def _write_bundle(directory, bundle):
    out = Path(directory) / "generated"
    out.mkdir(exist_ok=True)
    (out / "identity.toml").write_text(bundle.identity_toml)
    (out / "policy.toml").write_text(bundle.policy_toml)
    return out


def _resolve(persons, platform, value):
    account = persons.account_for(platform, value)
    return account.person_id if account is not None else None


async def test_export_rebuilds_a_faithful_bundle(graph):
    """The happy path: the generated pair loads through the real loaders,
    every verdict agrees, and the bundle keeps stable references."""
    _seed(graph)
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.COMPLETE, report.refusals
    bundle = report.bundle
    assert bundle is not None and report.digest.startswith("export:")

    out = _write_bundle(Path(policy_path).parent, bundle)
    persons = load_persons(out / "identity.toml")
    generated_policy = load_policy(out / "policy.toml")

    miles = persons.by_person["miles"]
    assert miles.name == "Miles"
    assert persons.social_off("miles")          # the social opt-out kept
    assert not persons.social_off("ada")
    # The retired id folds to the survivor exactly like managed aliases.
    assert persons.subject_id_for("miles_old") == "miles"
    # Exact account grouping and the operator-written labels.
    signal_accounts = persons.accounts_for("miles", "signal")
    assert len(signal_accounts) == 1
    assert signal_accounts[0].label == "personal"
    assert _resolve(persons, "signal", "+85200000001") == "miles"
    assert _resolve(persons, "telegram", "800001") == "miles"
    # Room lists derive from managed verdicts — nothing wider, nothing lost.
    room_a = generated_policy.conversation("signal", "generated-room-a")
    room_b = generated_policy.conversation("signal", "generated-room-b")
    assert room_a.persons == frozenset({"miles"})
    assert room_b.persons == frozenset({"ada"})
    # Topology carries over — units, labels, default.
    assert room_a.label == "Room A" and room_a.unit == "family"
    # The manifest records provenance privately.
    assert '"identity_sha256"' in bundle.manifest_json
    assert '"miles_old"' in bundle.manifest_json


async def test_plan_validates_without_publishing(graph):
    _seed(graph)
    *_, policy_path, export = graph
    report = await export.assess(policy_path)
    assert report.outcome.status is OutcomeStatus.COMPLETE, report.refusals
    assert report.bundle is None and report.digest


async def test_export_refuses_incomplete_publication(graph):
    """An unfinished journal, a hold and an authority block are all named —
    the export never certifies a partially published state."""
    store, *_ = _seed(graph)
    store.operations["op-1"] = OperationRecord(
        "op-1", "grant_access", OperationJournalState.INCOMPLETE,
        MappingProxyType({}), snapshot=MappingProxyType({}))
    store.identity_holds.add(("ada", "op-1"))
    store.blocks[("miles", ROOM_A)] = AuthorityBlock(
        person_id="miles", scope=ROOM_A, reason="blocked",
        operation_id="op-1")
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("incomplete-publication:operation")
               for r in report.refusals)
    assert any(r.code.startswith("incomplete-publication:holds")
               for r in report.refusals)
    assert any(r.code.startswith("incomplete-publication:authority-blocks")
               for r in report.refusals)


async def test_export_refuses_scoped_names_as_unsupported_state(graph):
    """Scoped nicknames have no TOML representation — the export names a
    fixed bounded refusal rather than silently dropping a person's name."""
    store, *_ = _seed(graph)
    store.scoped_names[(
        "miles", ROOM_A)] = ("Generated Room Name", "op-1")
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    refusal = next(
        r for r in report.refusals
        if r.code == "unsupported-state:scoped-names")
    assert refusal.detail == "1-names"


async def test_export_refuses_a_pending_link(graph):
    _seed(graph)
    store, backend, registry, policy_path, _ = graph
    export = _export_for(store, backend, registry, policy_path,
                         probe=_Probe(store, registry,
                                      links=[("req-1", "pending")]))
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("incomplete-publication:link")
               for r in report.refusals)


async def test_export_refuses_conflicting_ownership(graph):
    """One (platform, value) on two bound accounts is ambiguous in managed's
    own flat resolve — the export refuses instead of picking an owner."""
    store, *_ = _seed(graph)
    _account(store, "ada", "signal", [("uuid", "+85200000001")])
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("conflicting-ownership") for r in report.refusals)


async def test_export_refuses_management_authority(graph):
    """Delegated administration has no file surface — a grant of
    manage_members refuses rather than silently dropping who administers."""
    _seed(graph)
    _, backend, *_ = graph
    _grant(backend, "ada", ROOM_A, "manage_members")
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("unfaithful:management-authority")
               for r in report.refusals)


async def test_export_refuses_speak_view_divergence(graph):
    """A person who may speak but not view has no file shape — refused, not
    approximated as listed or unlisted."""
    _seed(graph)
    _, backend, *_ = graph
    _deny(backend, "ada", ROOM_A, "view")
    _grant(backend, "ada", ROOM_A, "speak")
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("unfaithful:speak-view-divergence")
               for r in report.refusals)


async def test_export_refuses_an_open_room_that_denies_someone(tmp_path):
    """`persons = "*"` admits every presented id — when managed denies a
    known person there, emitting `*` would widen their access."""
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    registry = FakeIdentityRegistry()
    policy_path = _policy_file(tmp_path, OPEN_POLICY)
    export = _export_for(store, backend, registry, policy_path)
    _person(store, registry, "miles")
    _person(store, registry, "ada")
    _member(store, backend, "miles", OPEN_ROOM)   # ada has no grant
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("unfaithful:open-room-denial")
               for r in report.refusals)


async def test_export_keeps_suspended_people_out_of_every_grant(graph):
    """A suspended person keeps a bare directory entry — no accounts, no
    list membership — and their still-bound identifiers resolve nowhere."""
    store, backend, registry, *_ = _seed(graph)
    _person(store, registry, "eve", name="Eve", suspended=True)
    _account(store, "eve", "signal", [("phone", "+85200000007")])
    _member(store, backend, "eve", ROOM_A)   # even a room member tuple
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.COMPLETE, report.refusals
    out = _write_bundle(Path(policy_path).parent, report.bundle)
    persons = load_persons(out / "identity.toml")
    assert "eve" in persons.by_person             # directory continuity kept
    assert not persons.accounts_for("eve")        # but no bound surface
    assert persons.social_off("eve")              # and opted out
    assert _resolve(persons, "signal", "+85200000007") is None
    assert "+85200000007" not in report.bundle.identity_toml
    room_a = load_policy(out / "policy.toml")\
        .conversation("signal", "generated-room-a")
    assert "eve" not in room_a.persons
    # The suspended binding is not lost: the private manifest carries the
    # account, its owner and its identifiers.
    assert '"suspended_accounts"' in report.bundle.manifest_json
    assert "+85200000007" in report.bundle.manifest_json


async def test_export_never_emits_a_revoked_identifier(graph):
    """A revoked binding stays dead — its identifier resolves nowhere in
    the generated file even though its rows remain in managed."""
    store, *_ = _seed(graph)
    _account(store, "miles", "signal", [("phone", "+85200000009")],
             revoked=True)
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.COMPLETE, report.refusals
    persons = load_persons(
        _write_bundle(Path(policy_path).parent, report.bundle) / "identity.toml")
    assert _resolve(persons, "signal", "+85200000009") is None
    assert '"revoked_accounts"' in report.bundle.manifest_json


async def test_export_refuses_an_unidentifiable_account(graph):
    """A bound account with no matchable identifier is a grant no one can
    ever reach in file mode — refused, not dropped."""
    store, *_ = _seed(graph)
    ghost = ManagedAccount(
        account_id="00000000-0000-0000-0000-0000000000ee",
        person_id="miles", platform="signal",
        identifiers=frozenset({Identifier("signal", "uuid", "ghost-id")}),
        state=AccountState.BOUND)
    store.accounts[ghost.account_id] = ghost
    # ...but the identifier rows for it are gone — the account exists with
    # nothing a sender could ever present.
    store.owners.pop(Identifier("signal", "uuid", "ghost-id"), None)
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("unfaithful:unidentifiable-account")
               for r in report.refusals)


async def test_export_refuses_an_archived_binding_that_moved(graph):
    """The archived admission source is metadata, never ownership: an
    archived account now bound to a DIFFERENT person refuses rather than
    restoring the old binding."""
    store, *_ = _seed(graph)
    archive_identity = """\
[persons.miles]
name = "Miles"

[[persons.miles.signal]]
phone = "+85200000003"
username = "stale"
"""
    # +85200000003 is ada's live binding — archived under miles.
    *_, policy_path, _export = graph
    probe = _Probe(store, graph[2], archive=(
        "op-archive", archive_identity, "default = \"deny\"\n"))
    export = _export_for(store, graph[1], graph[2], policy_path, probe=probe)
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("ambiguous-ownership:archived-binding-conflict")
               for r in report.refusals)


async def test_export_refuses_competing_archived_claims(graph):
    """Two archived blocks resolving to the SAME live binding are
    ambiguous — which authored metadata applies cannot be guessed, so the
    export refuses rather than letting the last claim win silently."""
    _seed(graph)
    archive_identity = """\
[persons.ada]
name = "Ada"

[[persons.ada.signal]]
phone = "+85200000003"
username = "first-archived"

[[persons.ada.signal]]
phone = "+85200000003"
username = "second-archived"
"""
    *_, policy_path, _export = graph
    probe = _Probe(graph[0], graph[2], archive=(
        "op-archive", archive_identity, "default = \"deny\"\n"))
    export = _export_for(graph[0], graph[1], graph[2], policy_path, probe=probe)
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("ambiguous-ownership:archived-binding-conflict")
               for r in report.refusals)


async def test_export_overlays_archived_metadata_only_where_it_still_applies(
    graph,
):
    """The same archived account on the same verified binding restores
    authored metadata; a re-named person's archived block follows the
    former-person alias."""
    store, *_ = _seed(graph)
    archive_identity = f"""\
[persons.miles_old]
name = "Miles"
first_name = "Milos"
aliases = ["generated-old-nick"]

[[persons.miles_old.signal]]
phone = "+85200000001"
uuid = "{SIGNAL_UUID}"
username = "generated-handle"
"""
    probe = _Probe(store, graph[2], archive=(
        "op-archive", archive_identity, "default = \"deny\"\n"))
    *_, policy_path, export = graph
    export = _export_for(store, graph[1], graph[2], policy_path, probe=probe)
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.COMPLETE, report.refusals
    out = _write_bundle(Path(policy_path).parent, report.bundle)
    persons = load_persons(out / "identity.toml")
    miles = persons.by_person["miles"]
    assert miles.first_name == "Milos"
    assert "generated-old-nick" in miles.aliases
    emitted = tomllib.loads(report.bundle.identity_toml)
    block = emitted["persons"]["miles"]["signal"][0]
    assert block["username"] == "generated-handle"


async def test_export_reports_unavailable_when_kratos_is_down(graph):
    _seed(graph)
    *_, registry, policy_path, export = graph
    registry.down = True
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.UNAVAILABLE


async def test_export_reports_unavailable_when_keto_is_down(graph):
    _seed(graph)
    _, backend, _, policy_path, export = graph
    backend.down = True
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.UNAVAILABLE


async def test_export_refuses_an_active_person_without_kratos_correlation(
    graph,
):
    """An active person with no Kratos correlation is untrustworthy state,
    not a person with no name."""
    store, *_ = _seed(graph)
    store.people["ghost"] = ManagedPerson(
        person_id="ghost", lifecycle=PersonLifecycle.ACTIVE,
        kratos_identity_id=None, display_name="")
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("untrustworthy:uncorrelated-person")
               for r in report.refusals)


async def test_export_refuses_a_live_alias(graph):
    """An alias naming a person who is still live is ownership ambiguity,
    not a fold."""
    store, *_ = _seed(graph)
    store.aliases["ada"] = "miles"    # ada is a live person
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("ambiguous-ownership:alias-is-live")
               for r in report.refusals)


async def test_export_refuses_guild_and_dm_topology(tmp_path):
    """Topology shapes the export cannot evaluate refuse before any store
    is touched — the same two inputs the admission refuses."""
    cases = (
        ('[[conversations]]\nplatform = "discord"\n'
         'guild = "100000000000000203"\nunit = "family"\n'
         'label = "Guild"\npersons = "*"\n',
         "unsupported-input:guild"),
        ('[dm]\npersons = ["miles"]\n', "unsupported-input:dm-admission"),
    )
    for index, (suffix, want) in enumerate(cases):
        store = FakeBindingStore()
        backend = FakeAuthorityBackend()
        registry = FakeIdentityRegistry()
        target = tmp_path / f"case{index}"
        target.mkdir()
        policy_path = _policy_file(target, POLICY + suffix)
        export = _export_for(store, backend, registry, policy_path)
        report = await export.export(policy_path)
        assert report.outcome.status is OutcomeStatus.DENIED
        assert any(r.code.startswith(want) for r in report.refusals)


async def test_export_refuses_a_shared_kratos_identity(graph):
    """Two canonical ids correlated to one Kratos identity is ambiguous
    ownership — not a merge."""
    store, *_ = _seed(graph)
    store.people["ada"] = ManagedPerson(
        person_id="ada", lifecycle=PersonLifecycle.ACTIVE,
        kratos_identity_id="kid-miles", display_name="Ada")
    *_, policy_path, export = graph
    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code.startswith("ambiguous-ownership:shared-kratos-identity")
               for r in report.refusals)


async def test_export_refuses_an_open_room_even_when_everyone_is_allowed(
    tmp_path,
):
    """`persons = "*"` is never frozen and never re-emitted: file `*`
    admits identifiers managed has never registered — a widening no
    allowlist can express — and the current roster would close the room
    to senders managed admits dynamically. The whole export refuses."""
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    registry = FakeIdentityRegistry()
    policy_path = _policy_file(tmp_path, OPEN_POLICY)
    export = _export_for(store, backend, registry, policy_path)
    _person(store, registry, "miles")
    _person(store, registry, "ada")
    _member(store, backend, "miles", OPEN_ROOM)
    _member(store, backend, "ada", OPEN_ROOM)

    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code == "unfaithful:open-room-dynamic"
               for r in report.refusals)
    assert report.bundle is None


async def test_export_refuses_when_the_kratos_id_correlates_elsewhere(
    graph,
):
    """The SQL correlation must lead to THIS person's record — a live
    identity answering for another external id is a mismatch, not a fold."""
    store, *_ = _seed(graph)
    *_, registry, policy_path, export = graph
    store.people["miles"] = ManagedPerson(
        person_id="miles", lifecycle=PersonLifecycle.ACTIVE,
        kratos_identity_id=registry.identities["ada"]["id"],
        display_name="Miles")

    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code == "untrustworthy:kratos-correlation-mismatch"
               for r in report.refusals)


async def test_export_refuses_when_the_correlated_identity_is_gone(graph):
    store, *_ = _seed(graph)
    *_, policy_path, export = graph
    store.people["miles"] = ManagedPerson(
        person_id="miles", lifecycle=PersonLifecycle.ACTIVE,
        kratos_identity_id="kid-absent", display_name="Miles")

    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code == "untrustworthy:kratos-correlation-missing"
               for r in report.refusals)


async def test_export_refuses_an_inactive_record_for_an_active_person(graph):
    _seed(graph)
    *_, registry, policy_path, export = graph
    registry.identities["miles"]["state"] = "inactive"

    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code == "untrustworthy:state-disagreement"
               for r in report.refusals)


async def test_export_refuses_when_the_topology_source_changed(graph):
    """One immutable snapshot drives every phase; the source is re-read
    before a bundle may appear — a changed file refuses rather than
    publishing a stale cut. The snapshot here is self-consistent (a real
    mid-run race's is); only the file on disk moved on."""
    _seed(graph)
    *_, policy_path, export = graph
    stale = ManagedExport(
        bindings=export.bindings, backend=export.backend,
        topology=export.topology, access=export.access, probe=export.probe,
        source_snapshot=(
            load_policy(policy_path),
            policy_path.read_text() + "\n# a later edit\n"))

    report = await stale.export(policy_path)
    assert report.outcome.status is OutcomeStatus.DENIED
    assert any(r.code == "untrustworthy:source-changed"
               for r in report.refusals)
    assert report.bundle is None


async def test_export_applies_archived_metadata_on_a_verified_subset(
    graph,
):
    """Verified ownership, not identifier-set equality: the archived block
    names ONE live identifier of a two-identifier account — the same
    verified account owns both, so the authored username still applies."""
    store, *_ = _seed(graph)
    archive_identity = """\
[persons.miles]
name = "Miles"

[[persons.miles.signal]]
phone = "+85200000001"
username = "generated-handle"
"""
    *_, policy_path, export = graph
    probe = _Probe(store, graph[2], archive=(
        "op-archive", archive_identity, "default = \"deny\"\n"))
    export = _export_for(store, graph[1], graph[2], policy_path, probe=probe)

    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.COMPLETE, report.refusals
    emitted = tomllib.loads(report.bundle.identity_toml)
    signal_blocks = emitted["persons"]["miles"]["signal"]
    assert signal_blocks[0]["username"] == "generated-handle"


async def test_export_preserves_the_kratos_labels_in_the_manifest(graph):
    """Current Kratos labels have no file surface — the private manifest
    carries the actual values, not a count."""
    _seed(graph)
    *_, registry, policy_path, export = graph
    registry.identities["miles"]["traits"]["labels"] = {
        "work": "Miles W", "home": "M"}

    report = await export.export(policy_path)
    assert report.outcome.status is OutcomeStatus.COMPLETE, report.refusals
    manifest = json.loads(report.bundle.manifest_json)
    miles = next(p for p in manifest["people"] if p["person_id"] == "miles")
    assert miles["labels"] == {"home": "M", "work": "Miles W"}
    assert "Miles W" not in report.bundle.identity_toml
