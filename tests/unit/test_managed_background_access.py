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


async def test_composed_culture_fold_uses_current_room_names_and_stops_on_outage(
    example_config, monkeypatch,
):
    from nora.app import composition_dream
    from nora.domain.models import DeliveryStatus, TranscriptRow
    from nora.testing import InMemoryCultureStore
    from tests.unit.test_scoped_engagement_names import _M_SIBLING

    bindings, access = _managed()
    await _managed_person(bindings)
    await bindings.set_scoped_name(person_id=_M_PERSON, scope=_M_ROOM,
        nickname="Generated Culture Name", operation_id="generated-culture-name")
    await bindings.set_scoped_name(person_id=_M_PERSON, scope=_M_SIBLING,
        nickname="Generated Private Sibling", operation_id="generated-sibling-name")
    culture = InMemoryCultureStore()
    monkeypatch.setattr(composition_dream, "SqlCultureStore", lambda _engine: culture)
    policy = SimpleNamespace(rooms=lambda: (SimpleNamespace(
        primary_id="g1", platform="signal", unit=_M_UNIT.id,
        label="Generated Room", description="Generated purpose"),))
    wired = composition_dream._character_adaptors(
        allowlist=policy, cfg=example_config, content_checks=False,
        engine=None, metrics=Metrics(), note_store=None,
        persons=access.persons_view(), access=access)
    adaptor = next(a for a in wired.adaptors if a.name == "culture")
    batch = (TranscriptRow(id=1, ts=1_757_000_000_000, sender_id="+852111",
        sender_name="Generated Spoofed Sender", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json="{}",
        body="Generated message about a planned meeting"),)
    first = await adaptor.inputs(key="g1", batch=batch)
    assert "Generated Culture Name" in first
    assert "Generated Global" not in first
    assert "Generated Private Sibling" not in first
    assert "Generated Spoofed Sender" not in first
    await bindings.set_scoped_name(person_id=_M_PERSON, scope=_M_ROOM,
        nickname="Generated New Culture Name", operation_id="generated-culture-rename")
    second = await adaptor.inputs(key="g1", batch=batch)
    assert "Generated New Culture Name" in second
    bindings.down = True
    with pytest.raises(RuntimeError):
        await adaptor.inputs(key="g1", batch=batch)
    assert await culture.head(key="g1") is None
    bindings.down = False
    assert "Generated New Culture Name" in await adaptor.inputs(key="g1", batch=batch)


@pytest.mark.parametrize("platform", ["signal", None])
async def test_culture_selected_file_backend_keeps_its_canonical_name(tmp_path, platform):
    from dataclasses import replace

    from nora.language.culture import CultureAdaptor
    from nora.people.wire import file_services
    from nora.persons import load_persons
    from nora.testing import InMemoryCultureStore
    from tests.unit.test_culture_adaptor import _row

    path = tmp_path / "identity.toml"
    path.write_text('[persons.generated]\nname = "Generated File Name"\n'
                    '[[persons.generated.signal]]\nphone = "+852111"\n')
    access = file_services(load_persons(path), permissive_policy()).access
    adaptor = CultureAdaptor(
        culture=InMemoryCultureStore(), access=access,
        platform_for=lambda _key: platform, unit_for=lambda _key: "generated-unit",
        label_for=lambda _key: "Generated Room", description_for=lambda _key: None)
    batch = (replace(_row("Generated file message", name="Generated Spoofed Sender"),
                     sender_id="+852111", person_name=None),)
    if platform is None:
        with pytest.raises(RuntimeError, match="platform is unavailable"):
            await adaptor.inputs(key="g1", batch=batch)
        return
    body = await adaptor.inputs(key="g1", batch=batch)
    assert "Generated File Name" in body
    assert "Generated Spoofed Sender" not in body
