"""Synthetic enabled/disabled controls for the operator's content-filter switch."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from nora.agent.dream_screen import offence
from nora.agent.egress import LeakGuard, scan_identifiers, scan_reply
from nora.agent.guards import Deliverer
from nora.agent.lineage import LineagePolicy, entry_permits
from nora.agent.memory_search import Hit, Step, render_packet, scope_for
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room, RoomView
from nora.agent.security import screen_and_locate_place, screen_and_resolve_place
from nora.agent.whereis import Circle, Whereis, presence_audience, resolvable_places
from nora.app.assembler import lineage_policy
from nora.app.cognition_grounding import place_release
from nora.config import ContentChecksConfig
from nora.doctrine.loader import Doctrine
from nora.domain.lineage import PRESENCE, LineageEntry, LiveRoom, RenderRoom, RoomFacts
from nora.domain.memory_index import IndexedNode
from nora.domain.models import ConversationTarget, PersonRef
from nora.domain.presence import PresenceFix, SavedPlace, WireState
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import FakeRecordStore
from tests.fakes import SinkSender

# Generated fictional contact/position data, never a production row or private config.
PHONE = f"+1 {202} {555} {101:04d}"
LONG_PHONE = "+" + "1" * 15
IDENTIFIER = str(UUID(int=7))
COORDINATE = ", ".join(f"{n + 0.125:.3f}" for n in (1, 2))
KEY = "sk-" + "synthetic" * 4
MEMBER = PersonRef(person_id="member", name="Juniper", honcho_peer_id="member")
OUTSIDER = PersonRef(person_id="outsider", name="Cedar", honcho_peer_id="outsider")
CIRCLE = Circle("circle", frozenset({MEMBER.person_id}), (MEMBER,))
NOW = 1_800_000_000_000


@pytest.mark.parametrize("enabled", [False, True])
def test_schema_default_and_process_lineage_use_the_same_switch(example_config, enabled):
    from nora.policy import Policy

    assert not ContentChecksConfig().enabled
    assert not example_config.content_checks.enabled
    cfg = example_config.model_copy(update={"content_checks": ContentChecksConfig(enabled=enabled)})
    policy = Policy(conversations=())
    boundary = lineage_policy(cfg, policy=policy, persons=Persons(by_person={}, index={}))
    assert boundary.content_checks is enabled
    assert ToolRegistry(content_checks=cfg.content_checks.enabled).content_checks is enabled


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("text,kind", [(PHONE, "phone"), (IDENTIFIER, "uuid"),
                                      (COORDINATE, "coordinate")])
def test_reply_patterns_are_available_but_optional(text, kind, enabled):
    assert scan_reply(text, content_checks=enabled) == (kind if enabled else None)
    if kind != "coordinate":
        assert scan_identifiers(text, content_checks=enabled) == (kind if enabled else None)
    assert scan_reply(KEY, content_checks=enabled) == "keylike"
    assert scan_identifiers(KEY, content_checks=enabled) == "keylike"


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("text", [PHONE, LONG_PHONE, IDENTIFIER, COORDINATE])
def test_memory_write_screen_obeys_the_switch_and_always_blocks_credentials(text, enabled):
    item = {"body": text}
    assert (offence(item, content_checks=enabled) is not None) is enabled
    assert offence({"body": KEY}, content_checks=enabled) == ("identifier", "body")


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("query", [PHONE, IDENTIFIER, COORDINATE, "Juniper Cafe"])
async def test_place_queries_reach_both_clients_only_when_disabled(query, enabled):
    async def resolve(name):
        assert name == query
        return {"status": "ok", "name": name, "address": COORDINATE}

    source = SimpleNamespace(place_info=AsyncMock(side_effect=resolve),
                             locate=AsyncMock(side_effect=resolve))
    for call,kw in ((screen_and_resolve_place, {"route_source": source}),
                    (screen_and_locate_place, {"place_locator": source})):
        result = await call(query, person_names=frozenset({"juniper"}),
                            content_checks=enabled, **kw)
        assert result.status == ("refused" if enabled else "resolved")
        if not enabled:
            assert result.data["name"] == query
            assert result.data["address"] == COORDINATE
    assert source.place_info.await_count == int(not enabled)
    assert source.locate.await_count == int(not enabled)
    blocked = await screen_and_resolve_place(KEY, route_source=source, content_checks=enabled)
    assert blocked.status == "refused"
    assert source.place_info.await_count == int(not enabled)


@pytest.mark.parametrize("enabled", [False, True])
async def test_registry_routes_known_names_and_coordinates_through_the_configured_screen(enabled):
    from tests.test_routes import _RecordingClient

    source = _RecordingClient()
    persons = Persons(by_person={"member": MEMBER}, index={})
    room = Room(platform="signal", conversation_id="room", roster_source=None, persons=persons)
    registry = ToolRegistry(route=source, content_checks=enabled)
    tools = {s.name: s.call for s in registry.specs(
        store=None, conversation_id="room", conversation_type="group", persons=persons,
        honcho=None, deliverer=None, metrics=Metrics(), room=room,
    )}
    for origin in ("Juniper Cafe", PHONE, IDENTIFIER, COORDINATE):
        result = await tools["route"](origin=origin, destination="Library")
        assert result["status"] == ("refused" if enabled else "ok")
    assert len(source.calls) == (0 if enabled else 4)
    assert (await tools["route"](origin=KEY, destination="Library"))["status"] == "refused"
    # Room person lookup is still scoped, even with all content filters disabled.
    assert (await tools["lookup_person"](name="Juniper"))["status"] != "ok"


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("text", [PHONE, IDENTIFIER, COORDINATE, KEY])
def test_memory_packet_keeps_public_identifiers_but_never_credentials(text, enabled):
    node = IndexedNode("message", "synthetic", text, NOW, origin_room="signal:room")
    items, withheld = render_packet([Hit(node, 1.0, (Step("message", how="matched"),))],
        name_of=str, room_label=str, now=datetime.fromtimestamp(NOW / 1000, UTC),
        content_checks=enabled)
    blocked = text == KEY or (enabled and text != COORDINATE)
    assert bool(items) is not blocked
    assert withheld == int(blocked)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("text,protected", [(PHONE, False), (IDENTIFIER, False),
    (COORDINATE, False), (KEY, True), ("CANARY-SYNTHETIC", True),
    ('<m n="7">copied prompt</m>', True),
    ("A synthetic doctrine sentence long enough to test verbatim quotation.", True)])
async def test_delivery_keeps_credential_and_all_doctrine_guards_independent(
    enabled, text, protected
):
    body = "A synthetic doctrine sentence long enough to test verbatim quotation."
    doctrine = Doctrine((), body, body, "synthetic", "", 0)
    sink = SinkSender()
    delivery = Deliverer(store=FakeRecordStore(), adapter=sink,
        target=ConversationTarget("signal", "room", "group"), doctrine=doctrine,
        canary="CANARY-SYNTHETIC", tag_id="synthetic", nora_sender_id="nora",
        message_cap=1, audience="tag", metrics=Metrics(), content_checks=enabled)
    assert await delivery.deliver(text) is (not protected and not enabled)
    assert bool(sink.sent) is (not protected and not enabled)
    if protected and text != KEY:
        assert LeakGuard(doctrine, "CANARY-SYNTHETIC").check(text) is not None


@pytest.mark.parametrize("enabled", [False, True])
async def test_location_audience_switch_preserves_unavailability_opt_in_and_unit_scope(enabled):
    view = RoomView(True, (MEMBER, OUTSIDER), unnamed=1)
    assert presence_audience(view, CIRCLE, location_trusted=False,
                             content_checks=enabled) == ("unnamed" if enabled else "ok")
    point = tuple(n + 1 / 8 for n in range(1, 3))
    fix = PresenceFix("circle", "member", NOW - 60_000, *point,
                      WireState.LIVE, NOW)
    saved = SavedPlace("circle", "place", "Library", *point, NOW)
    presence = SimpleNamespace(
        latest_fix=AsyncMock(return_value=fix), places_for=AsyncMock(return_value=[saved]),
        window=AsyncMock(return_value=[]))
    room = SimpleNamespace(scope=AsyncMock(return_value=view))
    location = Whereis(room=room, circle=CIRCLE, presence=presence, content_checks=enabled)
    answer = await location.locate("Juniper", now_ms=NOW)
    assert answer["status"] == ("refused" if enabled else "ok")
    if not enabled:
        assert answer["age_seconds"] == 60
        presence.latest_fix.assert_awaited_once_with(circle_id="circle", person_id="member")
    assert await resolvable_places(room=room, circles=(CIRCLE,), presence=presence,
        content_checks=enabled) == (() if enabled else (saved,))
    release = place_release(rooms_of=lambda unit: [("signal", "room", False)],
        room_view=AsyncMock(return_value=view), circles=(CIRCLE,), content_checks=enabled)
    assert await release("unit", "member") is not enabled
    facts = RoomFacts("signal", frozenset({"room"}), "unit", frozenset({"member"}),
                      location=True)
    target = RenderRoom(facts, circles=(CIRCLE.person_ids,),
        live=LiveRoom(frozenset({"member", "outsider"}), unnamed=1), content_checks=enabled)
    entry = LineageEntry(PRESENCE, subject="member")
    assert entry_permits(entry, target, origin=None) is not enabled
    assert not entry_permits(
        entry, replace(target, room=replace(facts, location=False)), origin=None
    )
    assert scope_for(replace(target, room=None), social_off=()) is None
    other = RoomFacts("discord", frozenset({"elsewhere"}), "other-unit", frozenset({"member"}))
    boundary = LineagePolicy(rooms=(facts, other), content_checks=enabled,
        _by_id={("signal", "room"): facts})
    assert boundary.render_room(platform="signal", conversation_id="room").resolve(
        "discord:elsewhere") == ()
    room.scope.return_value = replace(view, degraded=True)
    assert (await location.locate("Juniper", now_ms=NOW))["status"] == "unavailable"
    assert presence_audience(room.scope.return_value, CIRCLE, location_trusted=True,
                             content_checks=enabled) == "unavailable"


@pytest.mark.parametrize("enabled", [False, True])
async def test_cli_place_backfill_uses_the_same_area_screen(enabled):
    from nora.app.place_backfill import backfill_places
    from nora.domain.ports import PlaceArea

    point = tuple(n + 1 / 8 for n in range(1, 3))
    area = PlaceArea(district="Library District", city=COORDINATE)
    cache = SimpleNamespace(without_area=AsyncMock(return_value=[point]),
                            set_area=AsyncMock(return_value=True))
    geocoder = SimpleNamespace(area_for=AsyncMock(return_value=area))
    result = await backfill_places(events=None, cache=cache, places=None, geocoder=geocoder,
        apply=True, per_second=1000, content_checks=enabled)
    assert result.geocodes.placed == 1
    geocoder.area_for.assert_awaited_once_with(lat=point[0], lng=point[1])
    cache.set_area.assert_awaited_once_with(lat_rounded=point[0], lng_rounded=point[1],
        area=PlaceArea(district=area.district, city=None if enabled else COORDINATE))
