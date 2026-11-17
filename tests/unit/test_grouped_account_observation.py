"""`observe_account` on a GROUP of matchable identifiers — account-exact
grouped enrichment (`identity.md` «One person, many accounts»):

- an approved observation presenting identifiers the SAME account does
  not yet hold binds them onto that account — the phone-only account
  later seen as `phone`+`uuid` keeps its person and account;
- every supplied identifier must resolve to ONE bound account first —
  a group split across two accounts is denied even under one canonical
  person, and a foreign, revoked or reserved identifier is never
  re-pointed, linked or partially widened;
- the journal makes an exact retry idempotent and a reused operation
  identity carrying a different group an operation conflict;
- membership sync only ever sees a group that read back as one account —
  a conflict leaves no member behind.
"""

from __future__ import annotations

import time
from uuid import uuid4

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    ObservedMember,
    OperationJournalState,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.domain.management import (
    AccountObservation,
    Actor,
    ActorKind,
    Operation,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
)
from nora.people.app.managed import ManagedServices, RegistrationService
from nora.people.app.roster_provisioning import RosterProvisioning
from nora.testing.managed import (
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from nora.testing.managed_authority import FakeAuthorityBackend

UNIT = Scope(ScopeKind.UNIT, "generated-unit")
PERSON = "person_alice"
PHONE = Identifier(platform="signal", kind="phone", value="85290000101")
ACCT = Identifier(platform="signal", kind="uuid", value="85290000101")
ACCT_2 = Identifier(platform="signal", kind="uuid", value="85290000102")
SERVICE = Actor(
    kind=ActorKind.SERVICE, service_id="generated-bridge",
    sources=frozenset({"signal"}),
)


def _service() -> tuple[FakeBindingStore, FakeIdentityRegistry, RegistrationService]:
    store = FakeBindingStore()
    registry = FakeIdentityRegistry()
    return store, registry, RegistrationService(store, registry)


def _observe_op(
    operation_id: str,
    identifiers: frozenset[Identifier],
    *,
    target: Identifier | None = None,
    ownership: str = "0",
) -> Operation:
    group = frozenset(identifiers)
    return Operation(
        operation_id=operation_id,
        kind="observe_account",
        actor=SERVICE,
        target=Target(
            kind=TargetKind.ACCOUNT,
            account=target
            or min(group, key=lambda i: (i.platform, i.kind, i.value)),
        ),
        payload=AccountObservation(identifiers=group),
        observed_revisions={"ownership": ownership},
    )


async def _seed(
    store: FakeBindingStore,
    person_id: str,
    identifiers: frozenset[Identifier],
    *,
    revoked: bool = False,
) -> ManagedAccount:
    """Seed an independent account through the shared store contract."""
    account = ManagedAccount(
        account_id=str(uuid4()),
        person_id=person_id,
        platform="signal",
        identifiers=identifiers,
        state=AccountState.BOUND,
    )
    outcome = await store.register(
        person=ManagedPerson(
            person_id=person_id, lifecycle=PersonLifecycle.ACTIVE
        ),
        account=account,
        membership=None,
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    if revoked:
        await store.revoke_binding(account=next(iter(identifiers)))
    return account


def _roster() -> tuple[ManagedServices, RosterProvisioning]:
    services = ManagedServices(
        FakeBindingStore(), FakeAuthorityBackend(),
        FakeTopologySource(units={UNIT.id: "generated-workspace"},
                           workspaces={"generated-workspace"}),
        FakeIdentityRegistry(), FakeLinkRequests())
    return services, RosterProvisioning(
        bindings=services.bindings, registry=services.registry,
        topology=services.topology, registration=services.registration,
        authority=services.authority)


async def test_an_unclaimed_group_registers_one_independent_person():
    """Baseline: a group naming nothing yet becomes ONE person and ONE
    account — independent discovery, never a merge."""
    store, _, svc = _service()
    outcome = await svc.apply(_observe_op("op-1", {PHONE, ACCT}))
    assert outcome.status is OutcomeStatus.COMPLETE
    for ident in (PHONE, ACCT):
        bound = await store.record_for(account=ident)
        assert bound is not None
        assert bound.identifiers == frozenset({PHONE, ACCT})
    assert len(store.people) == 1 and len(store.accounts) == 1


async def test_a_phone_only_account_enriches_when_the_pair_arrives():
    """The acceptance case: phone was observed alone; a later approved
    `phone`+`uuid` observation binds the unclaimed uuid onto the SAME
    account and person — no second person, no second account."""
    store, _, svc = _service()
    assert (await svc.apply(_observe_op("op-1", {PHONE}))).status is (
        OutcomeStatus.COMPLETE)
    bound = await store.record_for(account=PHONE)
    assert bound is not None
    outcome = await svc.apply(
        _observe_op("op-2", {PHONE, ACCT}, ownership=str(bound.revision))
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert outcome.detail == bound.person_id
    for ident in (PHONE, ACCT):
        record = await store.record_for(account=ident)
        assert record is not None
        assert record.account_id == bound.account_id
        assert record.person_id == bound.person_id
    assert len(store.people) == 1 and len(store.accounts) == 1


async def test_enrichment_works_when_only_the_non_selected_identifier_resolves():
    """Reverse selection: the operation's target is the unbound phone
    while the uuid already resolves — the group still names one account."""
    store, _, svc = _service()
    await svc.apply(_observe_op("op-1", {ACCT}))
    bound = await store.record_for(account=ACCT)
    assert bound is not None
    outcome = await svc.apply(
        _observe_op("op-2", {PHONE, ACCT}, target=PHONE,
                    ownership=str(bound.revision))
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    record = await store.record_for(account=PHONE)
    assert record is not None and record.account_id == bound.account_id
    assert len(store.accounts) == 1


async def test_a_pair_spanning_two_accounts_on_one_person_is_denied():
    """Two accounts, ONE canonical person: the pair is a link claim —
    observation never widens, merges or picks a side."""
    store, _, svc = _service()
    one = await _seed(store, PERSON, {PHONE})
    two = await _seed(store, PERSON, {ACCT})
    outcome = await svc.apply(_observe_op("op-1", {PHONE, ACCT}))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "observation-accounts-conflict"
    for ident, account in ((PHONE, one), (ACCT, two)):
        bound = await store.record_for(account=ident)
        assert bound is not None and bound.account_id == account.account_id
        assert bound.identifiers == account.identifiers
    assert len(store.accounts) == 2


async def test_a_pair_spanning_two_people_is_denied():
    store, _, svc = _service()
    await _seed(store, PERSON, {PHONE})
    await _seed(store, "person_other", {ACCT})
    outcome = await svc.apply(_observe_op("op-1", {PHONE, ACCT}))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "observation-accounts-conflict"
    assert len(store.people) == 2 and len(store.accounts) == 2


async def test_a_revoked_identifiers_ownership_is_never_reassigned():
    """A revoked binding keeps its reservation: the surviving account on
    the same person cannot absorb the retired identifier either."""
    store, _, svc = _service()
    await _seed(store, PERSON, {PHONE}, revoked=True)
    live = await _seed(store, PERSON, {ACCT})
    outcome = await svc.apply(_observe_op("op-1", {PHONE, ACCT}))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "identifier-conflict"
    assert await store.record_for(account=PHONE) is None
    bound = await store.record_for(account=ACCT)
    assert bound is not None and bound.account_id == live.account_id
    assert bound.identifiers == live.identifiers


async def test_a_revoked_accounts_identifiers_stay_reserved_forever():
    """No account resolves, but every identifier is reserved: a fresh
    observation cannot claim them for a NEW person."""
    store, _, svc = _service()
    await _seed(store, PERSON, {PHONE, ACCT}, revoked=True)
    outcome = await svc.apply(_observe_op("op-1", {PHONE, ACCT}))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "identifier-conflict"
    for ident in (PHONE, ACCT):
        assert await store.record_for(account=ident) is None
    assert len(store.people) == 1


async def test_a_publication_hold_on_any_resolved_identifier_defers():
    """The publication-refusal rule covers the whole group: a hold on the
    person behind ANY supplied identifier defers the observation — never
    a partial enrichment under a publishing person."""
    store, _, svc = _service()
    await svc.apply(_observe_op("op-1", {PHONE}))
    bound = await store.record_for(account=PHONE)
    assert bound is not None
    await store.hold_identity(
        person_id=bound.person_id, operation_id="generated-hold"
    )
    outcome = await svc.apply(_observe_op("op-2", {PHONE, ACCT}))
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "identity-publication-pending"
    assert await store.record_for(account=ACCT) is None


async def test_a_suspended_persons_account_cannot_be_enriched():
    store, _, svc = _service()
    await svc.apply(_observe_op("op-1", {PHONE}))
    bound = await store.record_for(account=PHONE)
    assert bound is not None
    await store.set_lifecycle(
        person_id=bound.person_id, lifecycle=PersonLifecycle.SUSPENDED
    )
    outcome = await svc.apply(
        _observe_op("op-2", {PHONE, ACCT}, ownership=str(bound.revision))
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "observation-binding-changed"
    assert await store.record_for(account=ACCT) is None


async def test_an_exact_retry_of_a_committed_observation_preserves_its_effect():
    """Same operation id, same group: the journal answers with the
    recorded effect — ownership and revision untouched."""
    store, _, svc = _service()
    await svc.apply(_observe_op("op-1", {PHONE}))
    bound = await store.record_for(account=PHONE)
    assert bound is not None
    enriched = await svc.apply(
        _observe_op("op-2", {PHONE, ACCT}, ownership=str(bound.revision))
    )
    assert enriched.status is OutcomeStatus.COMPLETE
    owner = await store.record_for(account=ACCT)
    assert owner is not None
    replay = await svc.apply(
        _observe_op("op-2", {PHONE, ACCT}, ownership=str(owner.revision))
    )
    assert replay.status is OutcomeStatus.COMPLETE
    still = await store.record_for(account=ACCT)
    assert still is not None
    assert still.account_id == owner.account_id
    assert still.revision == owner.revision
    assert len(store.accounts) == 1


async def test_a_reused_operation_id_with_a_different_group_conflicts():
    store, _, svc = _service()
    assert (await svc.apply(_observe_op("op-1", {PHONE, ACCT}))).status is (
        OutcomeStatus.COMPLETE)
    bound = await store.record_for(account=PHONE)
    assert bound is not None
    outcome = await svc.apply(
        _observe_op("op-1", {PHONE, ACCT_2}, ownership=str(bound.revision))
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "operation-conflict"


async def test_a_resumed_enrichment_finishes_without_rebinding():
    """A registry outage mid-observation leaves the journal INCOMPLETE —
    the resume finishes the same effect and binds nothing twice."""
    store, registry, svc = _service()
    await svc.apply(_observe_op("op-1", {PHONE}))
    bound = await store.record_for(account=PHONE)
    assert bound is not None
    registry.down = True
    outage = await svc.apply(
        _observe_op("op-2", {PHONE, ACCT}, ownership=str(bound.revision))
    )
    assert outage.status is OutcomeStatus.INCOMPLETE
    journaled = await store.operation("op-2")
    assert journaled is not None
    assert journaled.state is OperationJournalState.INCOMPLETE
    assert journaled.steps["binding"] == "done"
    registry.down = False
    revision = (await store.record_for(account=ACCT)).revision
    replay = await svc.apply(
        _observe_op("op-2", {PHONE, ACCT}, ownership=str(revision))
    )
    assert replay.status is OutcomeStatus.COMPLETE
    settled = await store.record_for(account=ACCT)
    assert settled is not None
    assert settled.account_id == bound.account_id
    assert settled.revision == revision


async def test_roster_enriches_the_member_and_syncs_its_membership():
    """The roster path end-to-end: a phone-only account re-observed as
    `phone`+`uuid` keeps its person, gains the uuid, and reaches
    membership sync — the whole group read back as one account."""
    services, provisioning = _roster()
    await services.registration.apply(_observe_op("seed", {PHONE}))
    bound = await services.bindings.record_for(account=PHONE)
    assert bound is not None
    outcome = await provisioning.apply(
        operation_id="roster-1", actor=SERVICE, scope=UNIT,
        collection="generated-collection",
        members=(ObservedMember(frozenset({PHONE, ACCT})),),
        complete_roster=True, observed_ms=int(time.time() * 1000))
    assert outcome.status is OutcomeStatus.COMPLETE
    for ident in (PHONE, ACCT):
        record = await services.bindings.record_for(account=ident)
        assert record is not None and record.account_id == bound.account_id
    members = await services.bindings.members_of(UNIT)
    assert [m.person_id for m in members] == [bound.person_id]


async def test_roster_refuses_a_group_split_across_accounts():
    """A roster group whose identifiers live on different accounts never
    reaches membership sync — no member, no merged account, nothing
    partial, whichever direction the denial surfaces."""
    services, provisioning = _roster()
    one = await _seed(services.bindings, PERSON, {PHONE})
    two = await _seed(services.bindings, "person_other", {ACCT})
    outcome = await provisioning.apply(
        operation_id="roster-1", actor=SERVICE, scope=UNIT,
        collection="generated-collection",
        members=(ObservedMember(frozenset({PHONE, ACCT})),),
        complete_roster=True, observed_ms=int(time.time() * 1000))
    assert outcome.status is OutcomeStatus.INCOMPLETE
    assert outcome.detail == "account-registration-unconfirmed"
    assert await services.bindings.members_of(UNIT) == ()
    for ident, account in ((PHONE, one), (ACCT, two)):
        bound = await services.bindings.record_for(account=ident)
        assert bound is not None and bound.account_id == account.account_id
