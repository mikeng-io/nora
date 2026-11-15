"""The holder-facing link lifecycle over the real fakes (`account-linking.md`).

These tests drive `HolderLinkService` — request, private view, decisions
(HTTP re-auth AND the trusted signal-cli transport), cancellation and
holder-exact unlink — against the SAME `FakeBindingStore`/`FakeLinkRequests`
the publication tests use, so what is asserted is the real consent and
publication path, not a mock of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from nora.domain.authentication import AccountAuthentication, SessionBinding
from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.domain.management import LinkSide, Scope, ScopeKind
from nora.people.adapters.file_backend import FileLinkRequests
from nora.people.app.holder_link import HolderLinkService, HolderLinkView
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PERSON_A = "synthetic-person-a"
PERSON_B = "synthetic-person-b"
PERSON_C = "synthetic-person-c"
ACCT_I = Identifier("discord", "user_id", "synthetic-initiating")
ACCT_R = Identifier("signal", "uuid", "synthetic-receiving")
ACCT_R_PHONE = Identifier("signal", "phone", "synthetic-receiving-number")
ACCT_X = Identifier("telegram", "user_id", "synthetic-unrelated")
DAEMON = "synthetic-signal-daemon"
DISCORD_CLIENT = "synthetic-discord-client"
ISSUER = "synthetic-issuer"
UNIT = Scope(ScopeKind.UNIT, "synthetic-unit")
ROOM = Scope(ScopeKind.ROOM, "signal/synthetic-room")
WS = Scope(ScopeKind.WORKSPACE, "synthetic-workspace")


@dataclass
class FakeDelivery:
    """Records private pushes instead of sending — proves the address and
    the text without a transport."""

    sent: list[tuple[Identifier, str]] = field(default_factory=list)
    fail: bool = False

    async def deliver(self, *, account: Identifier, text: str) -> bool:
        if self.fail:
            return False
        self.sent.append((account, text))
        return True


def _clock_holder(start=NOW):
    clock = [start]
    return clock, (lambda: clock[0])


@dataclass
class World:
    store: FakeBindingStore
    registry: FakeIdentityRegistry
    links: FakeLinkRequests
    services: ManagedServices
    holder: HolderLinkService


def _world(*, signal_delivery=None, clock=lambda: NOW, links=None):
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: (UNIT.id, False)},
        units={UNIT.id: WS.id},
        workspaces={WS.id},
    )
    registry = FakeIdentityRegistry()
    links = links if links is not None else FakeLinkRequests()
    services = ManagedServices(store, backend, topology, registry, links)
    holder = HolderLinkService(
        store, registry, links, services.publication, services.registration,
        signal_delivery=signal_delivery, clock=clock)
    return World(store, registry, links, services, holder)


async def _person(store, registry, person_id, ident, extra_idents=()):
    account = ManagedAccount(
        account_id=str(uuid4()), person_id=person_id,
        platform=ident.platform,
        identifiers=frozenset({ident, *extra_idents}),
        state=AccountState.BOUND,
    )
    outcome = await store.register(
        person=ManagedPerson(person_id=person_id, lifecycle=PersonLifecycle.ACTIVE),
        account=account, membership=None,
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    await registry.ensure_identity(external_id=person_id, traits={})


def _auth(account, *, method, proof_issuer, purpose="session", binding=None,
          authenticated_at=NOW):
    return AccountAuthentication(
        issuer=ISSUER, session_id=f"session-{uuid4()}", account=account,
        method=method, proof_issuer=proof_issuer,
        authenticated_at=authenticated_at, purpose=purpose, binding=binding)


def _discord_auth(**kw):
    return _auth(ACCT_I, method="discord_oauth", proof_issuer=DISCORD_CLIENT, **kw)


async def _requested(holder, *, request_id="synthetic-request"):
    result = await holder.request_link(
        _discord_auth(), request_id=request_id, receiving=ACCT_R,
        surviving_person=PERSON_A)
    assert isinstance(result, HolderLinkView)
    return result


async def _approve_discord(holder, request_id, nonce, *, authenticated_at=NOW):
    return await holder.decide(
        _discord_auth(purpose="sign_in", authenticated_at=authenticated_at),
        operation_id=f"op-{uuid4()}", request_id=request_id,
        action="approve", nonce=nonce)


async def _signal_decide(holder, request_id, nonce, *, approve=True,
                         sender=frozenset({ACCT_R, ACCT_R_PHONE}), ts=None):
    ts = ts if ts is not None else int(NOW.timestamp() * 1000) + 1_000
    return await holder.decide_from_transport(
        sender=sender, issuer=DAEMON, timestamp_ms=ts,
        request_id=request_id, nonce=nonce, approve=approve)


async def test_request_persists_the_bound_request_and_pushes_the_signal_challenge():
    delivery = FakeDelivery()
    w = _world(signal_delivery=delivery)
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R, extra_idents={ACCT_R_PHONE})

    view = await _requested(w.holder)
    assert view.side is LinkSide.INITIATING and view.state.value == "pending"
    assert view.pair.initiating == ACCT_I and view.pair.receiving == ACCT_R
    assert view.surviving_person == PERSON_A and not view.approved

    # The signal side got ONE private push at its exact bound identifier,
    # carrying pair, survivor, request id and its OWN nonce — not the
    # initiator's.
    receiving_nonce = await w.links.challenge(view.request_id, side=LinkSide.RECEIVING)
    assert receiving_nonce and receiving_nonce != view.nonce
    assert [account for account, _ in delivery.sent] == [ACCT_R]
    text = delivery.sent[0][1]
    assert view.request_id in text and receiving_nonce in text
    assert view.nonce not in text
    assert f"!!!link approve {view.request_id} {receiving_nonce}" in text
    assert PERSON_A in text


async def test_view_is_per_side_and_never_shows_the_other_challenge():
    w = _world()
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)
    await _person(w.store, w.registry, PERSON_C, ACCT_X)
    view = await _requested(w.holder)

    received = await w.holder.view_link(
        _auth(ACCT_R, method="signal_code", proof_issuer=DAEMON),
        request_id=view.request_id)
    assert isinstance(received, HolderLinkView)
    assert received.side is LinkSide.RECEIVING
    assert received.nonce != view.nonce

    foreign = await w.holder.view_link(
        _auth(ACCT_X, method="telegram_oidc", proof_issuer="synthetic-tg"),
        request_id=view.request_id)
    assert isinstance(foreign, Outcome) and foreign.status is OutcomeStatus.DENIED


async def test_challenge_survives_a_service_restart_through_the_store():
    w = _world()
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)
    view = await _requested(w.holder)

    restarted = HolderLinkService(
        w.store, w.registry, w.links, w.services.publication, w.services.registration)
    again = await restarted.view_link(_discord_auth(), request_id=view.request_id)
    assert isinstance(again, HolderLinkView) and again.nonce == view.nonce


async def test_request_denied_for_an_unbound_pair_a_non_owner_survivor_or_an_unsupported_platform():
    w = _world()
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_C, ACCT_X)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)

    unbound = await w.holder.request_link(
        _discord_auth(), request_id="r1",
        receiving=Identifier("signal", "uuid", "unbound"), surviving_person=PERSON_A)
    assert isinstance(unbound, Outcome) and unbound.status is OutcomeStatus.DENIED

    not_owner = await w.holder.request_link(
        _discord_auth(), request_id="r2", receiving=ACCT_R,
        surviving_person=PERSON_C)
    assert isinstance(not_owner, Outcome) and not_owner.status is OutcomeStatus.DENIED

    unsupported = await w.holder.request_link(
        _discord_auth(), request_id="r3",
        receiving=Identifier("instagram", "igid", "someone"),
        surviving_person=PERSON_A)
    assert isinstance(unsupported, Outcome)
    assert unsupported.status is OutcomeStatus.DENIED


async def test_a_session_or_code_authentication_can_never_be_consent():
    w = _world()
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)
    view = await _requested(w.holder)
    nonce = await w.links.challenge(view.request_id, side=LinkSide.INITIATING)

    retained = await w.holder.decide(
        _discord_auth(purpose="session"),
        operation_id="op-1", request_id=view.request_id,
        action="approve", nonce=nonce)
    assert retained.outcome.status is OutcomeStatus.DENIED
    assert retained.outcome.detail == "fresh-authentication-required"

    code_auth = await w.holder.decide(
        _auth(ACCT_R, method="signal_code", proof_issuer=DAEMON,
              purpose="sign_in"),
        operation_id="op-2", request_id=view.request_id,
        action="approve", nonce="whatever")
    assert code_auth.outcome.status is OutcomeStatus.DENIED
    assert code_auth.outcome.detail == "consent-surface-required"

    assert not (await w.links.get(view.request_id)).approved


async def test_authentication_older_than_the_request_cannot_approve_it():
    w = _world()
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)
    view = await _requested(w.holder)
    nonce = await w.links.challenge(view.request_id, side=LinkSide.INITIATING)

    stale = await _approve_discord(
        w.holder, view.request_id, nonce,
        authenticated_at=NOW - timedelta(minutes=1))
    assert stale.outcome.status is OutcomeStatus.DENIED


async def test_one_side_does_not_publish_and_both_sides_commit_through_publication():
    clock, now = _clock_holder()
    w = _world(clock=now)
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R, extra_idents={ACCT_R_PHONE})
    view = await _requested(w.holder)
    nonces = {side: await w.links.challenge(view.request_id, side=side)
              for side in LinkSide}

    first = await _approve_discord(w.holder, view.request_id,
                                   nonces[LinkSide.INITIATING])
    assert first.outcome.status is OutcomeStatus.COMPLETE
    # One holder's approval changes nothing — no publication happened.
    assert (await w.store.record_for(account=ACCT_R)).person_id == PERSON_B

    clock[0] = NOW + timedelta(seconds=10)
    second = await _signal_decide(w.holder, view.request_id,
                                  nonces[LinkSide.RECEIVING])
    assert second.status is OutcomeStatus.COMPLETE
    link = await w.links.get(view.request_id)
    assert link.state.value == "committed"
    # The central publication actually consolidated ownership and left the
    # historical alias resolvable.
    assert (await w.store.record_for(account=ACCT_R)).person_id == PERSON_A
    assert await w.store.account_for(
        platform=ACCT_R.platform, kind=ACCT_R.kind, value=ACCT_R.value) is not None


async def test_a_wrong_nonce_or_forwarded_challenge_is_refused_without_side_effects():
    clock, now = _clock_holder()
    w = _world(clock=now)
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)
    view = await _requested(w.holder)

    wrong = await _approve_discord(w.holder, view.request_id, "not-the-nonce")
    assert wrong.outcome.status is OutcomeStatus.DENIED
    assert (await w.links.get(view.request_id)).approved == frozenset()

    # The initiator's nonce under the receiving account: a forwarded
    # challenge can never speak for the other holder.
    clock[0] = NOW + timedelta(seconds=10)
    forwarded = await _signal_decide(w.holder, view.request_id, view.nonce)
    assert forwarded.status is OutcomeStatus.DENIED
    assert not (await w.links.get(view.request_id)).approved


async def test_refusal_and_cancellation_leave_the_accounts_unlinked():
    clock, now = _clock_holder()
    w = _world(clock=now)
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)

    refused = await _requested(w.holder, request_id="refused-request")
    nonce = await w.links.challenge(refused.request_id, side=LinkSide.RECEIVING)
    clock[0] = NOW + timedelta(seconds=10)
    answer = await _signal_decide(w.holder, refused.request_id, nonce, approve=False)
    assert answer.status is OutcomeStatus.COMPLETE
    assert (await w.links.get(refused.request_id)).state.value == "refused"
    assert (await w.store.record_for(account=ACCT_R)).person_id == PERSON_B

    clock[0] = NOW
    cancelled = await _requested(w.holder, request_id="cancelled-request")
    nonce = await w.links.challenge(cancelled.request_id, side=LinkSide.INITIATING)
    result = await w.holder.decide(
        _discord_auth(purpose="sign_in"),
        operation_id="op-cancel", request_id=cancelled.request_id,
        action="cancel", nonce=nonce)
    assert result.outcome.status is OutcomeStatus.COMPLETE
    assert (await w.links.get(cancelled.request_id)).state.value == "cancelled"


async def test_expiry_denies_late_decisions_and_publishes_nothing():
    clock, now = _clock_holder()
    w = _world(clock=now)
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)
    view = await _requested(w.holder)
    nonce = await w.links.challenge(view.request_id, side=LinkSide.INITIATING)

    clock[0] = NOW + timedelta(hours=1)
    late = await _approve_discord(
        w.holder, view.request_id, nonce, authenticated_at=NOW + timedelta(minutes=5))
    assert late.outcome.status is OutcomeStatus.DENIED
    assert (await w.store.record_for(account=ACCT_R)).person_id == PERSON_B


async def test_transport_sender_must_resolve_to_one_bound_account_in_the_pair():
    w = _world()
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)
    await _person(w.store, w.registry, PERSON_C,
                  Identifier("signal", "uuid", "synthetic-stranger"))
    view = await _requested(w.holder)
    nonce = await w.links.challenge(view.request_id, side=LinkSide.RECEIVING)

    unbound = await _signal_decide(
        w.holder, view.request_id, nonce,
        sender=frozenset({Identifier("signal", "phone", "nobody-bound")}))
    assert unbound.status is OutcomeStatus.DENIED
    assert unbound.detail == "sender-unbound"

    not_party = await _signal_decide(
        w.holder, view.request_id, nonce,
        sender=frozenset({Identifier("signal", "uuid", "synthetic-stranger")}))
    assert not_party.status is OutcomeStatus.DENIED
    assert not_party.detail == "not-a-party"


async def test_conflicting_sender_aliases_fail_closed():
    w = _world()
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)
    await _person(w.store, w.registry, PERSON_C,
                  Identifier("signal", "phone", "synthetic-other-number"))
    view = await _requested(w.holder)
    nonce = await w.links.challenge(view.request_id, side=LinkSide.RECEIVING)

    conflict = await _signal_decide(
        w.holder, view.request_id, nonce,
        sender=frozenset({ACCT_R, Identifier("signal", "phone", "synthetic-other-number")}))
    assert conflict.status is OutcomeStatus.DENIED
    assert conflict.detail == "sender-conflict"


async def test_holder_exact_unlink_through_the_central_service():
    w = _world()
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)

    wrong_person = await w.holder.unlink(
        _auth(ACCT_R, method="signal_code", proof_issuer=DAEMON),
        operation_id="op-u1", expected_person=PERSON_A)
    assert wrong_person.status is OutcomeStatus.DENIED

    done = await w.holder.unlink(
        _auth(ACCT_R, method="signal_code", proof_issuer=DAEMON),
        operation_id="op-u2", expected_person=PERSON_B)
    assert done.status is OutcomeStatus.COMPLETE
    assert await w.store.record_for(account=ACCT_R) is None


async def test_a_retained_binding_that_drifted_cannot_act():
    w = _world()
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)

    stale = _discord_auth(binding=SessionBinding(PERSON_A, "999", "acct", "999"))
    denied_view = await w.holder.request_link(
        stale, request_id="r1", receiving=ACCT_R, surviving_person=PERSON_A)
    assert isinstance(denied_view, Outcome)
    assert denied_view.status is OutcomeStatus.DENIED


async def test_file_backend_refuses_managed_mutations_honestly():
    w = _world(links=FileLinkRequests())
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)

    outcome = await w.holder.request_link(
        _discord_auth(), request_id="r1", receiving=ACCT_R,
        surviving_person=PERSON_A)
    assert isinstance(outcome, Outcome)
    assert outcome.status is OutcomeStatus.DENIED
    assert "operator-maintained" in outcome.detail


async def test_delivery_failure_leaves_the_request_and_its_pull_view_intact():
    delivery = FakeDelivery(fail=True)
    w = _world(signal_delivery=delivery)
    await _person(w.store, w.registry, PERSON_A, ACCT_I)
    await _person(w.store, w.registry, PERSON_B, ACCT_R)

    view = await _requested(w.holder)
    assert isinstance(view, HolderLinkView)
    received = await w.holder.view_link(
        _auth(ACCT_R, method="signal_code", proof_issuer=DAEMON),
        request_id=view.request_id)
    assert isinstance(received, HolderLinkView) and received.nonce
