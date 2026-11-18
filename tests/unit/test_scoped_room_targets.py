"""Scoped identity names through the actual room targets callers."""

from __future__ import annotations

from nora.domain.managed import OutcomeStatus, room_scope_id
from nora.domain.management import Scope, ScopeKind
from nora.people import Clarify, RoomManagement, RoomRequest
from tests.unit.test_room_management import (
    ALICE_ID,
    ALICE_PERSON,
    ROOM,
    TARGET_ID,
    TARGET_PERSON,
    _member_phone,
    _message,
    _operator,
    _person,
    _services,
)

SIBLING_ROOM = Scope(ScopeKind.ROOM, room_scope_id("signal", "conv-elsewhere"))


async def test_a_room_nickname_targets_the_member_not_their_global_name():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    await _person(
        bindings, registry, ALICE_PERSON, ALICE_ID, display_name="Generated Global"
    )
    await bindings.set_membership(
        person_id=ALICE_PERSON, scope=ROOM, member=True, source="delegation"
    )
    assert (await bindings.set_scoped_name(
        person_id=ALICE_PERSON, scope=ROOM, nickname="Generated Room Nick",
        operation_id="generated-op-1")).status.value == "complete"
    management = RoomManagement(services)

    # The global name is not this room's name for them — the room's own
    # view renders the nickname, and only that resolves.
    missed = await management.invoke(
        _message("!!!member remove generated global"),
        RoomRequest(
            kind="remove", reference="generated global",
            reference_kind="name"),
    )
    assert isinstance(missed, Clarify)
    assert missed.status == "nothing_found"
    assert await _member_phone(bindings, ALICE_PERSON)

    outcome = await management.invoke(
        _message("!!!member remove generated room nick"),
        RoomRequest(
            kind="remove", reference="generated room nick",
            reference_kind="name"),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert not await _member_phone(bindings, ALICE_PERSON)


async def test_a_sibling_rooms_nickname_is_invisible_to_name_targeting():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    await _person(
        bindings, registry, ALICE_PERSON, ALICE_ID, display_name="Generated Global"
    )
    await bindings.set_membership(
        person_id=ALICE_PERSON, scope=ROOM, member=True, source="delegation"
    )
    assert (await bindings.set_scoped_name(
        person_id=ALICE_PERSON, scope=SIBLING_ROOM,
        nickname="Generated Sibling Nick",
        operation_id="generated-op-1")).status.value == "complete"
    management = RoomManagement(services)

    missed = await management.invoke(
        _message("!!!member remove generated sibling nick"),
        RoomRequest(
            kind="remove", reference="generated sibling nick",
            reference_kind="name"),
    )
    assert isinstance(missed, Clarify)
    assert missed.status == "nothing_found"
    assert await _member_phone(bindings, ALICE_PERSON)

    # Positive control: this room's own view — the global name — resolves.
    outcome = await management.invoke(
        _message("!!!member remove generated global"),
        RoomRequest(
            kind="remove", reference="generated global",
            reference_kind="name"),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert not await _member_phone(bindings, ALICE_PERSON)


async def test_two_members_sharing_a_room_nickname_still_clarifies():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    for person, phone in ((ALICE_PERSON, ALICE_ID), (TARGET_PERSON, TARGET_ID)):
        await _person(bindings, registry, person, phone)
        await bindings.set_membership(
            person_id=person, scope=ROOM, member=True, source="delegation"
        )
        assert (await bindings.set_scoped_name(
            person_id=person, scope=ROOM, nickname="Generated Room Nick",
            operation_id=f"generated-op-{person}")).status.value == "complete"
    management = RoomManagement(services)

    result = await management.invoke(
        _message("!!!member remove generated room nick"),
        RoomRequest(
            kind="remove", reference="generated room nick",
            reference_kind="name"),
    )
    assert isinstance(result, Clarify)
    assert result.status == "ambiguous"
    assert await _member_phone(bindings, ALICE_PERSON)
    assert await _member_phone(bindings, TARGET_PERSON)


async def test_an_unreadable_scoped_view_is_unavailable_never_a_guess():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    await _person(
        bindings, registry, ALICE_PERSON, ALICE_ID, display_name="Generated Global"
    )
    await bindings.set_membership(
        person_id=ALICE_PERSON, scope=ROOM, member=True, source="delegation"
    )
    assert (await bindings.set_scoped_name(
        person_id=ALICE_PERSON, scope=ROOM, nickname="Generated Room Nick",
        operation_id="generated-op-1")).status.value == "complete"
    management = RoomManagement(services)

    bindings.down = True
    try:
        outcome = await management.invoke(
            _message("!!!member remove generated room nick"),
            RoomRequest(
                kind="remove", reference="generated room nick",
                reference_kind="name"),
        )
    finally:
        bindings.down = False
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert await _member_phone(bindings, ALICE_PERSON)
