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
