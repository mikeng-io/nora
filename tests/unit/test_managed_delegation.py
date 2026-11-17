"""`delegate`/`revoke_delegation` against the in-memory fakes — the
bounded-authority half of `authorization.md`:

- a person delegation writes `grant_<permit>` tuples for the named
  delegatee alone — exactly the delegated permits, revocable, reaching
  only scopes the real containment tree encloses;
- an operator can never delegate to their own identity, canonical or
  alias-folded;
- an observed platform role holds no authority until the INSTALLATION
  delegates to it — then exactly the named permits, to exactly the
  roster-reported holders, and a complete roster's retraction removes the
  derived authority again;
- role delegation is the installation's out-of-band form: no in-band
  actor may write it.
"""

from __future__ import annotations

import time

from nora.domain.identity import Identifier
from nora.domain.managed import (
    MembershipState,
    OutcomeStatus,
    Verdict,
)
from nora.domain.management import (
    AccessChange,
    AccessDirection,
    Actor,
    ActorKind,
    Delegation,
    MembershipStanding,
    ObservedRole,
    Operation,
    RoleDelegation,
    RosterSync,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
)
from nora.people.app.managed import AuthorityService
from nora.testing.managed import (
    FakeBindingStore,
    FakeTopologySource,
)
from nora.testing.managed_authority import FakeAuthorityBackend
from tests.unit.test_managed_authority import (
    MEMBER_ID,
    OPERATOR,
    PERSON,
    ROOM,
    UNIT,
    WS,
    _grant_operator,
    _op,
    _person,
    _service,
)

MEMBER2_ID = Identifier(platform="signal", kind="uuid", value="85290000102")
INSTALLER = "person_installer"


def _role_roster(*, roles: frozenset[ObservedRole] = frozenset(),
                 members: frozenset[Identifier] = frozenset({MEMBER_ID}),
                 complete: bool = True,
                 observed_ms: int = 0) -> RosterSync:
    return RosterSync(
        members=members, complete=complete,
        observed_ms=observed_ms or int(time.time() * 1000), roles=roles)


async def _sync(svc, operation_id: str, roster: RosterSync):
    return await svc.apply(
        _op(operation_id, "sync_membership", actor_kind=ActorKind.SERVICE,
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=roster))


def _install_delegation(operation_id: str, kind: str,
                        payload: object) -> Operation:
    return _op(operation_id, kind, actor_kind=ActorKind.INSTALLATION,
               actor_person=INSTALLER,
               target=Target(kind=TargetKind.SCOPE, scope=ROOM),
               payload=payload)


async def test_a_workspace_delegation_reaches_a_room_through_the_backend():
    """Delegation on the workspace passes the room-level revalidation via
    OPL traversal — no caller-asserted containment."""
    store, backend, svc = _service()
    await _person(store, OPERATOR)
    await _person(store, PERSON)
    # The containment chain the first publish writes: room→unit→workspace.
    await backend.publish_containment(scope=UNIT, parent=WS)
    await backend.publish_containment(scope=ROOM, parent=UNIT)
    await backend.put_relation(
        scope=WS, relation="grant_manage_grants", person_id=OPERATOR
    )
    outcome = await svc.apply(
        _op(
            "op-1", "grant_access",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=AccessChange(
                direction=AccessDirection.GRANT,
                grantee=PERSON, permits=frozenset({"speak"}),
            ),
        )
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    result = await svc.check(scope=ROOM, person_id=PERSON, permit="speak")
    assert result.verdict is Verdict.ALLOWED


async def test_delegation_is_a_real_operation_not_a_side_effect():
    """`delegate` writes grant_<permit> tuples for the delegatee — the
    delegatee's new standing is then what the operator revalidation
    consults on their next operation."""
    store, backend, svc = _service()
    await _grant_operator(store, backend, "delegate")
    await _person(store, PERSON)
    outcome = await svc.apply(
        _op(
            "op-1", "delegate",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=Delegation(
                delegatee=PERSON, permits=frozenset({"manage_members"})
            ),
        )
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    # PERSON's new standing is real: the actor revalidation on their own
    # operation passes — the op then denies on the absent TARGET person,
    # which is what proves the actor check ran and passed.
    outcome = await svc.apply(
        _op(
            "op-2", "set_membership",
            actor_person=PERSON,
            target=Target(
                kind=TargetKind.MEMBERSHIP, person_id="person_new", scope=ROOM
            ),
            payload=MembershipStanding(member=True),
        )
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "target-person-inactive"


async def test_an_operator_cannot_delegate_to_their_own_identity():
    """Self-promotion refused at commitment: a `delegate`-holding operator
    whose delegatee is their own identity — directly or through a retired
    alias — is `denied` and no grant tuple lands, while the same operation
    still completes for any other person."""
    store, backend, svc = _service()
    await _grant_operator(store, backend, "delegate")
    await _person(store, PERSON)
    outcome = await svc.apply(
        _op("op-1", "delegate",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=Delegation(delegatee=OPERATOR,
                               permits=frozenset({"manage_grants"}))))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "operator-self-promotion"
    assert (await svc.check(scope=ROOM, person_id=OPERATOR,
                            permit="manage_grants")).verdict is Verdict.DENIED
    assert (await store.add_alias(former_id="person_op_former",
                                  person_id=OPERATOR)
            ).status is OutcomeStatus.COMPLETE
    outcome = await svc.apply(
        _op("op-2", "delegate",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=Delegation(delegatee="person_op_former",
                               permits=frozenset({"manage_grants"}))))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "operator-self-promotion"
    assert (await svc.check(scope=ROOM, person_id=OPERATOR,
                            permit="manage_grants")).verdict is Verdict.DENIED
    outcome = await svc.apply(
        _op("op-3", "delegate",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=Delegation(delegatee=PERSON,
                               permits=frozenset({"manage_members"}))))
    assert outcome.status is OutcomeStatus.COMPLETE


async def test_installation_bootstrap_authority_needs_no_in_band_standing():
    """Initial authority arrives out of band: an INSTALLATION actor applies
    a delegation with no prior tuples and no delegated standing — the
    boundary already proved it. In-band actors cannot construct this kind
    (`test_suspend_identity_is_installation_only_and_reasoned`,
    `test_empty_or_non_service_credential_is_refused`,
    `test_service_token_never_becomes_operator_or_holder_authority`)."""
    store, _backend, svc = _service()
    await _person(store, PERSON)
    outcome = await svc.apply(
        _install_delegation(
            "op-1", "delegate",
            Delegation(delegatee=PERSON,
                       permits=frozenset({"manage_grants"}))))
    assert outcome.status is OutcomeStatus.COMPLETE
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="manage_grants")).verdict is Verdict.ALLOWED


async def test_a_delegation_in_another_unit_cannot_reach_this_room():
    """Forged containment refused by the real tree: `Scope.encloses` is
    kind-level, so the operation constructs — but the operator's
    `manage_members` grant lives in unit-b, and a room the topology places
    under unit-a never admits their write."""
    other_unit = Scope(ScopeKind.UNIT, "unit-b")
    other_room = Scope(ScopeKind.ROOM, "signal/conv-other")
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: (UNIT.id, False),
               other_room.id: (other_unit.id, False)},
        units={UNIT.id: WS.id, other_unit.id: WS.id},
        workspaces={WS.id},
    )
    svc = AuthorityService(store, backend, topology)
    await _person(store, OPERATOR)
    await _person(store, PERSON)
    # The only containment the backend ever holds is topology-derived —
    # what `_publish_containment` writes on a real apply.
    for cursor, parent in ((UNIT, WS), (ROOM, UNIT),
                           (other_unit, WS), (other_room, other_unit)):
        await backend.publish_containment(scope=cursor, parent=parent)
    await backend.put_relation(
        scope=other_unit, relation="grant_manage_members",
        person_id=OPERATOR)
    outcome = await svc.apply(Operation(
        operation_id="op-1", kind="set_membership",
        actor=Actor(kind=ActorKind.OPERATOR, person_id=OPERATOR,
                    scope=other_unit),
        target=Target(kind=TargetKind.MEMBERSHIP, person_id=PERSON,
                      scope=ROOM),
        payload=MembershipStanding(member=True),
        observed_revisions={"authority": "0"}))
    assert outcome.status is OutcomeStatus.DENIED
    assert "manage_members" in outcome.detail
    assert (await svc.check(scope=other_room, person_id=OPERATOR,
                            permit="manage_members")).verdict is Verdict.ALLOWED
    assert (await svc.check(scope=ROOM, person_id=OPERATOR,
                            permit="manage_members")).verdict is Verdict.DENIED


async def test_an_observed_member_without_delegation_holds_no_management():
    """A platform observation — a synced roster member, the shape a guild
    owner or admin observation arrives as — is `member`, never authority:
    speak/view pass while every management verb denies, until an explicit
    `delegate` grants exactly the named permits, and `revoke_delegation`
    removes that derived authority again."""
    store, backend, svc = _service()
    await _person(store, PERSON, MEMBER_ID)
    await _grant_operator(store, backend, "delegate")
    synced = await _sync(
        svc, "op-sync", _role_roster(members=frozenset({MEMBER_ID}),
                                   complete=False))
    assert synced.status is OutcomeStatus.COMPLETE
    for permit in ("speak", "view"):
        assert (await svc.check(scope=ROOM, person_id=PERSON,
                                permit=permit)).verdict is Verdict.ALLOWED
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="manage_members")).verdict is Verdict.DENIED
    for kind, target, payload in (
        ("set_membership",
         Target(kind=TargetKind.MEMBERSHIP, person_id=OPERATOR, scope=ROOM),
         MembershipStanding(member=True)),
        ("grant_access",
         Target(kind=TargetKind.SCOPE, scope=ROOM),
         AccessChange(direction=AccessDirection.GRANT, grantee=OPERATOR,
                      permits=frozenset({"speak"}))),
        ("delegate",
         Target(kind=TargetKind.SCOPE, scope=ROOM),
         Delegation(delegatee=OPERATOR, permits=frozenset({"delegate"}))),
    ):
        outcome = await svc.apply(
            _op(f"op-{kind}", kind, actor_person=PERSON,
                target=target, payload=payload))
        assert outcome.status is OutcomeStatus.DENIED
        assert "lacks" in outcome.detail
    delegated = await svc.apply(
        _op("op-delegate", "delegate",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=Delegation(delegatee=PERSON,
                               permits=frozenset({"manage_members"}))))
    assert delegated.status is OutcomeStatus.COMPLETE
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="manage_members")).verdict is Verdict.ALLOWED
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="manage_grants")).verdict is Verdict.DENIED
    revoked = await svc.apply(
        _op("op-revoke", "revoke_delegation",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=Delegation(delegatee=PERSON,
                               permits=frozenset({"manage_members"}))))
    assert revoked.status is OutcomeStatus.COMPLETE
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="manage_members")).verdict is Verdict.DENIED


async def test_a_delegation_carries_exactly_its_named_permits():
    """`manage_members` delegated is `manage_members` alone: the delegatee's
    `grant_access` still refuses `actor-lacks-manage_grants` at commitment —
    exactly the delegated authority, never the adjacent permits."""
    store, backend, svc = _service()
    await _grant_operator(store, backend, "delegate")
    await _person(store, PERSON)
    assert (await svc.apply(
        _op("op-1", "delegate",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=Delegation(delegatee=PERSON,
                               permits=frozenset({"manage_members"}))))
    ).status is OutcomeStatus.COMPLETE
    outcome = await svc.apply(
        _op("op-2", "grant_access", actor_person=PERSON,
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=AccessChange(direction=AccessDirection.GRANT,
                                 grantee=PERSON, permits=frozenset({"speak"}))))
    assert outcome.status is OutcomeStatus.DENIED
    assert "manage_grants" in outcome.detail


async def test_an_observed_role_without_delegation_confers_nothing():
    """The locked negative: a roster-reported platform role is an
    observation, never authority — its holder denies every management
    verb until the installation delegates the role."""
    store, backend, svc = _service()
    await _person(store, PERSON, MEMBER_ID)
    synced = await _sync(svc, "op-sync", _role_roster(
        roles=frozenset({ObservedRole(member=MEMBER_ID, role="stewards")})))
    assert synced.status is OutcomeStatus.COMPLETE
    assert await backend.role_members(scope=ROOM, role="stewards") == frozenset(
        {PERSON})
    for permit in ("manage_members", "manage_grants", "delegate"):
        assert (await svc.check(scope=ROOM, person_id=PERSON,
                                permit=permit)).verdict is Verdict.DENIED


async def test_a_delegated_role_grants_its_observed_holders_exactly():
    """The locked positive: the installation delegates `manage_members`
    to the `stewards` role — every roster-reported holder gains exactly
    that permit (a holder of a different label and a plain member gain
    nothing), and `revoke_delegation` removes the derived authority."""
    store, _backend, svc = _service()
    await _person(store, PERSON, MEMBER_ID)
    await _person(store, "person_plain", MEMBER2_ID)
    synced = await _sync(svc, "op-sync", _role_roster(
        members=frozenset({MEMBER_ID, MEMBER2_ID}),
        roles=frozenset({ObservedRole(member=MEMBER_ID, role="stewards")})))
    assert synced.status is OutcomeStatus.COMPLETE
    delegated = await svc.apply(_install_delegation(
        "op-delegate", "delegate",
        RoleDelegation(role="stewards",
                       permits=frozenset({"manage_members"}))))
    assert delegated.status is OutcomeStatus.COMPLETE
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="manage_members")).verdict is Verdict.ALLOWED
    for permit in ("manage_grants", "delegate"):
        assert (await svc.check(scope=ROOM, person_id=PERSON,
                                permit=permit)).verdict is Verdict.DENIED
    assert (await svc.check(scope=ROOM, person_id="person_plain",
                            permit="manage_members")).verdict is Verdict.DENIED
    revoked = await svc.apply(_install_delegation(
        "op-revoke", "revoke_delegation",
        RoleDelegation(role="stewards",
                       permits=frozenset({"manage_members"}))))
    assert revoked.status is OutcomeStatus.COMPLETE
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="manage_members")).verdict is Verdict.DENIED


async def test_a_complete_roster_retracts_a_roles_derived_authority():
    """Removal semantics: a later complete roster that no longer reports
    the holder's role retracts the holder — the delegated role's grant
    stops answering for them even though the grant tuple itself stays."""
    store, backend, svc = _service()
    await _person(store, PERSON, MEMBER_ID)
    synced = await _sync(svc, "op-sync", _role_roster(
        roles=frozenset({ObservedRole(member=MEMBER_ID, role="stewards")})))
    assert synced.status is OutcomeStatus.COMPLETE
    delegated = await svc.apply(_install_delegation(
        "op-delegate", "delegate",
        RoleDelegation(role="stewards",
                       permits=frozenset({"manage_members"}))))
    assert delegated.status is OutcomeStatus.COMPLETE
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="manage_members")).verdict is Verdict.ALLOWED
    retracted = await _sync(svc, "op-sync-2",
                            _role_roster(observed_ms=int(time.time() * 1000) + 1000))
    assert retracted.status is OutcomeStatus.COMPLETE
    assert await backend.role_members(scope=ROOM, role="stewards") == frozenset()
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="manage_members")).verdict is Verdict.DENIED


async def test_a_role_delegation_is_the_installations_alone():
    """An operator — even one holding `delegate` — cannot write a role
    delegation: delegating to a role they hold is self-promotion through
    the roster, so only the out-of-band installation form is accepted."""
    store, backend, svc = _service()
    await _grant_operator(store, backend, "delegate")
    outcome = await svc.apply(
        _op("op-1", "delegate",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=RoleDelegation(role="stewards",
                                   permits=frozenset({"manage_members"}))))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "role-delegation-installation-only"
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="manage_members")).verdict is Verdict.DENIED


async def test_an_incomplete_roster_never_retracts_role_holders():
    """A partial roster can add role holders but never establish their
    absence — the same completeness rule members already obey."""
    store, backend, svc = _service()
    await _person(store, PERSON, MEMBER_ID)
    synced = await _sync(svc, "op-sync", _role_roster(
        roles=frozenset({ObservedRole(member=MEMBER_ID, role="stewards")})))
    assert synced.status is OutcomeStatus.COMPLETE
    partial = await _sync(svc, "op-sync-2", _role_roster(
        complete=False, observed_ms=int(time.time() * 1000) + 1000))
    assert partial.status is OutcomeStatus.COMPLETE
    assert await backend.role_members(scope=ROOM, role="stewards") == frozenset(
        {PERSON})


async def test_a_departing_member_loses_their_role_and_derived_authority():
    """A member the complete roster drops leaves the scope — their role
    holder tuple retracts with them, so a delegated role stops answering
    for a person who is no longer present at all."""
    store, _backend, svc = _service()
    await _person(store, PERSON, MEMBER_ID)
    await _person(store, "person_stay", MEMBER2_ID)
    synced = await _sync(svc, "op-sync", _role_roster(
        members=frozenset({MEMBER_ID, MEMBER2_ID}),
        roles=frozenset({ObservedRole(member=MEMBER_ID, role="stewards")})))
    assert synced.status is OutcomeStatus.COMPLETE
    delegated = await svc.apply(_install_delegation(
        "op-delegate", "delegate",
        RoleDelegation(role="stewards",
                       permits=frozenset({"manage_members"}))))
    assert delegated.status is OutcomeStatus.COMPLETE
    dropped = await _sync(svc, "op-sync-2", _role_roster(
        members=frozenset({MEMBER2_ID}), observed_ms=int(time.time() * 1000) + 1000))
    assert dropped.status is OutcomeStatus.COMPLETE
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="manage_members")).verdict is Verdict.DENIED
    assert (await svc.check(scope=ROOM, person_id=PERSON,
                            permit="speak")).verdict is Verdict.DENIED
    standing = {m.person_id: m.state for m in await store.members_of(ROOM)}
    assert standing["person_stay"] is MembershipState.MEMBER
    assert (await store.memberships_for(PERSON))[0].state is MembershipState.LEFT
