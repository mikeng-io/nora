"""`AuthorityService` against the in-memory fakes — the behaviors the
managed backend's scoped-authority half owns (`authorization.md`):

- the authoritative `check` is revocation-first (blocks answer
  `unavailable`), topology-real (`unknown_scope` ≠ `denied`), and
  lifecycle-aware — never a snapshot, never a file;
- an operator's standing is revalidated LIVE at commitment through the
  backend's own traversal — a workspace delegation reaches a room, a
  revoked or never-held delegation refuses, and an unreadable check is
  `unavailable`, not a denial;
- journaled steps are idempotent: a mid-effect outage leaves `incomplete`,
  the journaled effect replays to `complete` without a fresh actor, and a
  failed REMOVE raises a durable `AuthorityBlock` so a stale grant stops
  answering now;
- roster sync removes before it adds, and only a `complete` roster may
  establish absence.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    AuthorityBlock,
    ManagedAccount,
    ManagedPerson,
    MembershipState,
    OperationJournalState,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
)
from nora.domain.management import (
    AccessChange,
    AccessDirection,
    Actor,
    ActorKind,
    Delegation,
    MembershipStanding,
    Operation,
    RosterSync,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
)
from nora.people.app.managed import AuthorityService
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeTopologySource,
)

WS = Scope(ScopeKind.WORKSPACE, "ws-main")
UNIT = Scope(ScopeKind.UNIT, "unit-a")
ROOM = Scope(ScopeKind.ROOM, "signal/conv-1")
PERSON = "person_alice"
OPERATOR = "person_op"
MEMBER_ID = Identifier(platform="signal", kind="uuid", value="85290000101")


class _DropsFail(FakeAuthorityBackend):
    """A backend that answers reads but refuses tuple REMOVES — the
    outage a revoke meets mid-operation."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_drops = False

    async def drop_relation(
        self, *, scope: Scope, relation: str, person_id: str
    ) -> Outcome:
        if self.fail_drops:
            return Outcome(OutcomeStatus.UNAVAILABLE, "down")
        return await super().drop_relation(
            scope=scope, relation=relation, person_id=person_id
        )


def _service(
    backend: FakeAuthorityBackend | None = None,
) -> tuple[FakeBindingStore, FakeAuthorityBackend, AuthorityService]:
    store = FakeBindingStore()
    backend = backend or FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: (UNIT.id, False)},
        units={UNIT.id: WS.id},
        workspaces={WS.id},
    )
    return store, backend, AuthorityService(store, backend, topology)


async def _person(
    store: FakeBindingStore, person_id: str, ident: Identifier | None = None
) -> None:
    ident = ident or Identifier(
        platform="signal", kind="uuid", value=f"sig-{person_id}"
    )
    await store.register(
        person=ManagedPerson(person_id=person_id, lifecycle=PersonLifecycle.ACTIVE),
        account=ManagedAccount(
            account_id=str(uuid4()), person_id=person_id,
            platform="signal", identifiers=frozenset({ident}),
            state=AccountState.BOUND,
        ),
        membership=None,
    )


def _op(
    operation_id: str, kind: str, *, target: Target, payload: object,
    actor_kind: ActorKind = ActorKind.OPERATOR,
    actor_person: str = OPERATOR,
) -> Operation:
    actor = (
        Actor(kind=ActorKind.OPERATOR, person_id=actor_person, scope=WS)
        if actor_kind is ActorKind.OPERATOR
        else (
            Actor(kind=ActorKind.INSTALLATION, person_id=actor_person)
            if actor_kind is ActorKind.INSTALLATION
            else Actor(
                kind=ActorKind.SERVICE,
                service_id="bridge",
                sources=frozenset({"signal"}),
            )
        )
    )
    return Operation(
        operation_id=operation_id, kind=kind, actor=actor, target=target,
        payload=payload, observed_revisions={"authority": "0"},
    )


async def _grant_operator(
    store: FakeBindingStore,
    backend: FakeAuthorityBackend,
    *permits: str,
    person: str = OPERATOR,
    scope: Scope = ROOM,
) -> None:
    await _person(store, person)
    for permit in permits:
        await backend.put_relation(
            scope=scope, relation=f"grant_{permit}", person_id=person
        )


async def test_retired_subject_checks_the_survivors_permissions_and_blocks():
    store, backend, svc = _service()
    await _person(store, PERSON)
    former = "generated-retired-person"
    assert (await store.add_alias(former_id=former, person_id=PERSON)
            ).status is OutcomeStatus.COMPLETE
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=PERSON)
    assert (await svc.check(scope=ROOM, person_id=former,
                            permit="view")).verdict is Verdict.ALLOWED
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=former)
    await backend.put_relation(scope=ROOM, relation="deny_view", person_id=PERSON)
    assert (await svc.check(scope=ROOM, person_id=former,
                            permit="view")).verdict is Verdict.DENIED
    store.blocks[(PERSON, ROOM)] = AuthorityBlock(PERSON, ROOM, "generated hold", "generated-op")
    assert (await svc.check(scope=ROOM, person_id=former,
                            permit="view")).verdict is Verdict.UNAVAILABLE


async def test_publication_hold_blocks_checks_and_new_mutations_until_its_owner_releases():
    store, backend, service = _service()
    await _person(store, PERSON)
    await _grant_operator(store, backend, "manage_grants")
    await backend.put_relation(scope=ROOM, relation="member", person_id=PERSON)
    assert (await service.check(scope=ROOM, person_id=PERSON,
                                permit="view")).verdict is Verdict.ALLOWED
    await store.hold_identity(person_id=PERSON, operation_id="generated-publication")
    assert (await service.check(scope=ROOM, person_id=PERSON,
                                permit="view")).verdict is Verdict.UNAVAILABLE
    operation = _op("generated-held-grant", "grant_access",
        target=Target(kind=TargetKind.SCOPE, scope=ROOM),
        payload=AccessChange(direction=AccessDirection.GRANT, grantee=PERSON,
                             permits=frozenset({"view"})))
    assert (await service.apply(operation)).status is OutcomeStatus.UNAVAILABLE
    assert await store.operation(operation.operation_id) is None
    await store.release_identity_hold(person_id=PERSON, operation_id="generated-other")
    assert (await service.apply(operation)).status is OutcomeStatus.UNAVAILABLE
    await store.release_identity_hold(person_id=PERSON, operation_id="generated-publication")
    assert (await service.apply(operation)).status is OutcomeStatus.COMPLETE


async def test_member_check_allows_room_speak_and_view():
    store, backend, svc = _service()
    await _person(store, PERSON)
    await backend.put_relation(scope=ROOM, relation="member", person_id=PERSON)
    for permit in ("speak", "view"):
        result = await svc.check(scope=ROOM, person_id=PERSON, permit=permit)
        assert result.verdict is Verdict.ALLOWED


async def test_membership_confers_nothing_on_the_parent_unit():
    """OPL: member speaks on the Room alone — a unit check for a room
    member denies, never inherits upward."""
    store, backend, svc = _service()
    await _person(store, PERSON)
    await backend.put_relation(scope=ROOM, relation="member", person_id=PERSON)
    result = await svc.check(scope=UNIT, person_id=PERSON, permit="view")
    assert result.verdict is Verdict.DENIED


async def test_deny_wins_over_a_direct_grant():
    store, backend, svc = _service()
    await _person(store, PERSON)
    await backend.put_relation(
        scope=ROOM, relation="grant_speak", person_id=PERSON
    )
    await backend.put_relation(
        scope=ROOM, relation="deny_speak", person_id=PERSON
    )
    result = await svc.check(scope=ROOM, person_id=PERSON, permit="speak")
    assert result.verdict is Verdict.DENIED


async def test_an_undeclared_scope_is_unknown_not_denied():
    store, _, svc = _service()
    await _person(store, PERSON)
    result = await svc.check(
        scope=Scope(ScopeKind.ROOM, "signal/nowhere"),
        person_id=PERSON, permit="speak",
    )
    assert result.verdict is Verdict.UNKNOWN_SCOPE


async def test_a_suspended_person_denies_every_check():
    store, backend, svc = _service()
    await _person(store, PERSON)
    await backend.put_relation(
        scope=ROOM, relation="member", person_id=PERSON
    )
    await store.set_lifecycle(
        person_id=PERSON, lifecycle=PersonLifecycle.SUSPENDED
    )
    result = await svc.check(scope=ROOM, person_id=PERSON, permit="speak")
    assert result.verdict is Verdict.DENIED
    assert result.detail == "person-suspended"


async def test_an_authority_block_fails_closed_as_unavailable():
    store, backend, svc = _service()
    await _person(store, PERSON)
    await backend.put_relation(
        scope=ROOM, relation="grant_speak", person_id=PERSON
    )
    await store.block_authority(
        AuthorityBlock(
            person_id=PERSON, scope=ROOM,
            reason="revoke-unconfirmed", operation_id="op-x",
        )
    )
    result = await svc.check(scope=ROOM, person_id=PERSON, permit="speak")
    assert result.verdict is Verdict.UNAVAILABLE


async def test_operator_authority_is_revalidated_at_commitment():
    """An operator without the delegated permit refuses — the service
    re-checks the actor's standing NOW, not at request time."""
    store, _, svc = _service()
    await _person(store, OPERATOR)
    await _person(store, PERSON)
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
    assert outcome.status is OutcomeStatus.DENIED
    assert "manage_grants" in outcome.detail


async def test_an_unreadable_actor_check_is_unavailable_not_denied():
    """A backend that cannot answer the actor's standing is an outage —
    the operation refuses `unavailable`, never an authoritative denial a
    caller could read as 'not permitted'."""
    store, backend, svc = _service()
    await _person(store, OPERATOR)
    await _person(store, PERSON)
    backend.down = True
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
    assert outcome.status is OutcomeStatus.UNAVAILABLE


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


async def test_a_multi_permit_grant_applies_every_permit():
    """Regression: the journaled `authority` step is one batch — every
    permit lands before the step marks done (a per-permit mark used to
    skip the rest and dead-end read-back)."""
    store, backend, svc = _service()
    await _grant_operator(store, backend, "manage_grants")
    await _person(store, PERSON)
    outcome = await svc.apply(
        _op(
            "op-1", "grant_access",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=AccessChange(
                direction=AccessDirection.GRANT,
                grantee=PERSON, permits=frozenset({"speak", "view"}),
            ),
        )
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    for permit in ("speak", "view"):
        result = await svc.check(scope=ROOM, person_id=PERSON, permit=permit)
        assert result.verdict is Verdict.ALLOWED


async def test_a_failed_revoke_blocks_the_affected_person_until_confirmed():
    """Revocation-first: the remove could not confirm, so the stale grant
    stops answering NOW — `unavailable` on that (person, scope) until
    reconciliation clears the block."""
    store, backend, svc = _service(backend=_DropsFail())
    await _person(store, OPERATOR)
    await _person(store, PERSON)
    await backend.put_relation(
        scope=ROOM, relation="grant_speak", person_id=PERSON
    )
    backend.fail_drops = True
    outcome = await svc.apply(
        _op(
            "op-1", "revoke_access",
            actor_kind=ActorKind.INSTALLATION,
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=AccessChange(
                direction=AccessDirection.GRANT,
                grantee=PERSON, permits=frozenset({"speak"}),
            ),
        )
    )
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    blocks = await store.authority_blocks()
    assert [(b.person_id, b.scope) for b in blocks] == [(PERSON, ROOM)]
    # The stale tuple is still there — but it no longer answers.
    result = await svc.check(scope=ROOM, person_id=PERSON, permit="speak")
    assert result.verdict is Verdict.UNAVAILABLE


async def test_an_incomplete_operation_replays_to_complete():
    """A revoke that failed mid-run stays `incomplete` in the journal;
    `apply_replay` on THAT row — not a re-submitted op — resumes the
    pending steps, and `reconcile` clears the block it raised."""
    backend = _DropsFail()
    store, backend, svc = _service(backend=backend)
    await _person(store, OPERATOR)
    await _person(store, PERSON)
    await backend.put_relation(
        scope=ROOM, relation="grant_speak", person_id=PERSON
    )
    backend.fail_drops = True
    outcome = await svc.apply(
        _op(
            "op-1", "revoke_access",
            actor_kind=ActorKind.INSTALLATION,
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=AccessChange(
                direction=AccessDirection.GRANT,
                grantee=PERSON, permits=frozenset({"speak"}),
            ),
        )
    )
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    record = await store.operation("op-1")
    assert record is not None
    assert record.state is OperationJournalState.INCOMPLETE

    backend.fail_drops = False
    outcome = await svc.apply_replay(record)
    assert outcome.status is OutcomeStatus.COMPLETE
    finished = await svc.reconcile()
    assert [r.operation_id for r in finished] == []  # op-1 already complete
    assert await store.authority_blocks() == ()
    result = await svc.check(scope=ROOM, person_id=PERSON, permit="speak")
    assert result.verdict is Verdict.DENIED


async def test_a_stale_replay_cannot_revoke_a_later_completed_grant():
    backend = _DropsFail()
    store, backend, service = _service(backend)
    await _person(store, PERSON)
    await _grant_operator(store, backend, "manage_grants")
    await backend.put_relation(scope=ROOM, relation="grant_speak", person_id=PERSON)
    payload = AccessChange(direction=AccessDirection.GRANT, grantee=PERSON,
                           permits=frozenset({"speak"}))
    target = Target(kind=TargetKind.SCOPE, scope=ROOM)
    backend.fail_drops = True
    failed = await service.apply(_op("generated-old-revoke", "revoke_access",
        actor_kind=ActorKind.INSTALLATION, target=target, payload=payload))
    assert failed.status is OutcomeStatus.UNAVAILABLE
    stale = await store.operation("generated-old-revoke")
    assert stale is not None and stale.state is OperationJournalState.INCOMPLETE
    backend.fail_drops = False
    assert (await service.apply_replay(replace(stale))).status is OutcomeStatus.COMPLETE
    assert (await service.check(scope=ROOM, person_id=PERSON,
                                permit="speak")).verdict is Verdict.DENIED
    assert (await service.apply(_op("generated-later-grant", "grant_access",
        target=target, payload=payload))
            ).status is OutcomeStatus.COMPLETE
    assert (await service.check(scope=ROOM, person_id=PERSON,
                                permit="speak")).verdict is Verdict.ALLOWED
    assert (await service.apply_replay(stale)).status is OutcomeStatus.COMPLETE
    assert (await service.check(scope=ROOM, person_id=PERSON,
                                permit="speak")).verdict is Verdict.ALLOWED


async def test_reconcile_resumes_what_a_restart_left():
    """Restart path: an operation journaled incomplete is resumed by
    `reconcile` alone — the block it raised clears only after the replayed
    read-back passes."""
    backend = _DropsFail()
    store, backend, svc = _service(backend=backend)
    await _person(store, OPERATOR)
    await _person(store, PERSON)
    await backend.put_relation(
        scope=ROOM, relation="grant_speak", person_id=PERSON
    )
    backend.fail_drops = True
    await svc.apply(
        _op(
            "op-1", "revoke_access",
            actor_kind=ActorKind.INSTALLATION,
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=AccessChange(
                direction=AccessDirection.GRANT,
                grantee=PERSON, permits=frozenset({"speak"}),
            ),
        )
    )
    backend.fail_drops = False
    finished = await svc.reconcile()
    assert [r.operation_id for r in finished] == ["op-1"]
    assert finished[0].state is OperationJournalState.COMPLETE
    assert await store.authority_blocks() == ()
    result = await svc.check(scope=ROOM, person_id=PERSON, permit="speak")
    assert result.verdict is Verdict.DENIED


async def test_a_reused_operation_id_with_a_different_effect_conflicts():
    store, backend, svc = _service()
    await _grant_operator(store, backend, "manage_grants")
    await _person(store, PERSON)
    await svc.apply(
        _op(
            "op-1", "grant_access",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=AccessChange(
                direction=AccessDirection.GRANT,
                grantee=PERSON, permits=frozenset({"speak"}),
            ),
        )
    )
    outcome = await svc.apply(
        _op(
            "op-1", "grant_access",
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=AccessChange(
                direction=AccessDirection.GRANT,
                grantee=PERSON, permits=frozenset({"view"}),
            ),
        )
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "operation-conflict"


async def test_sync_membership_removes_departed_and_keeps_joined():
    """A complete roster establishes absence: sync-sourced members absent
    from the roster depart — member tuple gone, standing LEFT — while a
    registration-sourced membership is untouched."""
    store, backend, svc = _service()
    await _person(store, "person_stay", MEMBER_ID)
    await _person(store, "person_go")
    await _person(store, "person_keep")
    await store.set_membership(
        person_id="person_go", scope=ROOM, member=True, source="sync"
    )
    await store.set_membership(
        person_id="person_keep", scope=ROOM, member=True,
        source="registration",
    )
    for p in ("person_go", "person_keep"):
        await backend.put_relation(scope=ROOM, relation="member", person_id=p)
    outcome = await svc.apply(
        _op(
            "op-1", "sync_membership",
            actor_kind=ActorKind.SERVICE,
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=RosterSync(
                members=frozenset({MEMBER_ID}), complete=True
            ),
        )
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    standing = {
        m.person_id: m.state for m in await store.members_of(ROOM)
    }
    assert standing["person_stay"] is MembershipState.MEMBER
    assert standing["person_go"] is MembershipState.LEFT
    # Provenance: a registration-sourced membership is not a roster's to
    # remove.
    assert standing["person_keep"] is MembershipState.MEMBER
    result = await svc.check(scope=ROOM, person_id="person_go", permit="speak")
    assert result.verdict is Verdict.DENIED


async def test_an_incomplete_roster_never_removes():
    """`complete=False` is the roster's own claim that it may be partial —
    it can add, it can never establish absence."""
    store, backend, svc = _service()
    await _person(store, "person_go")
    await store.set_membership(
        person_id="person_go", scope=ROOM, member=True, source="sync"
    )
    await backend.put_relation(
        scope=ROOM, relation="member", person_id="person_go"
    )
    outcome = await svc.apply(
        _op(
            "op-1", "sync_membership",
            actor_kind=ActorKind.SERVICE,
            target=Target(kind=TargetKind.SCOPE, scope=ROOM),
            payload=RosterSync(members=frozenset(), complete=False),
        )
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    standing = {
        m.person_id: m.state for m in await store.members_of(ROOM)
    }
    assert standing["person_go"] is MembershipState.MEMBER
    result = await svc.check(scope=ROOM, person_id="person_go", permit="speak")
    assert result.verdict is Verdict.ALLOWED


async def test_a_down_store_is_unavailable_on_both_paths():
    store, _, svc = _service()
    await _person(store, PERSON)
    store.down = True
    outcome = await svc.apply(
        _op(
            "op-1", "set_membership",
            target=Target(
                kind=TargetKind.MEMBERSHIP, person_id=PERSON, scope=ROOM
            ),
            payload=MembershipStanding(member=True),
        )
    )
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    result = await svc.check(scope=ROOM, person_id=PERSON, permit="speak")
    assert result.verdict is Verdict.UNAVAILABLE


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
