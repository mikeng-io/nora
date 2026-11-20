"""Room-management target resolution as one guarded boundary.

- a registered sender without `manage_members` gets one refusal for every
  target state — bound, unbound, unknown or ambiguous — and performs no
  target-specific read;
- every supplied mention/account reference resolves or the request
  clarifies — a known-plus-unknown set never mutates the known half;
- the resolved target's provenance is re-read inside the mutation gate —
  a binding or identity that moved while the invocation waited refuses,
  and nothing mutates the stale person.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager

from nora.domain.identity import BoundAccount, Identifier
from nora.domain.managed import ManagedMembership, OutcomeStatus
from nora.domain.management import Scope
from nora.people import Clarify, RoomManagement, RoomRequest
from nora.testing.managed import FakeBindingStore
from tests.unit.test_room_management import (
    ALICE_ID,
    ALICE_PERSON,
    OP_ID,
    OP_PERSON,
    ROOM,
    TARGET_ID,
    TARGET_PERSON,
    _member_phone,
    _message,
    _operator,
    _person,
    _services,
)


class _ProbingStore(FakeBindingStore):
    """Records the reads target resolution performs — `resolve` for
    mention/account references, `members_of` for name lookups — so an
    unauthorized caller can be shown to have touched none of them."""

    def __init__(self) -> None:
        super().__init__()
        self.target_reads: list[str] = []

    async def resolve(self, platform: str, value: str) -> BoundAccount | None:
        if value != OP_ID:
            self.target_reads.append(value)
        return await super().resolve(platform, value)

    async def members_of(self, scope: Scope) -> tuple[ManagedMembership, ...]:
        self.target_reads.append(f"members:{scope.id}")
        return await super().members_of(scope)


class _HoldableStore(FakeBindingStore):
    """Holds the mutation gate on demand — `hold` makes the next gate
    acquisition announce `gate_entered` then wait on `gate_release`, so a
    test can move a binding while an invocation queues for the gate."""

    def __init__(self) -> None:
        super().__init__()
        self.hold = False
        self.gate_entered = asyncio.Event()
        self.gate_release = asyncio.Event()

    def mutation(self) -> AbstractAsyncContextManager[None]:
        self._up()
        if not self.hold:
            return self._mutation_lock
        entered = self.gate_entered
        release = self.gate_release
        lock = self._mutation_lock

        class _Gate(AbstractAsyncContextManager[None]):
            async def __aenter__(self) -> None:
                entered.set()
                await release.wait()
                await lock.acquire()

            async def __aexit__(self, *exc: object) -> None:
                lock.release()

        return _Gate()


# -- authorization precedes every target read ----------------------------


async def test_an_unauthorized_sender_gets_one_refusal_and_no_target_read():
    probing = _ProbingStore()
    bindings, backend, _, registry, services = _services(probing)
    # A registered sender with no manage_members grant meets the same
    # refusal whatever the target looks like — resolution run before
    # authorization is an oracle for every one of these states.
    await _person(bindings, registry, OP_PERSON, OP_ID)
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    management = RoomManagement(services)
    cases = (
        (_message("!!!member add", mentions=frozenset({TARGET_ID})),
         RoomRequest(kind="add")),
        (_message("!!!member add", mentions=frozenset({ALICE_ID})),
         RoomRequest(kind="add")),
        (_message("!!!member add alice"),
         RoomRequest(kind="add", reference="alice",
                     reference_kind="name")),
        (_message("!!!member remove",
                  mentions=frozenset({TARGET_ID, ALICE_ID})),
         RoomRequest(kind="remove")),
        (_message("!!!member register", mentions=frozenset({ALICE_ID})),
         RoomRequest(kind="register")),
    )
    refusal = None
    for message, request in cases:
        probing.target_reads.clear()
        outcome = await management.invoke(message, request)
        assert outcome.status is OutcomeStatus.DENIED
        assert outcome.detail == "operator-not-authorized"
        assert probing.target_reads == []
        text = management.render(outcome)
        refusal = text if refusal is None else refusal
        assert text == refusal
    assert len(bindings.people) == 2

    # An unavailable authority stays unavailable, never a guess either way.
    backend.down = True
    outcome = await management.invoke(cases[0][0], cases[0][1])
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "authority-unavailable"
    backend.down = False

    # Positive control: with the grant the same sender reaches the write.
    await backend.put_relation(
        scope=ROOM, relation="grant_manage_members", person_id=OP_PERSON
    )
    probing.target_reads.clear()
    outcome = await management.invoke(cases[0][0], cases[0][1])
    assert outcome.status is OutcomeStatus.COMPLETE
    assert await _member_phone(bindings, TARGET_PERSON)
    assert TARGET_ID in probing.target_reads


async def test_an_unverifiable_identity_gets_one_refusal_and_no_target_read():
    """Full authentication, not just a live grant: a sender whose Kratos
    identity is inactive — or whose registry cannot answer — meets the
    SAME refusal whether the named target is bound, unbound or unknown,
    and resolution never runs. Revert the `authenticate` call and every
    one of these probes reaches a target read the grant still allows."""
    probing = _ProbingStore()
    bindings, backend, _, registry, services = _services(probing)
    await _operator(bindings, backend, registry)
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    management = RoomManagement(services)
    # The scoped grant stands — only the identity fails to authenticate.
    registry.identities[OP_PERSON]["state"] = "inactive"
    cases = (
        (_message("!!!member add", mentions=frozenset({TARGET_ID})),
         RoomRequest(kind="add")),
        (_message("!!!member add", mentions=frozenset({ALICE_ID})),
         RoomRequest(kind="add")),
        (_message("!!!member add alice"),
         RoomRequest(kind="add", reference="alice",
                     reference_kind="name")),
        (_message("!!!member register", mentions=frozenset({ALICE_ID})),
         RoomRequest(kind="register")),
    )
    refusal = None
    for message, request in cases:
        probing.target_reads.clear()
        outcome = await management.invoke(message, request)
        assert outcome.status is OutcomeStatus.DENIED
        assert outcome.detail == "authentication-changed"
        assert probing.target_reads == []
        text = management.render(outcome)
        refusal = text if refusal is None else refusal
        assert text == refusal
    # And a registry that cannot answer is unavailable, never a guess.
    registry.identities[OP_PERSON]["state"] = "active"
    registry.down = True
    probing.target_reads.clear()
    outcome = await management.invoke(cases[0][0], cases[0][1])
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "authentication-unavailable"
    assert probing.target_reads == []
    registry.down = False
    # Positive control: the SAME sender, authenticated and authorized,
    # reaches the effect — the refusal was authentication, not the room.
    outcome = await management.invoke(cases[0][0], cases[0][1])
    assert outcome.status is OutcomeStatus.COMPLETE
    assert await _member_phone(bindings, TARGET_PERSON)


# -- every supplied reference resolves -----------------------------------


async def test_a_mixed_known_and_unknown_mention_clarifies_with_no_mutation():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    management = RoomManagement(services)
    # An unknown mention cannot be skipped so the known one mutates anyway.
    result = await management.invoke(
        _message(
            "!!!member add",
            mentions=frozenset({TARGET_ID, "+85299999990"}),
        ),
        RoomRequest(kind="add"),
    )
    assert isinstance(result, Clarify)
    assert result.status == "nothing_found"
    assert not await _member_phone(bindings, TARGET_PERSON)
    outcome = await management.invoke(
        _message(
            "!!!member add",
            mentions=frozenset({TARGET_ID}),
            timestamp=1_700_000_001_000,
        ),
        RoomRequest(kind="add"),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert await _member_phone(bindings, TARGET_PERSON)


async def test_a_register_mention_that_wont_type_clarifies_instead_of_skipping():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    management = RoomManagement(services)
    result = await management.invoke(
        _message(
            "!!!member register",
            mentions=frozenset({TARGET_ID, "not-an-account"}),
        ),
        RoomRequest(kind="register"),
    )
    assert isinstance(result, Clarify)
    assert result.status == "nothing_found"
    assert await bindings.resolve("signal", TARGET_ID) is None
    outcome = await management.invoke(
        _message(
            "!!!member register",
            mentions=frozenset({TARGET_ID}),
            timestamp=1_700_000_001_000,
        ),
        RoomRequest(kind="register"),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert await bindings.resolve("signal", TARGET_ID) is not None


async def test_an_explicit_account_and_mentions_resolve_as_one_set():
    """The explicit `account` a caller names and the message's own
    @-mentions are reconciled TOGETHER before any mutation: a repeated
    identifier deduplicates to the one intended target, a second distinct
    account clarifies rather than being picked over the mention, and an
    unknown mention beside a valid explicit account still asks."""
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    management = RoomManagement(services)

    # Explicit account + a DIFFERENT mention: two named accounts, no pick.
    result = await management.invoke(
        _message(
            "!!!member register " + TARGET_ID,
            mentions=frozenset({ALICE_ID}),
        ),
        RoomRequest(
            kind="register", reference=TARGET_ID,
            reference_kind="account",
        ),
    )
    assert isinstance(result, Clarify)
    assert result.status == "ambiguous"
    assert await bindings.resolve("signal", TARGET_ID) is None
    assert await bindings.resolve("signal", ALICE_ID) is None

    # A mention that cannot type at all beside a VALID explicit account
    # still asks — the explicit reference does not carry the request past
    # the unreadable one.
    result = await management.invoke(
        _message(
            "!!!member register " + TARGET_ID,
            mentions=frozenset({"not-an-account"}),
        ),
        RoomRequest(
            kind="register", reference=TARGET_ID,
            reference_kind="account",
        ),
    )
    assert isinstance(result, Clarify)
    assert result.status == "nothing_found"
    assert await bindings.resolve("signal", TARGET_ID) is None

    # The same account named twice — explicit AND mention — deduplicates
    # to the one intended target and registers it.
    outcome = await management.invoke(
        _message(
            "!!!member register " + TARGET_ID,
            mentions=frozenset({TARGET_ID}),
            timestamp=1_700_000_001_000,
        ),
        RoomRequest(
            kind="register", reference=TARGET_ID,
            reference_kind="account",
        ),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert await bindings.resolve("signal", TARGET_ID) is not None


async def test_an_explicit_target_and_mentions_resolve_as_one_set():
    """The membership path reconciles the same way: a mention and an
    explicit account naming DIFFERENT people clarify, a name and a
    mention naming the same person deduplicate, and an unknown mention
    beside a resolving name asks before any membership moves."""
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    await _person(
        bindings, registry, ALICE_PERSON, ALICE_ID,
        display_name="Alice",
    )
    await bindings.set_membership(
        person_id=ALICE_PERSON, scope=ROOM, member=True, source="delegation"
    )
    management = RoomManagement(services)

    # Explicit account + a mention naming ANOTHER person: clarify, never pick.
    result = await management.invoke(
        _message(
            "!!!member remove " + TARGET_ID,
            mentions=frozenset({ALICE_ID}),
        ),
        RoomRequest(
            kind="remove", reference=TARGET_ID,
            reference_kind="account",
        ),
    )
    assert isinstance(result, Clarify)
    assert result.status == "ambiguous"
    assert await _member_phone(bindings, ALICE_PERSON)

    # A name and a mention for the SAME person deduplicate to one target.
    outcome = await management.invoke(
        _message(
            "!!!member remove alice",
            mentions=frozenset({ALICE_ID}),
            timestamp=1_700_000_001_000,
        ),
        RoomRequest(
            kind="remove", reference="alice",
            reference_kind="name",
        ),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert not await _member_phone(bindings, ALICE_PERSON)

    # An unknown mention beside a valid name still asks — nothing mutates.
    await bindings.set_membership(
        person_id=ALICE_PERSON, scope=ROOM, member=True, source="delegation"
    )
    result = await management.invoke(
        _message(
            "!!!member remove alice",
            mentions=frozenset({"+85299999990"}),
            timestamp=1_700_000_002_000,
        ),
        RoomRequest(
            kind="remove", reference="alice",
            reference_kind="name",
        ),
    )
    assert isinstance(result, Clarify)
    assert result.status == "nothing_found"
    assert await _member_phone(bindings, ALICE_PERSON)


# -- the resolved target is re-read at commitment -------------------------


async def test_a_target_binding_moved_while_waiting_for_the_gate_refuses():
    held = _HoldableStore()
    bindings, backend, _, registry, services = _services(held)
    await _operator(bindings, backend, registry)
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    await _person(bindings, registry, ALICE_PERSON, ALICE_ID)
    management = RoomManagement(services)
    held.hold = True
    task = asyncio.create_task(
        management.invoke(
            _message("!!!member add", mentions=frozenset({TARGET_ID})),
            RoomRequest(kind="add"),
        )
    )
    await asyncio.wait_for(held.gate_entered.wait(), 5)
    # The resolved account's binding leaves while the invocation queues.
    await bindings.revoke_binding(
        account=Identifier(platform="signal", kind="phone", value=TARGET_ID)
    )
    held.gate_release.set()
    outcome = await asyncio.wait_for(task, 5)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "target-binding-changed"
    assert not await _member_phone(bindings, TARGET_PERSON)
    held.hold = False
    outcome = await management.invoke(
        _message(
            "!!!member add",
            mentions=frozenset({ALICE_ID}),
            timestamp=1_700_000_001_000,
        ),
        RoomRequest(kind="add"),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert await _member_phone(bindings, ALICE_PERSON)


async def test_a_target_identity_changed_while_waiting_for_the_gate_refuses():
    held = _HoldableStore()
    bindings, backend, _, registry, services = _services(held)
    await _operator(bindings, backend, registry)
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    management = RoomManagement(services)
    held.hold = True
    task = asyncio.create_task(
        management.invoke(
            _message("!!!member add", mentions=frozenset({TARGET_ID})),
            RoomRequest(kind="add"),
        )
    )
    await asyncio.wait_for(held.gate_entered.wait(), 5)
    # The resolved person's revision moves under the waiting invocation.
    await bindings.correlate_kratos(
        person_id=TARGET_PERSON, kratos_identity_id="kratos-target"
    )
    held.gate_release.set()
    outcome = await asyncio.wait_for(task, 5)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "stale-identity-revision"
    assert not await _member_phone(bindings, TARGET_PERSON)
    held.hold = False
    outcome = await management.invoke(
        _message(
            "!!!member add",
            mentions=frozenset({TARGET_ID}),
            timestamp=1_700_000_001_000,
        ),
        RoomRequest(kind="add"),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert await _member_phone(bindings, TARGET_PERSON)
