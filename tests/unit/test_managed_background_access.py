"""Background tool construction reads the selected identity scope and fails closed."""

from types import SimpleNamespace

import pytest

from nora.agent.dreamer import AdaptorGrant
from nora.agent.room import Room
from nora.app.composition_dream import _dream_tool_source
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.testing import FakeRosterSource
from tests.unit.test_dreamer_runtime import _RecordingRegistry
from tests.unit.test_scoped_engagement_names import (
    _M_PERSON,
    _M_ROOM,
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
        allowlist=permissive_policy(), audit=None, cfg=example_config, memory=None,
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
