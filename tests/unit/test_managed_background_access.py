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


def _population_rooms():
    return SimpleNamespace(rooms=lambda: (
        SimpleNamespace(primary_id="g1", platform="signal", unit=_M_UNIT.id,
                        label="Generated A", description=None, persons=frozenset()),
        SimpleNamespace(primary_id="g2", platform="signal", unit=_M_UNIT.id,
                        label="Generated B", description=None, persons=frozenset()),
    ))


async def _population_keys(adaptor):
    import inspect

    keys = adaptor.keys()
    return await keys if inspect.isawaitable(keys) else keys


async def test_character_and_profile_composition_enumerate_the_selected_managed_population(
    example_config,
):
    from datetime import UTC, datetime

    from nora.app.composition_dream import _character_adaptors, _profile_person_adaptors
    from nora.domain.culture import character_key

    bindings, access = _managed()
    await _managed_person(bindings)
    await bindings.set_scoped_name(person_id=_M_PERSON, scope=_M_UNIT,
        nickname="Generated Unit Person", operation_id="generated-unit-person-name")
    await bindings.set_scoped_name(person_id=_M_PERSON, scope=_M_ROOM,
        nickname="Generated Room Only", operation_id="generated-room-person-name")
    persons = access.persons_view()
    assert not persons.by_person  # File-shaped absence must not empty the managed population.
    character = _character_adaptors(
        allowlist=_population_rooms(), cfg=example_config, content_checks=None,
        engine=None, metrics=Metrics(), note_store=None, persons=persons, access=access)
    person = next(a for a in character.adaptors if a.name == "character-person")
    expected = character_key(_M_UNIT.id, _M_PERSON)
    assert await _population_keys(person) == (expected,)
    pk = character.person_keys[expected]
    assert (pk.person.person_id, pk.person.name, pk.home, pk.listed) == (
        _M_PERSON, "Generated Unit Person", "g1", ("g1",))
    assert tuple(r.key for r in pk.rooms) == ("g1", "g2")
    _, adaptors, *_ = _profile_person_adaptors(
        allowlist=_population_rooms(), cfg=example_config, character=character,
        clock=lambda: datetime.now(UTC), content_checks=None, engine=None,
        media_join=None, metrics=Metrics(), persons=persons,
        profile_collective_keys={}, profile_index=None, access=access)
    profile = next(a for a in adaptors if a.name == "profile-person")
    assert await _population_keys(profile) == (expected,)

    await bindings.set_scoped_name(person_id=_M_PERSON, scope=_M_UNIT,
        nickname="Generated Renamed Unit Person", operation_id="generated-unit-person-rename")
    assert await _population_keys(profile) == (expected,)
    assert character.person_keys[expected].person.name == "Generated Renamed Unit Person"
    bindings.down = True
    with pytest.raises(RuntimeError):
        await _population_keys(person)
    with pytest.raises(RuntimeError, match="population is unavailable"):
        character.person_keys[expected]
    bindings.down = False
    assert await _population_keys(profile) == (expected,)


async def test_selected_person_population_keeps_units_separate_and_observes_departure():
    from nora.app.composition_helpers import selected_character_person_keys
    from nora.domain.culture import character_key
    from nora.domain.managed import room_scope_id
    from nora.domain.management import Scope, ScopeKind

    bindings, access = _managed()
    await _managed_person(bindings)
    other = Scope(ScopeKind.UNIT, "generated-other-unit")
    other_room = Scope(ScopeKind.ROOM, room_scope_id("signal", "g3"))
    from nora.people.app.managed import ManagedServices
    from nora.testing.managed import (
        FakeAuthorityBackend,
        FakeIdentityRegistry,
        FakeLinkRequests,
        FakeTopologySource,
    )

    access = ManagedServices(bindings, FakeAuthorityBackend(), FakeTopologySource(
        rooms={_M_ROOM.id: (_M_UNIT.id, False),
               _M_SIBLING.id: (_M_UNIT.id, False), other_room.id: (other.id, False)},
        units={_M_UNIT.id: "generated-workspace", other.id: "generated-workspace"},
        workspaces={"generated-workspace"}), FakeIdentityRegistry(), FakeLinkRequests()).access
    await bindings.set_membership(person_id=_M_PERSON, scope=other_room,
        member=True, source="generated")
    await bindings.set_scoped_name(person_id=_M_PERSON, scope=_M_UNIT,
        nickname="Generated Unit One", operation_id="generated-name-one")
    await bindings.set_scoped_name(person_id=_M_PERSON, scope=other,
        nickname="Generated Unit Two", operation_id="generated-name-two")
    rooms = (*_population_rooms().rooms(), SimpleNamespace(
        primary_id="g3", platform="signal", unit=other.id, label="Generated C"))
    source = SimpleNamespace(rooms=lambda: rooms)
    keys = await selected_character_person_keys(source, access)
    first, second = character_key(_M_UNIT.id, _M_PERSON), character_key(other.id, _M_PERSON)
    assert set(keys) == {first, second}
    assert keys[first].person.name == "Generated Unit One"
    assert keys[second].person.name == "Generated Unit Two"
    assert tuple(r.key for r in keys[second].rooms) == ("g3",)
    await bindings.set_membership(person_id=_M_PERSON, scope=_M_ROOM,
        member=False, source="generated")
    keys = await selected_character_person_keys(source, access)
    assert set(keys) == {second}


@pytest.mark.parametrize("outage_first", [False, True])
async def test_dream_loop_awaits_the_selected_population_before_dispatch(outage_first):
    import asyncio

    from nora.agent.dreamer import AdaptorSchedule
    from nora.app.composition_helpers import selected_character_person_keys
    from nora.app.runtime import dream_loop
    from nora.domain.culture import character_key
    from nora.domain.live import AsyncLiveMapping
    from tests.unit.test_runtime import (
        _EntityAdaptor,
        _Evidence,
        _FakeRecord,
        _KeyedLanguage,
        _RecordingRunner,
    )

    bindings, access = _managed()
    await _managed_person(bindings)
    reads = 0

    async def read_population():
        nonlocal reads
        reads += 1
        if outage_first and reads == 1:
            raise RuntimeError("generated population outage")
        return await selected_character_person_keys(_population_rooms(), access)

    population = AsyncLiveMapping(read_population)
    dispatched = asyncio.Event()

    class PopulationAdaptor(_EntityAdaptor):
        def keys(self):
            return population.refresh()

    class RecordingRunner(_RecordingRunner):
        async def run(self, **kwargs):
            result = await super().run(**kwargs)
            dispatched.set()
            return result

    runner = RecordingRunner()
    task = asyncio.create_task(dream_loop(
        _FakeRecord({}), _KeyedLanguage(age=1000), Metrics(), scopes=lambda: (),
        tick_seconds=0.01, safety_lag=0, quiet_seconds=0,
        max_age_seconds=1e9, batch_limit=1,
        adaptors=[PopulationAdaptor("character-person", _Evidence([1]))], runtime=runner,
        schedules={"character-person": AdaptorSchedule(
                min_interval_seconds=0, batch_limit=1, wake_batch=1)}))
    waiter = asyncio.create_task(dispatched.wait())
    try:
        await asyncio.wait({waiter, task}, timeout=3, return_when=asyncio.FIRST_COMPLETED)
        assert dispatched.is_set(), "selected population was not dispatched"
    finally:
        task.cancel()
        waiter.cancel()
        await asyncio.gather(task, waiter, return_exceptions=True)
    assert runner.calls[0][:2] == ("character-person", character_key(_M_UNIT.id, _M_PERSON))
