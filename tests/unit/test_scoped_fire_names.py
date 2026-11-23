"""Scoped identity names through the actual fire names callers."""

from __future__ import annotations

from typing import Any

import pytest

from nora.app.fire import FirePath, reminder_occasion
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
from nora.obs.metrics import Metrics
from nora.people.app.managed import ManagedServices
from nora.persons import Persons
from nora.testing.fakes import FakeRecordStore
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from tests.unit.test_fire_path import T0, _reminder

_M_FIRE_ROOM = Scope(ScopeKind.ROOM, room_scope_id("signal", "conv-1"))
_M_FIRE_SIBLING = Scope(ScopeKind.ROOM, room_scope_id("signal", "conv-2"))
_M_FIRE_UNIT = Scope(ScopeKind.UNIT, "generated-unit")
_M_FIRE_WS = Scope(ScopeKind.WORKSPACE, "generated-workspace")


def _managed_fire() -> tuple[FakeBindingStore, Any]:
    bindings = FakeBindingStore()
    topology = FakeTopologySource(
        rooms={_M_FIRE_ROOM.id: (_M_FIRE_UNIT.id, False),
               _M_FIRE_SIBLING.id: (_M_FIRE_UNIT.id, False)},
        units={_M_FIRE_UNIT.id: _M_FIRE_WS.id},
        workspaces={_M_FIRE_WS.id})
    services = ManagedServices(
        bindings, FakeAuthorityBackend(), topology,
        FakeIdentityRegistry(), FakeLinkRequests())
    return bindings, services.access


async def _managed_fire_person(bindings: FakeBindingStore) -> None:
    await bindings.register(
        person=ManagedPerson(
            "miles", PersonLifecycle.ACTIVE, display_name="Generated Global"),
        account=ManagedAccount(
            "generated-binding", "miles", "signal",
            frozenset({Identifier("signal", "phone", "+85212345678")}),
            AccountState.BOUND),
        membership=ManagedMembership(
            "miles", _M_FIRE_ROOM, MembershipState.MEMBER, "generated"))


async def _managed_fire_path(
    bindings: FakeBindingStore, access, store: FakeRecordStore,
) -> FirePath:
    for i in range(2):
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id="conv-1",
            conversation_type="group", sender_id="+85212345678",
            sender_name="Generated Platform Name", timestamp=i,
            body=f"generated m{i}", raw={}))
    return FirePath(
        persons=Persons(by_person={}, index={}),   # unused under managed
        records=store,
        doctrine_store=None, metrics=Metrics(),                     # type: ignore[arg-type]
        runner=None, adapters={}, registry=None,                    # type: ignore[arg-type]
        canary="test-canary", display_name="Nora",
        ai_peer="nora", context_window=40, limits={},
        timeout_seconds=120.0, clock=lambda: T0, access=access)


async def test_the_occasion_rooms_nickname_renders_through_the_fire_turn():
    bindings, access = _managed_fire()
    await _managed_fire_person(bindings)
    store = FakeRecordStore()
    path = await _managed_fire_path(bindings, access, store)
    for scope, nickname in (
            (_M_FIRE_WS, "Generated Workspace Nick"),
            (_M_FIRE_UNIT, "Generated Unit Nick"),
            (_M_FIRE_ROOM, "Generated Room Nick"),
            (_M_FIRE_SIBLING, "Generated Sibling Nick")):
        assert (await bindings.set_scoped_name(
            person_id="miles", scope=scope, nickname=nickname,
            operation_id=f"generated-op-{scope.id}")).status.value == "complete"

    ctx = await path.build_context(reminder_occasion([_reminder()]))

    assert ctx.tagger is not None and ctx.tagger.name == "Generated Room Nick"
    assert all(
        row.person_name == "Generated Room Nick" for row in ctx.transcript)
    assert [r.name for r in ctx.room_members] == ["Generated Room Nick"]
    assert ctx.tagger.person_id == "miles"
    assert all(row.person_id == "miles" for row in ctx.transcript)


async def test_a_sibling_rooms_nickname_never_names_the_requester():
    bindings, access = _managed_fire()
    await _managed_fire_person(bindings)
    store = FakeRecordStore()
    path = await _managed_fire_path(bindings, access, store)
    assert (await bindings.set_scoped_name(
        person_id="miles", scope=_M_FIRE_SIBLING,
        nickname="Generated Sibling Nick",
        operation_id="generated-op-1")).status.value == "complete"

    ctx = await path.build_context(reminder_occasion([_reminder()]))

    # No name on this room's ancestry: the global name renders, not the
    # sibling's — and the sibling room's own view DOES see it.
    assert ctx.tagger is not None and ctx.tagger.name == "Generated Global"
    assert (await access.person_ref("miles", scope=_M_FIRE_SIBLING)).name \
        == "Generated Sibling Nick"


async def test_an_unreadable_scoped_store_fails_the_fire_turn():
    bindings, access = _managed_fire()
    await _managed_fire_person(bindings)
    store = FakeRecordStore()
    path = await _managed_fire_path(bindings, access, store)
    assert (await bindings.set_scoped_name(
        person_id="miles", scope=_M_FIRE_ROOM,
        nickname="Generated Room Nick",
        operation_id="generated-op-1")).status.value == "complete"

    bindings.down = True
    try:
        with pytest.raises(RuntimeError):
            await path.build_context(reminder_occasion([_reminder()]))
    finally:
        bindings.down = False

    ctx = await path.build_context(reminder_occasion([_reminder()]))
    assert ctx.tagger is not None and ctx.tagger.name == "Generated Room Nick"
