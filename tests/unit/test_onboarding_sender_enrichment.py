"""`PlatformOnboarding` first-encounter sender notes → grouped account
enrichment, driven through the runtime route (`notify_sender` → `_drain`
→ real `SignalDiscovery` → `RegistrationService`; `identity.md` «One
person, many accounts»):

- a sender whose presented value already resolves is still observed as
  the whole provider-proven group — a pre-existing phone-only account
  gains the paired uuid on the SAME account and person;
- a sender first provable phone-only and later re-observed with the pair
  is enriched in the same runtime — nothing remembers the earlier,
  smaller group, and an identical repeat replays the journal instead of
  growing anything;
- a group split across accounts is refused through the same route —
  even two accounts of one canonical person, and a revoked identifier's
  reservation is never re-pointed;
- unprovable discovery, an out-of-source answer or an unavailable
  registration leaves no durable mark — a later valid note retries;
- sender enrichment never grants membership or authority.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    CollectionSnapshot,
    ManagedAccount,
    ManagedPerson,
    ObservedMember,
    OperationJournalState,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.people.app.managed import ManagedServices
from nora.platform.signal.discovery import SignalDiscovery
from nora.testing.discovery import ScriptedDiscovery
from nora.testing.managed import FakeBindingStore
from tests.unit.test_platform_onboarding import (
    GROUP,
    GUILD,
    SERVICE,
    UNIT,
    _discord,
    _graph,
    _onboarding,
    _rows,
    _rule,
    guild_member,
    signal_member,
)

MEMBER = signal_member(1)
PHONE = Identifier("signal", "phone", MEMBER["number"])
ACI = Identifier("signal", "uuid", MEMBER["uuid"])
PERSON = "person_generated"


def _signal_onboarding(services: ManagedServices, provisioning, rows: list):
    """A Signal rule whose scripted `fetch` reads the mutable `rows`."""
    calls = {"n": 0}

    async def fetch(collection: str):
        calls["n"] += 1
        return _rows(list(rows))

    onboarding = _onboarding(
        services, provisioning,
        _rule(SignalDiscovery(fetch=fetch, own_account=""),
              collection=GROUP, platform="signal"))
    return onboarding, calls


async def _bind(
    store: FakeBindingStore, person_id: str,
    identifiers: frozenset[Identifier], *,
    revoked: bool = False,
) -> ManagedAccount:
    """Seed one independent account through the shared store contract."""
    account = ManagedAccount(
        account_id=str(uuid4()), person_id=person_id, platform="signal",
        identifiers=frozenset(identifiers), state=AccountState.BOUND)
    outcome = await store.register(
        person=ManagedPerson(
            person_id=person_id, lifecycle=PersonLifecycle.ACTIVE),
        account=account, membership=None)
    assert outcome.status is OutcomeStatus.COMPLETE
    if revoked:
        await store.revoke_binding(account=next(iter(identifiers)))
    return account


def _sender_ops(store: FakeBindingStore):
    return [o for o in store.operations.values()
            if o.operation_id.startswith("onboard-sender:")]


@pytest.mark.parametrize("sender", [PHONE.value, ACI.value], ids=["known-phone", "new-uuid"])
async def test_a_bound_senders_note_still_enriches_the_same_account(sender):
    """Either sender form enriches the phone-only account with the paired
    uuid, without changing the account/person or granting membership."""
    services, provisioning = _graph()
    rows: list[dict] = []
    onboarding, _ = _signal_onboarding(services, provisioning, rows)
    await onboarding._drain()  # empty roster — nothing registers
    bound = await _bind(services.bindings, PERSON, {PHONE})
    rows.append(MEMBER)

    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=sender)
    await onboarding._drain()

    for ident in (PHONE, ACI):
        record = await services.bindings.record_for(account=ident)
        assert record is not None
        assert record.account_id == bound.account_id
        assert record.person_id == bound.person_id
    assert len(services.bindings.people) == 1
    assert len(services.bindings.accounts) == 1
    assert await services.bindings.members_of(UNIT) == ()


async def test_sending_the_newly_seen_identifier_enriches_while_only_the_other_resolves():
    """An already-bound uuid identifies the account even when the selected
    group target is its unbound phone."""
    services, provisioning = _graph()
    rows: list[dict] = []
    onboarding, _ = _signal_onboarding(services, provisioning, rows)
    await onboarding._drain()
    bound = await _bind(services.bindings, PERSON, {ACI})
    rows.append(MEMBER)

    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=ACI.value)
    await onboarding._drain()

    for ident in (PHONE, ACI):
        record = await services.bindings.record_for(account=ident)
        assert record is not None
        assert record.account_id == bound.account_id
    assert len(services.bindings.accounts) == 1
    assert len(services.bindings.people) == 1


async def test_a_sender_reobserved_with_a_larger_group_is_enriched():
    """Same runtime: a sender first provable only by shape (phone) becomes
    a phone+uuid pair once the roster knows the row — the second note
    enriches the account the first created, never a duplicate person."""
    services, provisioning = _graph()
    rows: list[dict] = []
    onboarding, _ = _signal_onboarding(services, provisioning, rows)
    await onboarding._drain()

    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=PHONE.value)
    await onboarding._drain()
    first = await services.bindings.record_for(account=PHONE)
    assert first is not None and first.identifiers == frozenset({PHONE})

    rows.append(MEMBER)
    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=PHONE.value)
    await onboarding._drain()

    for ident in (PHONE, ACI):
        record = await services.bindings.record_for(account=ident)
        assert record is not None
        assert record.account_id == first.account_id
        assert record.person_id == first.person_id
    assert len(services.bindings.people) == 1
    assert len(services.bindings.accounts) == 1


async def test_duplicate_sender_notes_in_one_drain_coalesce():
    """The same note twice, plus the pair's other form, queue two entries
    in one drain — the second apply replays the first's journal row."""
    services, provisioning = _graph()
    rows: list[dict] = []
    onboarding, calls = _signal_onboarding(services, provisioning, rows)
    await onboarding._drain()
    assert calls["n"] == 1
    rows.append(MEMBER)

    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=PHONE.value)
    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=PHONE.value)
    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=ACI.value)
    await onboarding._drain()

    assert calls["n"] == 3  # roster read + two distinct queued senders
    account = await services.bindings.record_for(account=ACI)
    assert account is not None
    assert account.identifiers == frozenset({PHONE, ACI})
    assert len(services.bindings.people) == 1
    assert len(services.bindings.accounts) == 1
    ops = _sender_ops(services.bindings)
    assert len(ops) == 1 and ops[0].state is OperationJournalState.COMPLETE


async def test_a_repeated_completed_group_replays_without_growth():
    """A completed group's identical repeat replays its journal row —
    people, accounts, identifiers and revisions do not move, and no
    grant or membership is restored."""
    services, provisioning = _graph()
    rows: list[dict] = []
    onboarding, _ = _signal_onboarding(services, provisioning, rows)
    await onboarding._drain()
    rows.append(MEMBER)

    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=PHONE.value)
    await onboarding._drain()
    account = await services.bindings.record_for(account=ACI)
    assert account is not None
    op_count = len(services.bindings.operations)

    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=PHONE.value)
    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=ACI.value)
    await onboarding._drain()

    still = await services.bindings.record_for(account=ACI)
    assert still is not None and still.revision == account.revision
    assert still.identifiers == account.identifiers
    assert len(services.bindings.people) == 1
    assert len(services.bindings.accounts) == 1
    assert len(services.bindings.operations) == op_count
    assert await services.bindings.members_of(UNIT) == ()


async def test_a_pair_resolving_to_two_accounts_is_refused_even_on_one_person():
    """The account-exact boundary through the runtime route: the pair's
    identifiers already live on two accounts of ONE canonical person —
    nothing is moved, merged or partially bound."""
    services, provisioning = _graph()
    rows: list[dict] = []
    onboarding, _ = _signal_onboarding(services, provisioning, rows)
    await onboarding._drain()
    one = await _bind(services.bindings, PERSON, {PHONE})
    two = await _bind(services.bindings, PERSON, {ACI})
    rows.append(MEMBER)

    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=PHONE.value)
    await onboarding._drain()

    for ident, account in ((PHONE, one), (ACI, two)):
        record = await services.bindings.record_for(account=ident)
        assert record is not None
        assert record.account_id == account.account_id
        assert record.identifiers == account.identifiers
    assert len(services.bindings.people) == 1
    assert len(services.bindings.accounts) == 2
    assert _sender_ops(services.bindings) == []
    assert await services.bindings.members_of(UNIT) == ()


async def test_a_revoked_identifiers_reservation_stays_fail_closed():
    """A revoked binding keeps its ownership reserved: the live account on
    the same person cannot absorb the retired identifier either."""
    services, provisioning = _graph()
    rows: list[dict] = []
    onboarding, _ = _signal_onboarding(services, provisioning, rows)
    await onboarding._drain()
    await _bind(services.bindings, PERSON, {PHONE}, revoked=True)
    live = await _bind(services.bindings, PERSON, {ACI})
    rows.append(MEMBER)

    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=ACI.value)
    await onboarding._drain()

    assert await services.bindings.record_for(account=PHONE) is None
    record = await services.bindings.record_for(account=ACI)
    assert record is not None
    assert record.account_id == live.account_id
    assert record.identifiers == live.identifiers
    assert len(services.bindings.people) == 1
    assert len(services.bindings.accounts) == 2


async def test_an_unavailable_registration_leaves_no_mark_and_a_retry_enriches():
    """A publication hold makes the observation unavailable: nothing is
    journaled or remembered, and a later valid note enriches the SAME
    account — the retry proves the route actually ran."""
    services, provisioning = _graph()
    rows: list[dict] = []
    onboarding, _ = _signal_onboarding(services, provisioning, rows)
    await onboarding._drain()
    bound = await _bind(services.bindings, PERSON, {PHONE})
    rows.append(MEMBER)
    await services.bindings.hold_identity(
        person_id=PERSON, operation_id="generated-hold")

    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=PHONE.value)
    await onboarding._drain()
    assert await services.bindings.record_for(account=ACI) is None
    assert _sender_ops(services.bindings) == []

    await services.bindings.release_identity_hold(
        person_id=PERSON, operation_id="generated-hold")
    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=PHONE.value)
    await onboarding._drain()

    record = await services.bindings.record_for(account=ACI)
    assert record is not None and record.account_id == bound.account_id
    ops = _sender_ops(services.bindings)
    assert len(ops) == 1 and ops[0].state is OperationJournalState.COMPLETE


async def test_an_interrupted_observation_resumes_on_the_next_note():
    """A registry outage mid-observation journals INCOMPLETE — the next
    note re-applies the same operation and finishes its pending steps
    instead of starting over or staying suppressed."""
    services, provisioning = _graph()
    rows: list[dict] = []
    onboarding, _ = _signal_onboarding(services, provisioning, rows)
    await onboarding._drain()
    rows.append(MEMBER)
    services.registry.down = True

    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=PHONE.value)
    await onboarding._drain()
    bound = await services.bindings.record_for(account=ACI)
    assert bound is not None  # the binding step committed
    ops = _sender_ops(services.bindings)
    assert len(ops) == 1
    assert ops[0].state is OperationJournalState.INCOMPLETE

    services.registry.down = False
    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP, sender_id=PHONE.value)
    await onboarding._drain()
    ops = _sender_ops(services.bindings)
    assert len(ops) == 1 and ops[0].state is OperationJournalState.COMPLETE
    person = await services.bindings.person(bound.person_id)
    assert person is not None and person.kratos_identity_id is not None
    assert len(services.bindings.people) == 1
    assert len(services.bindings.accounts) == 1


async def test_unprovable_or_out_of_source_sender_evidence_registers_nothing():
    """An untypable value proves nothing, a collection the rule does not
    cover queues nothing, and a discovery answer outside the service's
    approved sources is refused — a valid note afterwards still runs."""
    services, provisioning = _graph()
    rows: list[dict] = []
    onboarding, _ = _signal_onboarding(services, provisioning, rows)
    await onboarding._drain()
    rows.append(MEMBER)

    onboarding.notify_sender(
        platform="signal", conversation_id=GROUP,
        sender_id="descriptive-only")
    onboarding.notify_sender(
        platform="signal", conversation_id="other-group",
        sender_id=PHONE.value)
    await onboarding._drain()
    assert not services.bindings.people

    class ForeignDiscovery(ScriptedDiscovery):
        async def sender_identifiers(self, *, collection, value):
            if value == "777":
                return frozenset(
                    {Identifier("telegram", "handle", "generated")})
            return await super().sender_identifiers(
                collection=collection, value=value)

    foreign = _onboarding(
        services, provisioning,
        _rule(ForeignDiscovery(
            CollectionSnapshot(available=False, detail="down"),
            collection=GUILD)))
    foreign.notify_sender(
        platform="discord", conversation_id=f"{GUILD}/room",
        sender_id="777")
    await foreign._drain()
    assert not services.bindings.people

    foreign.notify_sender(
        platform="discord", conversation_id=f"{GUILD}/room",
        sender_id="10001")
    await foreign._drain()
    assert len(services.bindings.people) == 1


@pytest.mark.parametrize("classification", ["person", "bot", "unknown"])
async def test_roster_and_sender_preserve_one_account_without_a_nature_gate(
    classification,
):
    services, provisioning = _graph()
    account = Identifier("discord", "user_id", "10002")
    discovery = ScriptedDiscovery(CollectionSnapshot(
        available=True, complete=True, observed_ms=int(time.time() * 1000),
        members=(ObservedMember(frozenset({account}), classification),)),
        collection=GUILD)
    onboarding = _onboarding(services, provisioning, _rule(discovery))
    onboarding.notify_sender(platform="discord",
        conversation_id=f"{GUILD}/generated-room", sender_id=account.value)
    await onboarding._drain()
    original = await services.bindings.record_for(account=account)
    assert original is not None
    members = await services.bindings.members_of(UNIT)
    assert [m.person_id for m in members] == [original.person_id]
    assert members[0].sources == {f'sync:["generated-bridge","{GUILD}"]'}
    onboarding.notify_sender(platform="discord",
        conversation_id=f"{GUILD}/generated-room", sender_id=account.value)
    await onboarding._drain()
    current = await services.bindings.record_for(account=account)
    assert current.account_id == original.account_id
    assert current.person_id == original.person_id
    assert len(services.bindings.people) == len(services.registry.identities) == 1
    assert not (await services.authority.check(scope=UNIT,
        person_id=original.person_id, permit="manage_members")).allowed

async def test_an_unproven_account_is_unavailable_before_any_write():
    services, provisioning = _graph()
    result = await provisioning.apply(operation_id="generated-unproven",
        actor=SERVICE, scope=UNIT, collection=GUILD,
        members=(ObservedMember(frozenset(), "unknown"),),
        complete_roster=True, observed_ms=int(time.time() * 1000))
    assert result.status.value == "unavailable"
    assert result.detail == "unproven-account"
    assert not services.bindings.people and not services.bindings.operations
    discovery, _ = _discord([guild_member(1)])
    await _onboarding(services, provisioning, _rule(discovery))._drain()
    assert len(services.bindings.people) == 1
