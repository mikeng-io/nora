"""A room's people and a due row's fire-time re-check read the selected managed
authority, not a file roster."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from nora.agent.room import Room
from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    PersonLifecycle,
    room_scope_id,
)
from nora.domain.management import Scope, ScopeKind
from nora.domain.reminders import Reminder, ReminderState
from nora.people.app.managed import ManagedServices
from nora.testing import FakeRosterSource
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from tests.unit.test_scoped_engagement_names import (
    _M_PERSON,
    _M_ROOM,
    _M_SIBLING,
    _M_UNIT,
    _M_WS,
    _managed,
    _managed_person,
)

_T0 = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
_FOREIGN_UNIT = Scope(ScopeKind.UNIT, "generated-foreign-unit")
_S_UNIT = Scope(ScopeKind.UNIT, "generated-sibling-unit")
_S_ROOM = Scope(ScopeKind.ROOM, room_scope_id("signal", "g9"))
_S_PERSON = "generated-sibling-member"


def _room(access, tagger=None, members=("+852111",)):
    return Room(platform="signal", conversation_id="g1", access=access, tagger=tagger,
                roster_source=FakeRosterSource(groups={"g1": (frozenset(members),)}))


def _due(person_id: str, text: str) -> Reminder:
    return Reminder(
        id=uuid.uuid4(), requester_person_id=person_id, platform="signal",
        conversation_id="g1", conversation_type="group", text=text,
        created_at=_T0, due_at=_T0 + timedelta(hours=2), state=ReminderState.PENDING,
        fired_at=None, outcome=None,
    )


async def test_the_room_names_its_member_by_the_room_scopes_nickname():
    bindings, access = _managed()
    await _managed_person(bindings)
    for scope, nickname in (
            (_M_ROOM, "Generated Room Nick"),
            (_FOREIGN_UNIT, "Generated Foreign Unit Nick")):
        assert (await bindings.set_scoped_name(
            person_id=_M_PERSON, scope=scope, nickname=nickname,
            operation_id=f"generated-name-{scope.id}")).status.value == "complete"

    [tagger] = (await _room(access).members()).people
    assert tagger.person_id == _M_PERSON
    # The room's ancestry answers its own nick; a foreign unit's never substitutes.
    assert tagger.name == "Generated Room Nick"


async def test_an_unmapped_sender_names_nobody_and_is_counted_unnamed():
    _, access = _managed()
    view = await _room(access, members=("+852999",)).members()
    assert view.people == () and view.unnamed == 1


async def test_a_member_only_of_a_sibling_unit_is_nobody_in_this_room():
    """Admitted in a sibling unit's room, nobody here: the selected
    authority's member view for this room excludes them, and the room's own
    resolve set cannot name them."""
    bindings, _ = _managed()
    access = ManagedServices(bindings, FakeAuthorityBackend(), FakeTopologySource(
        rooms={_M_ROOM.id: (_M_UNIT.id, False), _M_SIBLING.id: (_M_UNIT.id, False),
               _S_ROOM.id: (_S_UNIT.id, False)},
        units={_M_UNIT.id: _M_WS.id, _S_UNIT.id: _M_WS.id},
        workspaces={_M_WS.id}), FakeIdentityRegistry(), FakeLinkRequests()).access
    await _managed_person(bindings)
    await bindings.register(
        person=ManagedPerson(_S_PERSON, PersonLifecycle.ACTIVE,
                             display_name="Generated Sibling Member"),
        account=ManagedAccount(
            "generated-sibling-binding", _S_PERSON, "signal",
            frozenset({Identifier("signal", "phone", "+852222")}),
            AccountState.BOUND),
        membership=ManagedMembership(
            _S_PERSON, _S_ROOM, MembershipState.MEMBER, "generated"))

    # The membership is real — in the sibling unit's room, where it grants.
    assert [m.person_id for m in await access.members_of(_S_ROOM)] == [_S_PERSON]
    assert not (await access.check(
        scope=_M_ROOM, person_id=_S_PERSON, permit="view")).allowed
    assert [r.person_id for r in await access.room_refs("signal", "g1")] == [_M_PERSON]

    # The room's roster holds only the admitted member — the bound sibling
    # resolves as nobody in this room.
    view = await _room(access).members()
    assert [p.person_id for p in view.people] == [_M_PERSON]
    assert view.unnamed == 0 and view.find("Generated Sibling Member") == ()
