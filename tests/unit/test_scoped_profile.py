"""`ScopedProfileService` + `HumanManagement` — the operator's scoped
nickname (`authorization.md` «Scoped authority»). The behaviours this
slice owns:

- the same authenticated operator gate every scoped human operation
  uses — session, holds, topology and `manage_members` on the payload's
  scope — runs before any write, and every refusal is honest with no
  effects;
- the effect is a Nora-owned metadata row under (canonical person, exact
  scope) — never the global Kratos trait, an identifier, a grant or a
  membership;
- `person_ref(scope=...)` reads the nearest room→unit→workspace nickname
  then the global name — parent/sibling isolation, canonical folding,
  absent-versus-unavailable;
- the journal orders intents PER profile target: a stale replay cannot
  overwrite a newer nickname in the same scope, and neither side can
  supersede or replay-overwrite the global name or a sibling scope.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nora.domain.authentication import AccountAuthentication, SessionBinding
from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    AuthorityBlock,
    ManagedAccount,
    ManagedPerson,
    OperationJournalState,
    OutcomeStatus,
    PersonLifecycle,
    unavailable,
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
from nora.people import HumanManagement
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)

PERSON = "generated-member"
OPERATOR = "generated-operator"
OP_ACCT = Identifier("discord", "user_id", "generated-operator-account")
OP_ACCOUNT_ID = "generated-binding-operator"
ROOM = Scope(ScopeKind.ROOM, "generated-room")
SIBLING = Scope(ScopeKind.ROOM, "generated-sibling-room")
UNIT = Scope(ScopeKind.UNIT, "generated-unit")
SIBLING_UNIT = Scope(ScopeKind.UNIT, "generated-sibling-unit")
WORKSPACE = Scope(ScopeKind.WORKSPACE, "generated-workspace")
NICK = "Generated Room Nickname"
GLOBAL = "Generated Global Name"


def _topology() -> FakeTopologySource:
    return FakeTopologySource(
        rooms={ROOM.id: (UNIT.id, False), SIBLING.id: (SIBLING_UNIT.id, False)},
        units={UNIT.id: WORKSPACE.id, SIBLING_UNIT.id: WORKSPACE.id},
        workspaces={WORKSPACE.id})


def _services(
    store: FakeBindingStore | None = None,
    backend: FakeAuthorityBackend | None = None,
) -> ManagedServices:
    store = store or FakeBindingStore()
    return ManagedServices(
        store, backend or FakeAuthorityBackend(), _topology(),
        FakeIdentityRegistry(), FakeLinkRequests())


async def _person(services, person_id=PERSON, *, display_name=GLOBAL):
    store = services.bindings
    await store.register(
        person=ManagedPerson(person_id, PersonLifecycle.ACTIVE,
                             display_name=display_name),
        account=ManagedAccount(
            f"generated-binding-{person_id}", person_id, "discord",
            frozenset({Identifier(
                "discord", "user_id", f"generated-{person_id}-account")}),
            AccountState.BOUND))
    await services.registry.ensure_identity(
        external_id=person_id, traits={"display_name": display_name})


async def _operator(services, backend, scope=ROOM, relation="admin"):
    await _person(services, OPERATOR)
    await backend.put_relation(
        scope=scope, relation=relation, person_id=OPERATOR)
    # The authority graph's containment mirrors the declared topology.
    for child, parent in (
            (ROOM, UNIT), (SIBLING, SIBLING_UNIT),
            (UNIT, WORKSPACE), (SIBLING_UNIT, WORKSPACE)):
        await backend.publish_containment(scope=child, parent=parent)


async def _authentication(store, *, person_id=OPERATOR):
    record = next(
        (a for a in store.accounts.values() if a.person_id == person_id), None)
    assert record is not None
    person = await store.person(person_id)
    assert person is not None
    return AccountAuthentication(
        issuer="generated-portal", session_id="generated-session",
        account=next(iter(record.identifiers)), method="discord_oauth",
        proof_issuer="generated-client",
        authenticated_at=datetime.now(UTC), purpose="session",
        binding=SessionBinding(person_id, str(person.revision),
                               record.account_id, str(record.revision)))


def _operation(store_person_revision="0", *, op_id="generated-op-1",
               scope=ROOM, person_id=PERSON, nickname=NICK):
    return Operation(
        operation_id=op_id, kind="update_profile",
        actor=Actor(ActorKind.OPERATOR, person_id=OPERATOR, scope=scope),
        target=Target(TargetKind.PERSON, person_id=person_id),
        payload=ProfileEdit(nickname=nickname, scope=scope),
        observed_revisions={
            "identity": store_person_revision, "authority": "a-1"})


async def _apply(services, store, op, *, person_id=OPERATOR):
    authentication = await _authentication(store, person_id=person_id)
    human = HumanManagement(
        services.bindings, services.registry, services.registration,
        services.authority, services.scoped_profile)
    return await human.apply(op, authentication)


async def test_an_authorized_operator_sets_a_room_nickname_and_reads_it_back():
    services = _services()
    store = services.bindings
    await _person(services)
    await _operator(services, services.backend)
    outcome = await _apply(services, store, _operation())
    assert outcome.status is OutcomeStatus.COMPLETE

    # The nickname lives only under the room; the global name is untouched.
    ref = await services.access.person_ref(PERSON, scope=ROOM)
    assert ref is not None and ref.name == NICK
    assert (await services.access.person_ref(PERSON)).name == GLOBAL
    person = await store.person(PERSON)
    assert person is not None and person.display_name == GLOBAL
    # No ownership, membership or grant changed — metadata only.
    assert (await store.memberships_for(PERSON)) == ()
    record = await store.operation("generated-op-1")
    assert record is not None
    assert record.state is OperationJournalState.COMPLETE
    assert set(record.steps) == {"profile", "readback"}


@pytest.mark.parametrize("failure", [
    "sibling-scope", "authority-down", "unknown-scope", "unknown-person",
    "suspended-person", "identity-held", "stale-identity",
])
async def test_the_gate_refuses_honestly_before_any_effect(failure):
    services = _services()
    store = services.bindings
    await _person(services)
    await _operator(services, services.backend)
    op = _operation()
    if failure == "sibling-scope":
        op = _operation(scope=SIBLING)
    elif failure == "authority-down":
        services.backend.down = True
    elif failure == "unknown-scope":
        # Authority rows can outlive a declared scope; the operator even
        # holds manage_members there — only the topology check refuses it.
        missing = Scope(ScopeKind.ROOM, "generated-missing")
        await services.backend.put_relation(
            scope=missing, relation="admin", person_id=OPERATOR)
        op = _operation(scope=missing)
    elif failure == "unknown-person":
        op = _operation(person_id="generated-absent")
    elif failure == "suspended-person":
        await store.set_lifecycle(
            person_id=PERSON, lifecycle=PersonLifecycle.SUSPENDED)
    elif failure == "identity-held":
        await store.hold_identity(
            person_id=PERSON, operation_id="generated-publication")
    elif failure == "stale-identity":
        op = _operation("999")
    outcome = await _apply(services, store, op)
    assert outcome.status is (
        OutcomeStatus.UNAVAILABLE
        if failure in ("authority-down", "identity-held")
        else OutcomeStatus.DENIED), (failure, outcome)
    assert store.scoped_names == {}
    assert store.operations.get("generated-op-1") is None or (
        store.operations["generated-op-1"].state
        is OperationJournalState.COMPLETE)


async def test_the_service_alone_refuses_an_undeclared_scope():
    """Without the web boundary, the service still refuses a scope the
    topology does not declare — even with an authority row on it."""
    services = _services()
    store = services.bindings
    await _person(services)
    await _operator(services, services.backend)
    missing = Scope(ScopeKind.ROOM, "generated-missing")
    await services.backend.put_relation(
        scope=missing, relation="admin", person_id=OPERATOR)
    outcome = await services.scoped_profile.apply(_operation(scope=missing))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "unknown-scope"
    assert store.scoped_names == {}


async def test_a_holder_or_service_actor_never_reaches_the_scoped_path():
    services = _services()
    store = services.bindings
    await _person(services)
    # The service-level admission: only an OPERATOR actor with a scoped
    # payload owns this journal — anything else is refused, not re-routed.
    op = Operation(
        operation_id="generated-op-1", kind="update_profile",
        actor=Actor(
            ActorKind.HOLDER, person_id=PERSON,
            account=Identifier("discord", "user_id", "generated-m-account"),
            proof_issuer="generated-client"),
        target=Target(TargetKind.PERSON, person_id=PERSON),
        payload=ProfileEdit(display_name="Generated sneak"),
        observed_revisions={"identity": "0", "authority": "a-1"})
    outcome = await services.scoped_profile.apply(op)
    assert outcome.status is OutcomeStatus.DENIED
    assert store.scoped_names == {}


async def test_the_same_operation_id_replays_but_a_changed_effect_conflicts():
    services = _services()
    store = services.bindings
    await _person(services)
    await _operator(services, services.backend)
    first = await _apply(services, store, _operation())
    second = await _apply(services, store, _operation())
    assert first.status is second.status is OutcomeStatus.COMPLETE
    conflict = await _apply(
        services, store, _operation(nickname="Generated Other"))
    assert conflict.status is OutcomeStatus.DENIED
    assert conflict.detail == "operation-conflict"
    assert (await store.scoped_name_for(person_id=PERSON, scope=ROOM)) == NICK


class _FailOnceStore(FakeBindingStore):
    """A scoped-name write that fails once — the partial state a crash
    between the journal and the store write leaves."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_write = True

    async def set_scoped_name(self, **kw):
        if self.fail_write:
            self.fail_write = False
            return unavailable("generated store outage")
        return await super().set_scoped_name(**kw)


async def test_reconcile_finishes_exactly_the_journaled_scoped_effect():
    store = _FailOnceStore()
    services = _services(store)
    await _person(services)
    await _operator(services, services.backend)
    outcome = await _apply(services, store, _operation())
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    record = await store.operation("generated-op-1")
    assert record is not None
    assert record.state is not OperationJournalState.COMPLETE

    finished = await services.reconcile()
    assert [r.operation_id for r in finished] == ["generated-op-1"]
    assert (await store.scoped_name_for(person_id=PERSON, scope=ROOM)) == NICK


async def test_a_stale_replay_cannot_overwrite_a_newer_same_scope_intent():
    """Two journals for the same (person, scope): the newer owns the name —
    a replay of the older closes superseded without a write."""
    store = _FailOnceStore()
    services = _services(store)
    await _person(services)
    await _operator(services, services.backend)
    first = await _apply(services, store, _operation(op_id="generated-op-1"))
    assert first.status is OutcomeStatus.UNAVAILABLE
    # A newer intent for the SAME scope lands while the first is unfinished.
    second = await _apply(
        services, store,
        _operation(op_id="generated-op-2", nickname="Generated Newer"))
    assert second.status is OutcomeStatus.COMPLETE
    finished = await services.scoped_profile.reconcile()
    # Nothing completed — but the stale journal closed, journaled denied,
    # and never wrote its older nickname over the newer intent.
    assert finished == ()
    record = await store.operation("generated-op-1")
    assert record is not None
    assert record.state is OperationJournalState.COMPLETE
    assert record.outcome == "denied" and record.detail == "superseded"
    assert (await store.scoped_name_for(person_id=PERSON, scope=ROOM)
            ) == "Generated Newer"


async def test_scoped_and_global_edits_never_supersede_each_other():
    """The ordering partitions by profile target: a scoped nickname can
    neither supersede nor replay-overwrite the global display name, and
    another scope's label is independent too."""
    store = _FailOnceStore()
    services = _services(store)
    await _person(services)
    await _operator(services, services.backend)
    # Leave a scoped op INCOMPLETE, then rename globally — the global
    # edit is a different profile target, so it completes and wins ITS name.
    pending = await _apply(services, store, _operation(op_id="generated-op-1"))
    assert pending.status is OutcomeStatus.UNAVAILABLE
    latest = await store.latest_profile_operation(PERSON)
    assert latest is None  # the scoped op never claims the global ordering
    await store.set_display_name(person_id=PERSON, display_name="Generated Renamed")
    # The scoped replay still owns its own scope and finishes.
    finished = await services.scoped_profile.reconcile()
    assert [r.operation_id for r in finished] == ["generated-op-1"]
    assert (await store.person(PERSON)).display_name == "Generated Renamed"
    assert (await store.scoped_name_for(person_id=PERSON, scope=ROOM)) == NICK


async def test_the_nearest_ancestry_nickname_wins_then_the_global_name():
    services = _services()
    store = services.bindings
    await _person(services)
    await _operator(services, services.backend, scope=WORKSPACE)

    async def set_name(scope, nickname):
        op = _operation(
            op_id=f"generated-op-{scope.kind.value}", scope=scope,
            nickname=nickname)
        outcome = await _apply(services, store, op)
        assert outcome.status is OutcomeStatus.COMPLETE

    await set_name(WORKSPACE, "Generated Workspace Name")
    await set_name(UNIT, "Generated Unit Name")
    # Room inherits the unit's; the sibling unit's room keeps the workspace's.
    assert (await services.access.person_ref(PERSON, scope=ROOM)).name \
        == "Generated Unit Name"
    assert (await services.access.person_ref(PERSON, scope=SIBLING)).name \
        == "Generated Workspace Name"
    # A room override affects only that room — the sibling stays at unit level.
    await set_name(ROOM, NICK)
    assert (await services.access.person_ref(PERSON, scope=ROOM)).name == NICK
    assert (await services.access.person_ref(PERSON, scope=SIBLING)).name \
        == "Generated Workspace Name"
    # Unscoped stays global throughout.
    assert (await services.access.person_ref(PERSON)).name == GLOBAL


async def test_a_retired_id_reads_the_survivors_nickname():
    services = _services()
    store = services.bindings
    await _person(services)
    await _person(services, "generated-former")
    await _operator(services, services.backend)
    op = _operation(person_id="generated-former")
    outcome = await _apply(services, store, op)
    assert outcome.status is OutcomeStatus.COMPLETE
    await store.consolidate(survivor=PERSON, former="generated-former")
    ref = await services.access.person_ref("generated-former", scope=ROOM)
    assert ref is not None and ref.name == NICK


async def test_an_unreadable_scoped_source_never_answers_another_name():
    services = _services()
    store = services.bindings
    await _person(services)
    await _operator(services, services.backend)
    assert (await _apply(services, store, _operation())).status \
        is OutcomeStatus.COMPLETE
    store.down = True
    with pytest.raises(RuntimeError):
        await services.access.person_ref(PERSON, scope=ROOM)
    store.down = False
    # Recovery: the stored name reads again, no stale substitute.
    ref = await services.access.person_ref(PERSON, scope=ROOM)
    assert ref is not None and ref.name == NICK


async def test_absent_nickname_is_distinct_from_an_unreadable_store():
    services = _services()
    await _person(services)
    # No nickname anywhere: the read is the global name, honestly absent.
    ref = await services.access.person_ref(PERSON, scope=ROOM)
    assert ref is not None and ref.name == GLOBAL
    ref = await services.access.person_ref("generated-absent", scope=ROOM)
    assert ref is None


async def test_the_file_backend_refuses_the_write_and_reads_absent():
    """Operator-maintained file mode: no TOML write, no managed fallback —
    and absence is honest because file state holds no scoped names."""
    from nora.domain.models import PersonRef
    from nora.people.adapters.file_backend import FileBindingStore
    from nora.persons import Persons

    store = FileBindingStore(Persons(
        by_person={PERSON: PersonRef(
            person_id=PERSON, name=GLOBAL, honcho_peer_id="generated-peer")},
        index={}))
    outcome = await store.set_scoped_name(
        person_id=PERSON, scope=ROOM, nickname=NICK,
        operation_id="generated-op-1")
    assert outcome.status is OutcomeStatus.DENIED
    assert await store.scoped_name_for(person_id=PERSON, scope=ROOM) is None
    assert await store.identity_ids(PERSON) == (PERSON,)
    assert await store.identity_ids("generated-absent") is None


async def test_the_direct_scoped_service_honors_a_pending_authority_revocation():
    services = _services()
    store = services.bindings
    await _person(services)
    await _operator(services, services.backend)
    await store.block_authority(AuthorityBlock(
        OPERATOR, ROOM, "generated-revocation", "generated-revoke-op"))
    result = await services.scoped_profile.apply(_operation())
    assert result.status is OutcomeStatus.UNAVAILABLE
    assert not store.scoped_names and not store.operations
    await store.clear_block(person_id=OPERATOR, scope=ROOM)
    recovered = await services.scoped_profile.apply(_operation())
    assert recovered.status is OutcomeStatus.COMPLETE
    assert await store.scoped_name_for(person_id=PERSON, scope=ROOM) == NICK


async def test_a_pending_former_person_edit_cannot_overwrite_the_survivor():
    store = _FailOnceStore()
    services = _services(store)
    await _person(services)
    survivor = "generated-survivor"
    await _person(services, survivor)
    await _operator(services, services.backend)
    pending = await _apply(services, store, _operation())
    assert pending.status is OutcomeStatus.UNAVAILABLE
    merged = await store.consolidate(survivor=survivor, former=PERSON)
    assert merged.status is OutcomeStatus.COMPLETE
    current = await store.person(survivor)
    newer = await _apply(services, store, _operation(str(current.revision),
        op_id="generated-survivor-edit", person_id=survivor, nickname="Generated Newer"))
    assert newer.status is OutcomeStatus.COMPLETE
    finished = await services.scoped_profile.reconcile()
    assert await store.scoped_name_for(person_id=survivor, scope=ROOM) == "Generated Newer"
    assert finished == ()
    original = await store.operation("generated-op-1")
    assert original.outcome == "denied" and original.detail == "profile-target-changed"
    assert (await services.access.person_ref(PERSON, scope=ROOM)).name == "Generated Newer"


@pytest.mark.parametrize("nickname", ["", "   "])
async def test_blank_scoped_names_refuse_before_journaling(nickname):
    services = _services()
    store = services.bindings
    await _person(services)
    await _operator(services, services.backend)
    result = await _apply(services, store, _operation(nickname=nickname))
    assert result.status is OutcomeStatus.DENIED
    assert not store.scoped_names and not store.operations
    assert (await _apply(services, store, _operation())).status is OutcomeStatus.COMPLETE


class _ScopedReadFault(FakeBindingStore):
    def __init__(self, mode="mismatch"):
        super().__init__()
        self.mode = mode
        self.fault = True
        self.name_writes = 0

    async def set_scoped_name(self, **kwargs):
        result = await super().set_scoped_name(**kwargs)
        if result.status is OutcomeStatus.COMPLETE:
            self.name_writes += 1
        return result

    async def scoped_name_for(self, **kwargs):
        value = await super().scoped_name_for(**kwargs)
        if self.fault and value is not None:
            if self.mode == "exception":
                raise RuntimeError("generated scoped read outage")
            return "Generated Mismatching Read"
        return value


@pytest.mark.parametrize("mode", ["mismatch", "exception"])
async def test_a_committed_scoped_name_with_failed_readback_is_incomplete(mode):
    store = _ScopedReadFault(mode)
    services = _services(store)
    await _person(services)
    await _operator(services, services.backend)
    result = await services.scoped_profile.apply(_operation())
    assert result.status is OutcomeStatus.INCOMPLETE
    recorded = await store.operation("generated-op-1")
    assert recorded.state is OperationJournalState.INCOMPLETE
    assert recorded.steps["profile"] == "done"
    assert recorded.steps["readback"] == "pending"
    store.fault = False
    assert await store.scoped_name_for(person_id=PERSON, scope=ROOM) == NICK
    finished = await services.scoped_profile.reconcile()
    assert [record.operation_id for record in finished] == ["generated-op-1"]
    assert store.name_writes == 1
    assert (await store.operation("generated-op-1")).state is OperationJournalState.COMPLETE


async def test_the_room_facing_name_reads_forward_the_callers_scope():
    """`resolve_ref`, `display_name` and `name_transcript` take the caller's
    trusted read scope — the same `person_ref` contract — so the consumers
    that render a room never need a second, global-only path."""
    from nora.domain.models import (
        DeliveryStatus,
        NormalizedMessage,
        TranscriptRow,
    )

    store = FakeBindingStore()
    services = _services(store)
    await _person(services)
    for scope, nickname in (
            (SIBLING, "Generated Sibling Nickname"), (ROOM, NICK)):
        outcome = await store.set_scoped_name(
            person_id=PERSON, scope=scope, nickname=nickname,
            operation_id=f"generated-op-{scope.id}")
        assert outcome.status is OutcomeStatus.COMPLETE

    access = services.access
    ref = await access.resolve_ref(
        "discord", "generated-generated-member-account", scope=ROOM)
    assert ref is not None and ref.name == NICK
    msg = NormalizedMessage(
        platform="discord", conversation_id="generated-room",
        conversation_type="group",
        sender_id="generated-generated-member-account",
        sender_name="Generated Platform Name", timestamp=1,
        body="generated body", raw={})
    assert await access.display_name(msg, scope=ROOM) == NICK
    row = TranscriptRow(
        id=1, sender_id="generated-generated-member-account",
        sender_name="Generated Platform Name", ts=1, body="generated body",
        is_nora=False, delivery_status=DeliveryStatus.SENT,
        raw_json=None)
    (named,) = await access.name_transcript(
        (row,), platform="discord", scope=ROOM)
    assert named.person_name == NICK and named.person_id == PERSON

    # The sibling scope's own view and the unscoped global view resolve
    # through the same seam — the scope decides, not the caller.
    ref = await access.resolve_ref(
        "discord", "generated-generated-member-account", scope=SIBLING)
    assert ref is not None and ref.name == "Generated Sibling Nickname"
    ref = await access.resolve_ref(
        "discord", "generated-generated-member-account")
    assert ref is not None and ref.name == GLOBAL

    store.down = True
    try:
        with pytest.raises(RuntimeError):
            await access.resolve_ref(
                "discord", "generated-generated-member-account", scope=ROOM)
    finally:
        store.down = False
    ref = await access.resolve_ref(
        "discord", "generated-generated-member-account", scope=ROOM)
    assert ref is not None and ref.name == NICK


async def test_a_sibling_only_member_is_denied_the_local_scope_profile_edit():
    """Membership in the sibling unit is real — and confers nothing on
    this room's chain: the same operator gate that admits the in-unit
    holder refuses the sibling-only member's edit before any effect."""
    services = _services()
    store = services.bindings
    await _person(services)
    sibling = "generated-sibling-member"
    await _person(services, sibling)
    await store.set_membership(person_id=sibling, scope=SIBLING,
                               member=True, source="generated")
    await services.backend.put_relation(
        scope=SIBLING, relation="member", person_id=sibling)
    for child, parent in (
            (ROOM, UNIT), (SIBLING, SIBLING_UNIT),
            (UNIT, WORKSPACE), (SIBLING_UNIT, WORKSPACE)):
        await services.backend.publish_containment(scope=child, parent=parent)
    # The member standing is real — in the sibling unit, where it grants.
    assert (await services.backend.check(
        scope=SIBLING, person_id=sibling, permit="view")).allowed
    op = Operation(
        operation_id="generated-op-sibling", kind="update_profile",
        actor=Actor(ActorKind.OPERATOR, person_id=sibling, scope=ROOM),
        target=Target(TargetKind.PERSON, person_id=PERSON),
        payload=ProfileEdit(nickname=NICK, scope=ROOM),
        observed_revisions={"identity": "0", "authority": "a-1"})
    outcome = await _apply(services, store, op, person_id=sibling)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "operator-not-authorized"
    assert store.scoped_names == {}

    # The in-unit positive control: the same edit from an operator whose
    # standing is on this room's chain completes.
    await _operator(services, services.backend)
    positive = await _apply(services, store, _operation(op_id="generated-op-local"))
    assert positive.status is OutcomeStatus.COMPLETE
