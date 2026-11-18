"""Scoped identity names through the actual engagement names callers."""

from __future__ import annotations

import pytest

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
from nora.domain.models import NormalizedMessage
from nora.engagement.context import TranscriptInjection
from nora.obs.metrics import Metrics
from nora.people.app.managed import ManagedServices
from nora.persons import Persons
from nora.testing import FakeRecordStore
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from tests.unit.test_engagement_context import FakeMemory, _request

_M_ROOM = Scope(ScopeKind.ROOM, room_scope_id("signal", "g1"))
_M_SIBLING = Scope(ScopeKind.ROOM, room_scope_id("signal", "g2"))
_M_UNIT = Scope(ScopeKind.UNIT, "generated-unit")
_M_WS = Scope(ScopeKind.WORKSPACE, "generated-workspace")
_M_PERSON = "generated-miles"


def _managed():
    """A selected managed backend: two sibling rooms of one unit."""
    bindings = FakeBindingStore()
    topology = FakeTopologySource(
        rooms={_M_ROOM.id: (_M_UNIT.id, False),
               _M_SIBLING.id: (_M_UNIT.id, False)},
        units={_M_UNIT.id: _M_WS.id},
        workspaces={_M_WS.id})
    services = ManagedServices(
        bindings, FakeAuthorityBackend(), topology,
        FakeIdentityRegistry(), FakeLinkRequests())
    return bindings, services.access


async def _managed_person(
    bindings: FakeBindingStore, *, member: bool = True,
    display_name: str = "Generated Global",
) -> None:
    await bindings.register(
        person=ManagedPerson(
            _M_PERSON, PersonLifecycle.ACTIVE, display_name=display_name),
        account=ManagedAccount(
            "generated-binding", _M_PERSON, "signal",
            frozenset({Identifier("signal", "phone", "+852111")}),
            AccountState.BOUND),
        membership=ManagedMembership(
            _M_PERSON, _M_ROOM, MembershipState.MEMBER, "generated")
        if member else None)


def _managed_assembler(store: FakeRecordStore, access) -> TranscriptInjection:
    return TranscriptInjection(
        audit=store, memory=FakeMemory(),
        persons=Persons(by_person={}, index={}),   # unused under managed
        context_window=10, metrics=Metrics(), access=access)


async def _seeded_managed(
    bindings: FakeBindingStore, access, *, member: bool = True,
):
    await _managed_person(bindings, member=member)
    store = FakeRecordStore()
    for i in range(2):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Generated Platform Name",
            timestamp=i, body=f"generated m{i}", raw={}))
    return _managed_assembler(store, access)


async def test_the_rooms_nickname_renders_through_the_tag_context():
    bindings, access = _managed()
    assembler = await _seeded_managed(bindings, access)
    assert (await bindings.set_scoped_name(
        person_id=_M_PERSON, scope=_M_ROOM, nickname="Generated Room Nick",
        operation_id="generated-op-1")).status.value == "complete"

    ctx = await assembler.assemble(request=_request())

    assert ctx.tagger is not None and ctx.tagger.name == "Generated Room Nick"
    assert all(
        row.person_name == "Generated Room Nick" for row in ctx.transcript)
    assert [r.name for r in ctx.room_members] == ["Generated Room Nick"]
    # IDs are untouched — the scoped view renames nothing canonical.
    assert ctx.tagger.person_id == _M_PERSON
    assert all(row.person_id == _M_PERSON for row in ctx.transcript)


async def test_the_nearest_scoped_name_wins_and_a_sibling_never_substitutes():
    bindings, access = _managed()
    assembler = await _seeded_managed(bindings, access)
    for scope, nickname in (
            (_M_WS, "Generated Workspace Nick"),
            (_M_UNIT, "Generated Unit Nick"),
            (_M_SIBLING, "Generated Sibling Nick")):
        assert (await bindings.set_scoped_name(
            person_id=_M_PERSON, scope=scope, nickname=nickname,
            operation_id=f"generated-op-{scope.id}")).status.value == "complete"

    ctx = await assembler.assemble(request=_request())
    # No room nickname: this room inherits the UNIT's, never the sibling's.
    assert all(
        row.person_name == "Generated Unit Nick" for row in ctx.transcript)
    assert ctx.tagger is not None and ctx.tagger.name == "Generated Unit Nick"

    # The sibling room's own view falls to the same shared ancestry —
    # and a room-level name overrides the unit's only in that room.
    assert (await access.person_ref(_M_PERSON, scope=_M_SIBLING)).name \
        == "Generated Sibling Nick"
    assert (await bindings.set_scoped_name(
        person_id=_M_PERSON, scope=_M_ROOM, nickname="Generated Room Nick",
        operation_id="generated-op-room")).status.value == "complete"
    ctx = await assembler.assemble(request=_request())
    assert ctx.tagger is not None and ctx.tagger.name == "Generated Room Nick"
    assert (await access.person_ref(_M_PERSON, scope=_M_SIBLING)).name \
        == "Generated Sibling Nick"
    assert (await access.person_ref(_M_PERSON)).name == "Generated Global"


async def test_an_unreadable_scoped_store_fails_the_turn_not_the_name():
    bindings, access = _managed()
    assembler = await _seeded_managed(bindings, access)
    assert (await bindings.set_scoped_name(
        person_id=_M_PERSON, scope=_M_ROOM, nickname="Generated Room Nick",
        operation_id="generated-op-1")).status.value == "complete"

    bindings.down = True
    try:
        with pytest.raises(RuntimeError):
            await assembler.assemble(request=_request())
    finally:
        bindings.down = False

    ctx = await assembler.assemble(request=_request())
    assert ctx.tagger is not None and ctx.tagger.name == "Generated Room Nick"


async def test_an_unmapped_sender_keeps_the_platform_name_not_a_scoped_one():
    bindings, access = _managed()
    await _managed_person(bindings)
    store = FakeRecordStore()
    await store.append(message=NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852999", sender_name="Generated Stranger",
        timestamp=0, body="generated m0", raw={}))
    ctx = await _managed_assembler(store, access).assemble(request=_request())
    assert ctx.transcript[0].person_name is None
    assert ctx.transcript[0].sender_name == "Generated Stranger"
