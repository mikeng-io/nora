"""Pin the provider-facing tool inventory and measure what reaches its clients."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nora.agent.catalog.entries import NATIVE_TOOLS
from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.computer import ComputerToolSettings
from nora.domain.models import PersonRef
from nora.domain.ports import ImageResult, MusicToolOutcome
from nora.memory import act_event
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import FakeBlobStore, FakeComputerEventStore, FakeSandboxRuntime
from nora.testing.external_files import FakeExternalFilesSource, FakeFile
from nora.testing.fakes import FakeRecordStore
from tests.unit.test_engagement_egress import PRIVATE, _authority, _show

ROOT = Path(__file__).resolve().parents[2] / "nora"
OUTSIDE = frozenset({
    "computer_import", "file_fetch", "find_parking", "generate_image", "instagram_read",
    "instagram_search", "linkedin_read", "linkedin_search", "media_fetch", "place_info",
    "play_music", "reverse_geocode", "route", "search_gifs", "transit", "video_create",
    "web_fetch", "web_search", "x_read", "x_search",
})
OUTSIDE_SOURCES = frozenset({
    "external_files", "fetch_source", "geocoder", "giphy_source", "image_generator",
    "linkedin_source", "music", "parking_source", "route_source", "search_source",
    "social_source", "street_parking_source", "transit_source", "video", "vision_source",
    "weather_source",
})
# These resolve configured IDs or serve Nora-owned state.
LOCAL_SOURCES = frozenset({
    "documents", "online_source", "presence", "presence_source", "refresher", "voice_source",
})
# These exemptions end if a wired provider starts sending the model argument.
LOCAL_ARGUMENTS = {
    "parking_status": "parking_source",
    "weather": "weather_source",
    "send_gif": "giphy_source",  # Fetches an already issued candidate; Deliverer scans the caption.
    "where_is": "geocoder",  # An unused collaborator; the person is resolved locally.
    "music_status": "music",
    "set_music_volume": "music",
    "skip_music": "music",
    "stop_music": "music",
    "top_music": "music",
}


def test_outside_flags_equal_the_reviewed_inventory():
    assert {name for name, tool in NATIVE_TOOLS.items() if tool.outside} == OUTSIDE


def test_outside_provider_reads_require_an_outside_flag():
    from nora.agent.tool_deps import ToolDeps

    assert set(ToolDeps.__dataclass_fields__) >= OUTSIDE_SOURCES | LOCAL_SOURCES
    assert not OUTSIDE_SOURCES & LOCAL_SOURCES
    found = set()
    missing = set()
    # Aliased reads such as web.py's fetch_client escape this AST rule. OUTSIDE is the binding pin.
    for path in ROOT.rglob("*.py"):
        if "tools" not in path.parts and path.name != "tools.py":
            continue
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("_make_"):
                continue
            name = node.name.removeprefix("_make_")
            if name.startswith("_"):
                continue
            for attr in ast.walk(node):
                if not (
                    isinstance(attr, ast.Attribute) and isinstance(attr.value, ast.Name)
                    and attr.value.id == "deps" and attr.attr in OUTSIDE_SOURCES
                ):
                    continue
                if LOCAL_ARGUMENTS.get(name) == attr.attr:
                    found.add(name)
                elif name not in NATIVE_TOOLS or not NATIVE_TOOLS[name].outside:
                    missing.add((name, attr.attr))
    assert found == LOCAL_ARGUMENTS.keys()
    assert missing == set()


PHONE = f"+1{202}{555}{101:04d}"
FILE_ID = "1SyntheticEgressFile00000"
FILE_URL = f"https://docs.google.com/document/d/{FILE_ID}/edit"
PLACE = "Central Library"
PERSON = PersonRef("test-person", "Juniper", "test-person")

# A bare name or a non-identifier handle can pass. Name screens follow the content-filter decision.
# reverse_geocode has only float arguments. Its coordinate provenance is a separate boundary.
PROBES = {
    "computer_import": ("link", FILE_URL, "files", {}),
    "file_fetch": ("link", FILE_URL, "files", {}),
    "find_parking": ("place", PLACE, "locate", {}),
    "generate_image": ("prompt", "A painting of a lighthouse", "generate", {}),
    "instagram_read": ("link", "https://www.instagram.com/example/", "social", {}),
    "instagram_search": ("query", "example", "social", {}),
    "linkedin_read": ("link", "https://www.linkedin.com/in/example/", "linkedin", {}),
    "linkedin_search": ("query", "example", "linkedin", {}),
    "media_fetch": ("question", "What colour is the boat?", "files", {"url": FILE_URL}),
    "parking_status": ("name", "Harbour Car Park", "vacancy", {}),
    "place_info": ("name", PLACE, "place", {}),
    "play_music": ("query", "Ocean overture", "play", {}),
    "route": ("origin", PLACE, "route", {"destination": "Town Hall"}),
    "search_gifs": ("query", "a little wave", "gifs", {}),
    "transit": ("near", PLACE, "arrivals", {"route": "1A"}),
    "video_create": ("prompt", "A boat at sunrise", "video", {}),
    "weather": ("place", "Kowloon", "weather", {}),
    "web_fetch": ("url", "https://example.org/public", "fetch", {}),
    "web_search": ("query", "ferry timetables", "search", {}),
    "x_read": ("link", "https://x.com/example", "social", {}),
    "x_search": ("query", "ferry timetables", "social", {}),
}


def _world(*, content_checks=True):
    files = FakeExternalFilesSource(files={
        FILE_ID: FakeFile("Plan", parts=("A public plan.",), data=b"public plan"),
    })

    def lookup(*args, **kwargs):
        if not (args or kwargs):
            raise TypeError("a provider needs a request")
        return {"status": "nothing_found", "note": "No match in the provider fixture."}

    calls = {
        name: AsyncMock(side_effect=lookup)
        for name in ("arrivals", "fetch", "linkedin", "place", "route", "search", "social")
    }
    calls.update(
        generate=AsyncMock(side_effect=lambda *, prompt, **_: ImageResult(
            "ok", artifact="https://example.org/artifact") if "lighthouse" in prompt
            else ImageResult("nothing_found")),
        gifs=AsyncMock(side_effect=lambda *, query: [] if query else None),
        locate=AsyncMock(side_effect=lambda place: SimpleNamespace(
            status="not_found", place=None) if place else None),
        play=AsyncMock(side_effect=lambda *, query_or_url, **_: MusicToolOutcome(
            "nothing_found" if query_or_url else "unavailable")),
        vacancy=AsyncMock(side_effect=lambda *, name: (
            {"vacancy": 7} if name == "Harbour Car Park" else None)),
        video=AsyncMock(side_effect=lambda *, request, **_: SimpleNamespace(
            status="unavailable" if request.prompt else "refused", reason="")),
        weather=AsyncMock(side_effect=lambda *, place, **_: (
            None if place else pytest.fail("expected a place"))),
    )
    persons = Persons(
        by_person={PERSON.person_id: PERSON},
        index={("discord", "100000000000000001"): PERSON.person_id},
    )
    room = Room(platform="discord", conversation_id="guild/room", roster_source=None,
                persons=persons, tagger=PERSON)
    tools = {tool.name: tool.call for tool in build_tool_specs(
        store=FakeRecordStore(), conversation_id="guild/room", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(), room=room,
        content_checks=content_checks,
        unit_for=lambda *_: "unit", transit_source=SimpleNamespace(arrivals=calls["arrivals"]),
        route_source=SimpleNamespace(
            route=calls["route"], place_info=calls["place"],
            _resolver=SimpleNamespace(resolve=calls["locate"]),
        ),
        fetch_source=SimpleNamespace(fetch=calls["fetch"]),
        search_source=SimpleNamespace(search=calls["search"]),
        social_source=SimpleNamespace(read=calls["social"]),
        linkedin_source=SimpleNamespace(read=calls["linkedin"]),
        weather_source=SimpleNamespace(current=calls["weather"]),
        parking_source=SimpleNamespace(vacancy=calls["vacancy"]),
        giphy_source=SimpleNamespace(search=calls["gifs"]),
        music=SimpleNamespace(play=calls["play"]),
        image_generator=SimpleNamespace(generate=calls["generate"], max_reference_images=0),
        video=SimpleNamespace(create=calls["video"]),
        vision_source=SimpleNamespace(), blob_store=FakeBlobStore(), external_files=files,
        computer=FakeSandboxRuntime(), computer_events=FakeComputerEventStore(),
        computer_settings=ComputerToolSettings(),
    )}
    return tools, calls, files


@pytest.mark.parametrize("name", sorted(PROBES))
async def test_provider_tools_refuse_phone_before_the_client(name):
    assert set(PROBES) == (OUTSIDE - {"reverse_geocode"}) | {"weather", "parking_status"}
    tools, calls, files = _world()
    argument, public, backend, kwargs = PROBES[name]

    def count():
        return len(files.calls) if backend == "files" else calls[backend].await_count

    private = f"{public}?private={PHONE}" if public.startswith("https://") else PHONE
    denied = await tools[name](**{argument: private, **kwargs})
    assert denied["status"] in {"refused", "nothing_found", "not_found", "unavailable"}
    assert count() == 0
    await tools[name](**{argument: public, **kwargs})
    assert count() > 0


async def test_an_outside_query_refuses_an_internal_quote():
    tools, calls, _ = _world(content_checks=False)
    with act_event("pass-1", _authority("called")):
        _show()
        assert (await tools["web_search"](query=PRIVATE))["status"] == "nothing_found"
        calls["search"].assert_not_awaited()
        await tools["web_search"](query="ferry timetables")
        calls["search"].assert_awaited_once_with(query="ferry timetables")
