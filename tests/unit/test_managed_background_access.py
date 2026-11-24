"""Background tool construction reads the selected identity scope and fails closed."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from nora.agent.dreamer import AdaptorGrant
from nora.agent.room import Room
from nora.app.composition_dream import _dream_tool_source
from nora.domain.social_stream import PersonSocialItem, RecordedCounts
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.testing import FakeRosterSource
from tests.unit.test_dreamer_runtime import _RecordingRegistry
from tests.unit.test_scoped_engagement_names import (
    _M_PERSON,
    _M_ROOM,
    _M_SIBLING,
    _M_UNIT,
    _managed,
    _managed_person,
)


async def test_dreamer_tools_use_live_scoped_identity_and_refuse_a_lookup_outage(example_config):
    bindings, access = _managed()
    await _managed_person(bindings)
    await bindings.set_scoped_name(person_id=_M_PERSON, scope=_M_ROOM,
        nickname="Generated Background Nick", operation_id="generated-background-name")
    registry = _RecordingRegistry()
    source = _dream_tool_source(
        allowlist=permissive_policy(), audit=None, cfg=example_config,
        metrics=Metrics(), persons=Persons(by_person={}, index={}),
        proactive_homes={_M_UNIT.id: SimpleNamespace(
            platform="signal", primary_id="g1", location=False)},
        registry=SimpleNamespace(self_aliases=lambda: frozenset()),
        rosters={"signal": FakeRosterSource(groups={"g1": (frozenset({"+852111"}),)})},
        tool_registry=registry, access=access)
    source.build(key=_M_UNIT.id, unit=_M_UNIT.id, names=frozenset({"now"}),
                 grant=AdaptorGrant(home_room=True))
    room = registry.calls[-1]["room"]
    first = await room.members()
    assert [(p.person_id, p.name) for p in first.people] == [
        (_M_PERSON, "Generated Background Nick")]
    await bindings.set_scoped_name(person_id=_M_PERSON, scope=_M_ROOM,
        nickname="Generated Renamed Nick", operation_id="generated-background-rename")
    assert [p.name for p in (await room.members()).people] == ["Generated Renamed Nick"]
    bindings.down = True
    with pytest.raises(RuntimeError):
        await room.members()
    bindings.down = False
    assert [p.name for p in (await room.members()).people] == ["Generated Renamed Nick"]


async def test_a_room_without_identity_wiring_returns_unavailable():
    room = Room(platform="signal", conversation_id="g1", roster_source=FakeRosterSource(
        groups={"g1": (frozenset({"+852111"}),)}))
    result = await room.members()
    assert not result.available
    assert result.people == ()


_NOW = datetime.fromtimestamp((1_760_000_000_000 + 3_600_000) / 1000, tz=UTC)


class _SocialFake:
    def __init__(self, items: list[PersonSocialItem], *, polled: int = 0) -> None:
        self._items = items
        self._polled = polled
        self.counts_asked: list[tuple[str, str]] = []

    async def polled_count(self, *, persons, after_us) -> int:
        return self._polled

    async def now(self) -> datetime:
        return _NOW

    async def person_items(self, *, persons, after_us, limit):
        return [i for i in self._items if i.recorded_us > after_us][:limit]

    async def current_counts(self, *, items):
        self.counts_asked = list(items)
        return {
            item: RecordedCounts(likes=12, comments=3, reposts=None,
                                 likes_hidden=False, read_at=_NOW)
            for item in items
        }




async def _background_identity_case():
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
    from nora.people.app.managed import ManagedServices
    from nora.testing.managed import (
        FakeAuthorityBackend,
        FakeBindingStore,
        FakeIdentityRegistry,
        FakeLinkRequests,
        FakeTopologySource,
    )

    bindings, registry = FakeBindingStore(), FakeIdentityRegistry()
    other_unit = Scope(ScopeKind.UNIT, "generated-profile-other")
    other_room = Scope(ScopeKind.ROOM, room_scope_id("signal", "g3"))
    people = (_M_PERSON, "generated-profile-peer", "generated-profile-outsider")
    phones = ("+85290000110", "+85290000111", "+85290000112")
    for i, person in enumerate(people):
        scope = other_room if i == 2 else _M_ROOM
        await bindings.register(person=ManagedPerson(
            person, PersonLifecycle.ACTIVE, display_name=f"Generated Global {i}"),
            account=ManagedAccount(f"generated-profile-account-{i}", person, "signal",
                frozenset({Identifier("signal", "phone", phones[i])}),
                AccountState.BOUND), membership=ManagedMembership(
                    person, scope, MembershipState.MEMBER, "generated"))
        await registry.ensure_identity(external_id=person, traits={
            "display_name": f"Generated Global {i}", "social": True})
        await bindings.set_scoped_name(person_id=person,
            scope=other_unit if i == 2 else _M_UNIT,
            nickname=f"Generated Unit Name {i}", operation_id=f"generated-profile-name-{i}")
    topology = FakeTopologySource(
        rooms={_M_ROOM.id: (_M_UNIT.id, False), _M_SIBLING.id: (_M_UNIT.id, False),
               other_room.id: (other_unit.id, False)},
        units={_M_UNIT.id: "generated-workspace", other_unit.id: "generated-workspace"},
        workspaces={"generated-workspace"})
    access = ManagedServices(bindings, FakeAuthorityBackend(), topology,
                             registry, FakeLinkRequests()).access
    return SimpleNamespace(bindings=bindings, registry=registry, access=access,
                           people=people, topology=topology)


async def test_room_membership_excludes_a_sibling_units_members():
    from nora.domain.managed import room_scope_id
    from nora.domain.management import Scope, ScopeKind

    case = await _background_identity_case()
    person, peer, outsider = case.people
    other_room = Scope(ScopeKind.ROOM, room_scope_id("signal", "g3"))

    assert [m.person_id for m in await case.access.members_of(other_room)] == [outsider]
    assert [r.person_id for r in await case.access.room_refs("signal", "g1")] == [person, peer]
