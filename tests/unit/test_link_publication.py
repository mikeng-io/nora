"""The link publication executor, against the real fake stores.

`observe` grounds the request in live revisions; `commit` runs the whole
journaled effect; every refusal and partial failure is measured on the
actual service — nothing here answers for the store.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nora.domain.account_linking import (
    ConsentProof,
    ConsentSurface,
    LinkState,
    LinkStateError,
    PendingLink,
)
from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    OperationJournalState,
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
)
from nora.domain.management import (
    AccessChange,
    AccessDirection,
    Actor,
    ActorKind,
    LifecycleAction,
    LinkApproval,
    LinkRequest,
    LinkSide,
    Operation,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
    Unlink,
)
from nora.people.app.link_publication import LinkPublicationService, _journal_id
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from nora.testing.managed_authority import FakeAuthorityBackend

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PERSON_A = "synthetic-person-a"
PERSON_B = "synthetic-person-b"
PERSON_C = "synthetic-person-c"
ACCT_I = Identifier("discord", "user_id", "synthetic-initiating")
ACCT_R = Identifier("signal", "uuid", "synthetic-receiving")
ACCT_C = Identifier("telegram", "user_id", "synthetic-third")
NONCE = {LinkSide.INITIATING: "gen-init-nonce", LinkSide.RECEIVING: "gen-recv-nonce"}
ISSUER = {LinkSide.INITIATING: "gen-discord-surface", LinkSide.RECEIVING: "gen-signal-surface"}
_SURFACE_FOR = {"discord": ConsentSurface.DISCORD_REAUTH,
                "signal": ConsentSurface.SIGNAL_PRIVATE,
                "telegram": ConsentSurface.TELEGRAM_REAUTH}
ROOM = Scope(ScopeKind.ROOM, "signal/synthetic-room")
UNIT = Scope(ScopeKind.UNIT, "synthetic-unit")
WS = Scope(ScopeKind.WORKSPACE, "synthetic-workspace")


def _world():
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: (UNIT.id, False)},
        units={UNIT.id: WS.id},
        workspaces={WS.id},
    )
    registry = FakeIdentityRegistry()
    links = FakeLinkRequests()
    svc = LinkPublicationService(store, backend, topology, registry, links)
    return store, backend, topology, registry, links, svc


async def _person(store, registry, person_id, ident, scope=None):
    account = ManagedAccount(
        account_id=str(uuid4()), person_id=person_id,
        platform=ident.platform, identifiers=frozenset({ident}),
        state=AccountState.BOUND,
    )
    membership = (
        ManagedMembership(person_id=person_id, scope=scope,
                          state=MembershipState.MEMBER, source="registration")
        if scope is not None else None
    )
    outcome = await store.register(
        person=ManagedPerson(person_id=person_id, lifecycle=PersonLifecycle.ACTIVE),
        account=account, membership=membership,
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    await registry.ensure_identity(external_id=person_id, traits={})


def _actor(side, person):
    account = ACCT_I if side is LinkSide.INITIATING else ACCT_R
    return Actor(ActorKind.HOLDER, person_id=person,
                 account=account, proof_issuer=ISSUER[side])


async def _requested(svc, links, *, initiating=ACCT_I, receiving=ACCT_R,
                     initiating_person=PERSON_A, surviving_person=PERSON_A,
                     request_id="synthetic-request", nonces=None, now=NOW):
    """Observe -> request through the REAL store, on any overlapping pair."""
    nonces = NONCE if nonces is None else nonces
    observation = await svc.observe(
        initiating=initiating, receiving=receiving,
        surviving_person=surviving_person)
    op = Operation(
        request_id, "request_link",
        Actor(ActorKind.HOLDER, person_id=initiating_person, account=initiating,
              proof_issuer=ISSUER[LinkSide.INITIATING]),
        Target(TargetKind.ACCOUNT, account=receiving),
        LinkRequest(surviving_person), dict(observation.revisions),
    )
    return await links.create(PendingLink.request(
        op, receiving_person=observation.receiving_person,
        initiating_nonce=nonces[LinkSide.INITIATING],
        receiving_nonce=nonces[LinkSide.RECEIVING],
        created_at=now, expires_at=now + timedelta(minutes=5),
    ), nonces=nonces)


async def _approve(links, link, side, *, person, account, surviving_person,
                   request_id, operation_id, now=NOW, nonce=None):
    decision = Operation(
        operation_id, "approve_link",
        Actor(ActorKind.HOLDER, person_id=person, account=account,
              proof_issuer=ISSUER[side]),
        Target(TargetKind.ACCOUNT, account=link.pair.account(LinkSide.RECEIVING)),
        LinkApproval(request_id=request_id, side=side, pair=link.pair,
                     surviving_person=surviving_person),
        dict(link.revisions),
    )
    proof = ConsentProof(
        account=account, issuer=ISSUER[side],
        surface=_SURFACE_FOR[account.platform],
        authenticated_at=now + timedelta(seconds=1),
        request_id=request_id,
        nonce=NONCE[side] if nonce is None else nonce,
    )
    return await links.decide(decision, proof, now=now + timedelta(seconds=2))


async def _approved(svc, links, *, request_id="synthetic-request", now=NOW):
    """Observe -> request -> both holder decisions through the REAL store."""
    link = await _requested(svc, links, request_id=request_id, now=now)
    link = await _approve(links, link, LinkSide.INITIATING, person=PERSON_A,
                          account=ACCT_I, surviving_person=PERSON_A,
                          request_id=request_id,
                          operation_id="gen-approve-initiating", now=now)
    return await _approve(links, link, LinkSide.RECEIVING, person=PERSON_B,
                          account=ACCT_R, surviving_person=PERSON_A,
                          request_id=request_id,
                          operation_id="gen-approve-receiving", now=now)


async def _approved_competing(svc, links, *, request_id="competing-request", now=NOW):
    """Second approved request naming the same bound receiving account."""
    link = await _requested(
        svc, links, initiating=ACCT_C, receiving=ACCT_R,
        initiating_person=PERSON_C, surviving_person=PERSON_C,
        request_id=request_id, now=now)
    link = await _approve(links, link, LinkSide.INITIATING, person=PERSON_C,
                          account=ACCT_C, surviving_person=PERSON_C,
                          request_id=request_id,
                          operation_id="gen-approve-competing-init", now=now)
    return await _approve(links, link, LinkSide.RECEIVING, person=PERSON_B,
                          account=ACCT_R, surviving_person=PERSON_C,
                          request_id=request_id,
                          operation_id="gen-approve-competing-recv", now=now)


async def _linked_world():
    store, backend, topology, registry, links, svc = _world()
    await _person(store, registry, PERSON_A, ACCT_I)
    await _person(store, registry, PERSON_B, ACCT_R)
    return store, backend, topology, registry, links, svc


async def test_commit_publishes_alias_consolidation_and_normalized_authority():
    store, backend, _topology, registry, links, svc = await _linked_world()
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=PERSON_B)
    await backend.put_relation(scope=UNIT, relation="restricted", person_id=PERSON_B)
    await _approved(svc, links)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.COMPLETE
    assert await store.canonical_person(PERSON_B) == PERSON_A
    assert await store.person(PERSON_B) is not None  # resolves to survivor
    initiator = await store.record_for(account=ACCT_I)
    receiver = await store.record_for(account=ACCT_R)
    assert initiator is not None and receiver is not None
    assert initiator.person_id == receiver.person_id == PERSON_A
    room = await backend.relations_on(scope=ROOM)
    assert ("grant_view", PERSON_B) not in room and ("grant_view", PERSON_A) in room
    assert ("restricted", PERSON_A) in await backend.relations_on(scope=UNIT)
    assert (await registry.identity_state(external_id=PERSON_B)).verdict is Verdict.DENIED
    assert not await store.identity_held(PERSON_A) and not await store.identity_held(PERSON_B)
    record = await store.operation(_journal_id("synthetic-request"))
    assert record is not None and record.state is OperationJournalState.COMPLETE
    assert (await links.get("synthetic-request")).state is LinkState.COMMITTED


async def test_observe_binds_real_owners_and_revisions():
    _store, backend, _topology, _registry, _links, svc = await _linked_world()
    observation = await svc.observe(
        initiating=ACCT_I, receiving=ACCT_R, surviving_person=PERSON_A)
    assert observation.owners[LinkSide.INITIATING] == PERSON_A
    assert observation.owners[LinkSide.RECEIVING] == PERSON_B
    assert set(observation.revisions) == {"identity", "ownership", "authority"}
    changed = await svc.observe(
        initiating=ACCT_I, receiving=ACCT_R, surviving_person=PERSON_B)
    assert changed.revisions["authority"] == observation.revisions["authority"]
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=PERSON_B)
    moved = await svc.observe(
        initiating=ACCT_I, receiving=ACCT_R, surviving_person=PERSON_B)
    assert moved.revisions["authority"] != observation.revisions["authority"]


async def test_commit_without_both_approvals_is_denied_and_nothing_changes():
    store, _backend, _topology, _registry, links, svc = await _linked_world()
    observation = await svc.observe(
        initiating=ACCT_I, receiving=ACCT_R, surviving_person=PERSON_A)
    op = Operation(
        "synthetic-request", "request_link", _actor(LinkSide.INITIATING, PERSON_A),
        Target(TargetKind.ACCOUNT, account=ACCT_R), LinkRequest(PERSON_A),
        dict(observation.revisions),
    )
    await links.create(PendingLink.request(
        op, receiving_person=PERSON_B,
        initiating_nonce=NONCE[LinkSide.INITIATING],
        receiving_nonce=NONCE[LinkSide.RECEIVING],
        created_at=NOW, expires_at=NOW + timedelta(minutes=5)),
        nonces=NONCE)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.DENIED
    assert await store.person(PERSON_B) is not None and not store.aliases
    assert not await store.identity_held(PERSON_A)
    assert not await store.identity_held(PERSON_B)
    assert (await links.get("synthetic-request")).state is LinkState.PENDING


async def test_expired_request_is_refused_not_committed():
    store, _backend, _topology, _registry, links, svc = await _linked_world()
    await _approved(svc, links)
    outcome = await svc.commit(
        "synthetic-request", now=NOW + timedelta(minutes=6))
    assert outcome.status is OutcomeStatus.DENIED
    assert await store.person(PERSON_B) is not None and not store.aliases
    assert not await store.identity_held(PERSON_A)
    assert not await store.identity_held(PERSON_B)


async def test_moved_ownership_between_approval_and_commit_denies():
    store, _backend, _topology, _registry, links, svc = await _linked_world()
    await _approved(svc, links)
    # Ownership moved after approval: the receiving account now belongs to
    # someone else — the approved state no longer holds, so nothing publishes.
    receiver = await store.record_for(account=ACCT_R)
    store.accounts[receiver.account_id] = replace(receiver, person_id=PERSON_A)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.DENIED
    assert await store.person(PERSON_B) is not None and not store.aliases


async def test_changed_authority_between_approval_and_commit_denies():
    store, backend, _topology, _registry, links, svc = await _linked_world()
    await _approved(svc, links)
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=PERSON_B)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.DENIED
    assert not store.aliases
    record = await store.operation(_journal_id("synthetic-request"))
    assert record is not None and record.state is OperationJournalState.COMPLETE
    assert record.outcome == "denied"


async def test_suspended_holder_denies_and_suspension_mid_commit_stays_held():
    store, _backend, _topology, _registry, links, svc = await _linked_world()
    await _approved(svc, links)
    await store.set_lifecycle(person_id=PERSON_B, lifecycle=PersonLifecycle.SUSPENDED)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.DENIED
    assert not store.aliases


async def test_a_held_person_blocks_commit_until_the_hold_releases():
    store, _backend, _topology, _registry, links, svc = await _linked_world()
    await _approved(svc, links)
    await store.hold_identity(person_id=PERSON_A, operation_id="other-operation")
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert not store.aliases
    await store.release_identity_hold(person_id=PERSON_A, operation_id="other-operation")
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.COMPLETE


class _Outage:
    """A backend wrapper that fails one verb and passes the rest — an
    outage that strikes MID-effect, after the journal row exists."""
    def __init__(self, inner, verbs):
        self._inner = inner
        self._verbs = verbs
    def __getattr__(self, name):
        if name in self._verbs:
            async def refuse(**kw):
                from nora.domain.managed import unavailable
                return unavailable("down")
            return refuse
        return getattr(self._inner, name)


async def test_kratos_outage_mid_commit_stays_incomplete_held_and_resumable():
    store, _backend, _topology, registry, links, svc = await _linked_world()
    await _approved(svc, links)
    svc._registry = _Outage(registry, {"set_state"})
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    # Consolidated, but the retire step could not confirm — both people stay
    # held so identity resolution and checks answer unavailable, not pass.
    assert await store.canonical_person(PERSON_B) == PERSON_A
    assert await store.identity_held(PERSON_A) and await store.identity_held(PERSON_B)
    record = await store.operation(_journal_id("synthetic-request"))
    assert record is not None and record.state is OperationJournalState.INCOMPLETE
    assert (await links.get("synthetic-request")).state is LinkState.COMMITTING
    # And the survivor's checks fail closed while the holds stand.
    svc._registry = registry
    recovered = ManagedServices(store, _backend, _topology, registry, links)
    finished = await recovered.reconcile()
    assert [r.operation_id for r in finished] == [_journal_id("synthetic-request")]
    assert not await store.identity_held(PERSON_A)
    assert (await links.get("synthetic-request")).state is LinkState.COMMITTED
    assert (await registry.identity_state(external_id=PERSON_B)).verdict is Verdict.DENIED


async def test_backend_outage_leaves_recorded_steps_resumable_after_restart():
    store, backend, topology, registry, links, svc = await _linked_world()
    await backend.put_relation(scope=ROOM, relation="deny_view", person_id=PERSON_B)
    await _approved(svc, links)
    svc._backend = _Outage(backend, {"put_relation", "drop_relation"})
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert await store.identity_held(PERSON_A)
    # A NEW service instance over the same stores resumes the recorded steps.
    recovered = LinkPublicationService(store, backend, topology, registry, links)
    finished = await recovered.reconcile()
    assert [r.operation_id for r in finished] == [_journal_id("synthetic-request")]
    rows = await backend.relations_on(scope=ROOM)
    assert ("deny_view", PERSON_A) in rows and ("deny_view", PERSON_B) not in rows


async def test_member_supports_merge_and_a_later_roster_departure_keeps_grant():
    store, backend, _topology, _registry, links, svc = await _linked_world()
    # B is a member by roster sync; after consolidation the survivor keeps
    # BOTH supports, so a roster that forgets the room cannot erase it.
    await store.set_membership(person_id=PERSON_B, scope=ROOM, member=True, source="sync")
    await store.set_membership(person_id=PERSON_B, scope=ROOM, member=True, source="delegation")
    await backend.put_relation(scope=ROOM, relation="member", person_id=PERSON_B)
    await _approved(svc, links)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.COMPLETE
    membership = (await store.memberships_for(PERSON_A))[0]
    assert membership.scope == ROOM and membership.state is MembershipState.MEMBER
    assert membership.sources == {"sync", "delegation"}
    rows = await backend.relations_on(scope=ROOM)
    assert ("member", PERSON_A) in rows and ("member", PERSON_B) not in rows


async def test_unlink_is_holder_exact_and_drops_derived_sessions():
    store, backend, _topology, registry, links, svc = await _linked_world()
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=PERSON_B)
    await _approved(svc, links)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.COMPLETE
    registry.sessions[PERSON_A] = 3
    # A different person cannot unlink the account — only its exact
    # current holder can (the op's actor.account == target.account is
    # construction-enforced; the person must match too).
    from nora.people.app.managed import RegistrationService
    registration = RegistrationService(store, registry)
    bound = await store.record_for(account=ACCT_R)
    person = await store.person(PERSON_A)
    revisions = {
        "identity": str(person.revision), "ownership": str(bound.revision),
        "authority": "observed",
    }
    wrong = Operation(
        "gen-unlink-wrong", "unlink_account",
        Actor(ActorKind.HOLDER, person_id="synthetic-intruder",
              account=ACCT_R, proof_issuer=ISSUER[LinkSide.RECEIVING]),
        Target(TargetKind.ACCOUNT, account=ACCT_R),
        Unlink(expected_person=PERSON_A),
        revisions,
    )
    assert (await registration.apply(wrong)).status is OutcomeStatus.DENIED
    assert await store.record_for(account=ACCT_R) is not None
    # The holder's own unlink revokes the binding and drops the sessions.
    right = Operation(
        "gen-unlink", "unlink_account",
        Actor(ActorKind.HOLDER, person_id=PERSON_A,
              account=ACCT_R, proof_issuer=ISSUER[LinkSide.RECEIVING]),
        Target(TargetKind.ACCOUNT, account=ACCT_R),
        Unlink(expected_person=PERSON_A), revisions,
    )
    outcome = await registration.apply(right)
    assert outcome.status is OutcomeStatus.COMPLETE
    assert await store.record_for(account=ACCT_R) is None
    assert await store.record_for(account=ACCT_I) is not None  # other account kept
    assert PERSON_A not in registry.sessions
    assert (await store.person(PERSON_A)) is not None  # history and person stay
    # The canonical alias remains, and the consolidated grant stays the
    # survivor's: unlink restores no pre-link permission snapshot.
    assert await store.canonical_person(PERSON_B) == PERSON_A
    rows = await backend.relations_on(scope=ROOM)
    assert ("grant_view", PERSON_A) in rows and ("grant_view", PERSON_B) not in rows


async def test_suspending_the_survivor_restores_no_loser_grants():
    """Suspension is a global hold, not a rewind: the consolidated grant
    stays exactly where the link put it, the alias stands and the retired
    person gains nothing back."""
    store, backend, topology, registry, links, svc = await _linked_world()
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=PERSON_B)
    await _approved(svc, links)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.COMPLETE
    person = await store.person(PERSON_A)
    op = Operation(
        "gen-suspend", "suspend_identity",
        Actor(ActorKind.INSTALLATION, person_id="synthetic-installer"),
        Target(TargetKind.PERSON, person_id=PERSON_A),
        LifecycleAction(reason="moderation"),
        {"identity": str(person.revision), "authority": "observed"},
    )
    services = ManagedServices(store, backend, topology, registry, links)
    outcome = await services.registration.apply(op)
    assert outcome.status is OutcomeStatus.COMPLETE
    person = await store.person(PERSON_A)
    assert person.lifecycle is PersonLifecycle.SUSPENDED
    assert await store.canonical_person(PERSON_B) == PERSON_A
    assert await backend.relations_on(scope=ROOM) == {("grant_view", PERSON_A)}


async def test_revoking_the_survivors_grant_restores_no_loser_grants():
    """Revocation removes the consolidated grant outright — it is not a
    rollback to the pre-link snapshot, so the retired person's original
    grant never comes back."""
    store, backend, topology, registry, links, svc = await _linked_world()
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=PERSON_B)
    await _approved(svc, links)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.COMPLETE
    services = ManagedServices(store, backend, topology, registry, links)
    op = Operation(
        "gen-revoke", "revoke_access",
        Actor(ActorKind.INSTALLATION, person_id="synthetic-installer"),
        Target(TargetKind.SCOPE, scope=ROOM),
        AccessChange(direction=AccessDirection.GRANT, grantee=PERSON_A,
                     permits=frozenset({"view"})),
        {"authority": "observed"},
    )
    outcome = await services.authority.apply(op)
    assert outcome.status is OutcomeStatus.COMPLETE
    assert not await backend.relations_on(scope=ROOM)
    assert await store.canonical_person(PERSON_B) == PERSON_A
    check = await services.authority.check(
        scope=ROOM, person_id=PERSON_B, permit="view")
    assert check.verdict is Verdict.DENIED


async def test_concurrent_commits_on_one_request_publish_once():
    store, _backend, _topology, _registry, links, svc = await _linked_world()
    await _approved(svc, links)
    outcomes = await asyncio.gather(
        svc.commit("synthetic-request", now=NOW + timedelta(seconds=3)),
        svc.commit("synthetic-request", now=NOW + timedelta(seconds=3)),
    )
    assert all(o.status is OutcomeStatus.COMPLETE for o in outcomes)
    assert await store.canonical_person(PERSON_B) == PERSON_A
    assert len(store.people) == 1
    # A replayed commit answers the recorded outcome — nothing publishes twice.
    again = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert again.status is OutcomeStatus.COMPLETE
    assert len(store.people) == 1 and len(store.aliases) == 1


async def test_commit_of_an_unapproved_request_never_uses_live_state_to_skip_approval():
    _store, _backend, _topology, _registry, _links, svc = await _linked_world()
    outcome = await svc.commit("absent-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.DENIED


async def test_observe_refuses_same_person_and_absent_accounts():
    store, _backend, _topology, _registry, _links, svc = await _linked_world()
    result = await svc.observe(
        initiating=ACCT_I, receiving=ACCT_I, surviving_person=PERSON_A)
    assert result.verdict is Verdict.DENIED
    result = await svc.observe(
        initiating=ACCT_I, receiving=Identifier("signal", "uuid", "nobody"),
        surviving_person=PERSON_A)
    assert result.verdict is Verdict.DENIED
    store.down = True
    result = await svc.observe(
        initiating=ACCT_I, receiving=ACCT_R, surviving_person=PERSON_A)
    assert result.verdict is Verdict.UNAVAILABLE


class _CrashBeforeTransition:
    def __init__(self, links):
        self._links = links

    def __getattr__(self, name):
        if name == "begin_commit":
            async def crash(*args, **kwargs):
                raise RuntimeError("generated interruption before transition")
            return crash
        return getattr(self._links, name)


@pytest.mark.parametrize("resume", ["commit", "reconcile"])
async def test_interruption_after_holds_before_transition_remains_recoverable(resume):
    store, backend, topology, registry, links, svc = await _linked_world()
    now = datetime.now(UTC)
    await _approved(svc, links, now=now - timedelta(seconds=3))
    interrupted = LinkPublicationService(store, backend, topology, registry,
                                          _CrashBeforeTransition(links))
    result = await interrupted.commit("synthetic-request", now=now)
    assert result.status is OutcomeStatus.UNAVAILABLE
    assert (await links.get("synthetic-request")).state is LinkState.PENDING
    assert await store.identity_held(PERSON_A)
    recovered = LinkPublicationService(store, backend, topology, registry, links)
    if resume == "commit":
        result = await recovered.commit("synthetic-request", now=now)
        assert result.status is OutcomeStatus.COMPLETE
    else:
        assert len(await recovered.reconcile()) == 1
    assert (await links.get("synthetic-request")).state is LinkState.COMMITTED
    assert not await store.identity_held(PERSON_A)
    assert not await store.identity_held(PERSON_B)


async def test_a_retried_commit_whose_recorded_effect_changed_is_refused():
    """A crash left a journaled publish; the retry recomputes the loser's
    fact set and finds it changed — the journaled effect refuses the new
    plan rather than silently publishing a different consolidation."""
    store, backend, topology, registry, links, svc = await _linked_world()
    now = datetime.now(UTC)
    await _approved(svc, links, now=now - timedelta(seconds=3))
    interrupted = LinkPublicationService(store, backend, topology, registry,
                                          _CrashBeforeTransition(links))
    result = await interrupted.commit("synthetic-request", now=now)
    assert result.status is OutcomeStatus.UNAVAILABLE
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=PERSON_B)
    outcome = await svc.commit("synthetic-request", now=now)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "effect-conflict"
    assert await store.canonical_person(PERSON_B) == PERSON_B
    rows = await backend.relations_on(scope=ROOM)
    assert ("grant_view", PERSON_B) in rows and ("grant_view", PERSON_A) not in rows


async def test_a_replayed_approval_or_proof_never_authorizes_a_commit():
    """One decision identity names one request and one effect: a consumed
    approval replayed under a second pending request is refused by the
    store, and commit revalidation still refuses the never-approved
    request — the legitimate approvals alongside stay valid."""
    _store, _backend, _topology, _registry, links, svc = await _linked_world()
    await _approved(svc, links)
    link = await _requested(
        svc, links, surviving_person=PERSON_B, request_id="replayed-target",
        nonces={LinkSide.INITIATING: "gen-init-b",
                LinkSide.RECEIVING: "gen-recv-b"})
    replayed = Operation(
        "gen-approve-initiating", "approve_link",
        _actor(LinkSide.INITIATING, PERSON_A),
        Target(TargetKind.ACCOUNT, account=ACCT_R),
        LinkApproval(request_id="replayed-target", side=LinkSide.INITIATING,
                     pair=link.pair, surviving_person=PERSON_B),
        dict(link.revisions),
    )
    proof = ConsentProof(
        account=ACCT_I, issuer=ISSUER[LinkSide.INITIATING],
        surface=_SURFACE_FOR[ACCT_I.platform],
        authenticated_at=NOW + timedelta(seconds=1),
        request_id="replayed-target", nonce="gen-init-b",
    )
    with pytest.raises(LinkStateError):
        await links.decide(replayed, proof, now=NOW + timedelta(seconds=2))
    # A fresh decision id replaying the FIRST request's consumed proof
    # dies too: this request recorded a different challenge.
    replayed = replace(replayed, operation_id="gen-approve-replay")
    proof = replace(proof, nonce=NONCE[LinkSide.INITIATING])
    with pytest.raises(LinkStateError):
        await links.decide(replayed, proof, now=NOW + timedelta(seconds=2))
    outcome = await svc.commit("replayed-target", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "awaiting-approvals"
    assert not (await links.get("replayed-target")).approved
    assert (await svc.commit(
        "synthetic-request", now=NOW + timedelta(seconds=3))
    ).status is OutcomeStatus.COMPLETE


async def test_a_competing_link_request_is_refused_by_commit_revalidation():
    """Two fully-approved requests name the same account: the first commit
    consolidates it, and the loser's commit re-reads an owner that no
    longer matches its approved state — refused, never published."""
    store, backend, _topology, registry, links, svc = await _linked_world()
    await _person(store, registry, PERSON_C, ACCT_C)
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=PERSON_B)
    await _approved(svc, links)
    await _approved_competing(svc, links)
    outcome = await svc.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert outcome.status is OutcomeStatus.COMPLETE
    outcome = await svc.commit("competing-request", now=NOW + timedelta(seconds=4))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "a holder's current ownership or lifecycle changed"
    assert await store.canonical_person(PERSON_B) == PERSON_A
    assert (await store.record_for(account=ACCT_C)).person_id == PERSON_C
    assert await backend.relations_on(scope=ROOM) == {("grant_view", PERSON_A)}
    assert (await links.get("competing-request")).state is LinkState.PENDING


async def test_concurrent_competing_link_commits_resolve_to_one_winner():
    """The mutation gate serializes the two first-commitments: whichever
    publishes first owns the shared account, and the other's revalidation
    then fails — exactly one consolidation and one refusal, no partial."""
    store, backend, _topology, registry, links, svc = await _linked_world()
    await _person(store, registry, PERSON_C, ACCT_C)
    await backend.put_relation(scope=ROOM, relation="grant_view", person_id=PERSON_B)
    await _approved(svc, links)
    await _approved_competing(svc, links)
    outcomes = await asyncio.gather(
        svc.commit("synthetic-request", now=NOW + timedelta(seconds=3)),
        svc.commit("competing-request", now=NOW + timedelta(seconds=3)),
    )
    assert {o.status for o in outcomes} == {
        OutcomeStatus.COMPLETE, OutcomeStatus.DENIED}
    assert len(store.people) == 2 and len(store.aliases) == 1
    winner = await store.canonical_person(PERSON_B)
    assert winner in {PERSON_A, PERSON_C}
    assert await backend.relations_on(scope=ROOM) == {("grant_view", winner)}
    states = [
        (await links.get("synthetic-request")).state,
        (await links.get("competing-request")).state,
    ]
    assert states.count(LinkState.COMMITTED) == 1


async def test_missing_retiring_kratos_identity_never_counts_as_confirmed_revocation():
    store, backend, topology, registry, links, svc = await _linked_world()
    await _approved(svc, links)
    interrupted = LinkPublicationService(store, backend, topology,
                                          _Outage(registry, {"set_state"}), links)
    result = await interrupted.commit("synthetic-request", now=NOW + timedelta(seconds=3))
    assert result.status is OutcomeStatus.UNAVAILABLE
    registry.identities.pop(PERSON_B)
    assert await svc.reconcile() == ()
    assert (await links.get("synthetic-request")).state is LinkState.COMMITTING
    assert await store.identity_held(PERSON_A)
    record = await store.operation(_journal_id("synthetic-request"))
    assert record.state is not OperationJournalState.COMPLETE


async def test_interrupted_preparation_preserves_another_operations_hold_and_remains_retryable():
    store, backend, topology, registry, links, svc = await _linked_world()
    now = datetime.now(UTC)
    await _approved(svc, links, now=now - timedelta(seconds=3))
    interrupted = LinkPublicationService(store, backend, topology, registry,
                                          _CrashBeforeTransition(links))
    result = await interrupted.commit("synthetic-request", now=now)
    assert result.status is OutcomeStatus.UNAVAILABLE
    await store.hold_identity(person_id=PERSON_A, operation_id="generated-other-owner")
    assert await svc.reconcile() == ()
    record = await store.operation(_journal_id("synthetic-request"))
    assert record.state is not OperationJournalState.COMPLETE
    assert await store.identity_held(PERSON_A)
    await store.release_identity_hold(person_id=PERSON_A, operation_id="generated-other-owner")
    assert len(await svc.reconcile()) == 1
    assert not await store.identity_held(PERSON_A)
