"""The engagement graph surface is the same for resident and deferred tools."""


import pytest
from strands import Agent

from nora.agent.catalog.groups import LEGACY_ENGAGEMENT_TOOLS, LOADER_TOOL
from nora.agent.dreamer import RegistryToolSource
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.surface import load_onto
from nora.obs.metrics import Metrics
from tests.unit.test_tool_registry import _deliverer, _FakePersons, _FakeStore

AMBIENT = frozenset({
    "calculate", "file_fetch", "find_parking", "generate_image", "group_members",
    "instagram_read", "instagram_search", "intention_commit", "linkedin_read", "linkedin_search",
    "load_transcript", "lookup_person", "media_fetch", "media_search", "media_send",
    "memory_read", "memory_search", "now", "online_now", "parking_status",
    "place_info", "reaction_send", "reverse_geocode", "room_list", "room_read",
    "route", "routine_read", "saved_places", "search_gifs", "send_gif",
    "send_reply", "send_to", "transit", "version_list", "version_read",
    "weather", "web_fetch", "web_search", "where_been", "where_is", "who_said",
    "x_read", "x_search",
})
CALLED = AMBIENT | {
    "member_update", "rollup_read", "subagent_create", "subagent_list", "subagent_read",
    "subagent_cancel", "video_create",
}


def _registry():
    return ToolRegistry(
        engagement_graph_retrieval=object(), intention_admission=object(), rollup=object(),
        subagent=object(), video=object(),
    )


def _surface(**kwargs):
    return _registry().surface(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), deliverer=_deliverer(),
        metrics=Metrics(), room=Room(platform="signal", conversation_id="g1",
                                     roster_source=None, persons=_FakePersons()),
        engagement=True, **kwargs,
    )


@pytest.mark.parametrize("ambient,expected", [(False, CALLED), (True, AMBIENT)])
@pytest.mark.parametrize("defer", [False, True])
def test_the_graph_surface_has_the_pinned_tools(ambient, expected, defer):
    surface = _surface(fire_turn=ambient, defer=defer)
    assert surface.admitted == expected
    names = {t.tool_name for t in surface.tools}
    assert names | surface.shelf.names() == expected | ({LOADER_TOOL} if defer else set())
    assert "intention_commit" in surface.resident


@pytest.mark.parametrize("ambient", [False, True])
def test_the_graph_dispute_verb_is_offered_on_the_engagement_surface_with_its_intake(ambient):
    assert "profile_dispute" not in _surface(fire_turn=ambient).admitted
    wired = ToolRegistry(
        engagement_graph_retrieval=object(), intention_admission=object(), memory_intake=object(),
    )
    assert "profile_dispute" in wired.surface(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), deliverer=_deliverer(),
        metrics=Metrics(), room=Room(platform="signal", conversation_id="g1",
                                     roster_source=None, persons=_FakePersons()),
        engagement=True, fire_turn=ambient,
    ).admitted


@pytest.mark.parametrize("ambient", [False, True])
def test_load_tools_cannot_reach_any_retired_tool(ambient):
    surface = _surface(fire_turn=ambient, defer=True)
    agent = Agent(tools=surface.tools)
    names = sorted({
        "checkpoint_update", "memo_read", "note_read", "note_search",
        "recall_lookups", "standing_read",
    })
    assert set(names) == LEGACY_ENGAGEMENT_TOOLS
    for i in range(0, len(names), 4):
        asked = names[i:i + 4]
        result = load_onto(agent, surface.shelf, asked)
        assert result["status"] == "nothing_found"
        assert result["unknown"] == asked
    assert load_onto(agent, surface.shelf, ["memory_read"])["loaded"] == ["memory_read"]


@pytest.mark.parametrize("ambient", [False, True])
def test_who_said_remains_loadable_on_the_engagement_surface(ambient):
    """The attribution read is not a legacy store read: the engagement surface
    keeps it available where granted (DEC-316)."""
    surface = _surface(fire_turn=ambient, defer=True)
    agent = Agent(tools=surface.tools)
    assert "who_said" in surface.admitted
    assert load_onto(agent, surface.shelf, ["who_said"])["loaded"] == ["who_said"]


def test_dreamers_read_only_grant_keeps_its_legacy_sources():
    source = RegistryToolSource(
        registry=_registry(), store=_FakeStore(), persons=_FakePersons(),
        metrics=Metrics(),
    )
    expected = {"load_transcript"}
    built = source.build(key="g1", adaptor="fixture", names=frozenset(expected | {"send_reply"}))
    assert {t.tool_name for t in built} == expected
