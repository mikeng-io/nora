import pytest
from aiohttp import web

from nora.agent.mcp import McpToolGroup
from nora.agent.whereis import Circle
from nora.domain.models import PersonRef
from nora.domain.presence import PresenceFix, WireState
from nora.persons import Persons
from nora.testing import FakeRecordStore
from nora.testing.reminders import FakeReminderStore
from nora.web.api import register_web_routes
from tests.fakes import FakeHoncho

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")


class FakePresenceReader:
    """Conforms to `nora.agent.whereis.PresenceReader` — the same narrow
    protocol `Whereis` itself programs against, not the concrete SQL store."""

    def __init__(self, *, fixes: dict[str, PresenceFix] | None = None,
                 windows: dict[str, list[PresenceFix]] | None = None) -> None:
        self._fixes = fixes or {}
        self._windows = windows or {}

    async def latest_fix(self, *, circle_id: str, person_id: str) -> PresenceFix | None:
        return self._fixes.get(person_id)

    async def places_for(self, *, circle_id: str) -> list:
        return []

    async def window(
        self, *, circle_id: str, person_id: str, since_ts: int, until_ts: int
    ) -> list[PresenceFix]:
        return self._windows.get(person_id, [])


@pytest.fixture
async def client(aiohttp_client):
    app = web.Application()
    register_web_routes(
        app, store=FakeRecordStore(), honcho=FakeHoncho(conclusions={"miles": ["x"]}),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"miles": MILES}, index={}),
        ai_peer="nora", started_at=0.0,
        # Empty — the same "not configured/reachable" state build_mcp_tools()
        # returns for a deployment with no `pulse` mount, or one whose token
        # was rejected. Games routes must degrade to 503, never fabricate 200s.
        mcp_tools={},
        presence=None, presence_circles=(),
    )
    return await aiohttp_client(app)


async def test_status_route_returns_200_with_the_expected_shape(client):
    resp = await client.get("/app/v1/status")
    assert resp.status == 200
    body = await resp.json()
    assert set(body) == {"uptime_seconds", "store", "honcho", "reminders"}


async def test_memory_route_returns_200_with_every_person(client):
    resp = await client.get("/app/v1/memory")
    assert resp.status == 200
    body = await resp.json()
    assert body == [
        {
            "person_id": "miles", "display_name": "Miles", "reachable": True,
            "identity": [], "episodes": ["x"],
        },
    ]


async def test_games_servers_route_is_503_when_pulse_is_not_wired(client):
    """Matches app/src/lib/games-schema.ts's fetchServers(): a non-2xx makes
    it throw BEFORE `.parse()` ever runs, so a 503 needs no schema-shaped
    body — nora/web/games.py's module docstring explains why this is the
    honest state today."""
    resp = await client.get("/app/v1/games/servers")
    assert resp.status == 503
    body = await resp.json()
    assert "error" in body


async def test_games_server_status_route_is_503_when_pulse_is_not_wired(client):
    resp = await client.get("/app/v1/games/servers/abiotic1/status")
    assert resp.status == 503
    body = await resp.json()
    assert "error" in body


async def test_games_server_graph_route_is_503_regardless_of_pulse(client):
    resp = await client.get("/app/v1/games/servers/abiotic1/graph")
    assert resp.status == 503
    body = await resp.json()
    assert "error" in body


async def test_games_server_graph_route_rejects_a_non_numeric_hours(client):
    resp = await client.get("/app/v1/games/servers/abiotic1/graph?hours=nope")
    assert resp.status == 400


@pytest.fixture
async def client_with_pulse(aiohttp_client):
    """Same fixture as `client`, but with a `pulse` mount present — proves
    the games routes actually reach into `mcp_tools` rather than always
    hitting the "mount absent" branch of GamesUnavailableError."""
    app = web.Application()
    register_web_routes(
        app, store=FakeRecordStore(), honcho=FakeHoncho(),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"miles": MILES}, index={}),
        ai_peer="nora", started_at=0.0,
        mcp_tools={"pulse": McpToolGroup(budget=5, tools=())},
        presence=None, presence_circles=(),
    )
    return await aiohttp_client(app)


async def test_games_servers_route_is_still_503_with_pulse_mounted_but_no_tool(
    client_with_pulse,
):
    """The mount exists but advertises no tools (e.g. discovery partially
    failed) — still an honest 503, not a crash and not a fabricated 200."""
    resp = await client_with_pulse.get("/app/v1/games/servers")
    assert resp.status == 503


async def test_presence_route_returns_empty_list_when_presence_is_disabled(client):
    resp = await client.get("/app/v1/presence")
    assert resp.status == 200
    assert await resp.json() == []


async def test_presence_history_route_is_404_when_presence_is_disabled(client):
    """No circle at all → nobody is a known circle member — a 404, not an
    empty history, matching build_presence_history_payload's own contract."""
    resp = await client.get("/app/v1/presence/miles/history")
    assert resp.status == 404


@pytest.fixture
async def client_with_presence(aiohttp_client):
    circle = Circle(circle_id="c1", person_ids=frozenset({"miles", "ada"}), people=(MILES, ADA))
    fix = PresenceFix(
        circle_id="c1", person_id="miles", ts=1_755_000_000_000, lat=22.3, lng=114.2,
        wire_state=WireState.LIVE, recorded_at=1_755_000_000_000, battery=80.0,
    )
    older = PresenceFix(
        circle_id="c1", person_id="miles", ts=1_754_999_000_000, lat=22.30001, lng=114.20001,
        wire_state=WireState.LIVE, recorded_at=1_754_999_000_000,
    )
    reader = FakePresenceReader(
        fixes={"miles": fix}, windows={"miles": [older, fix]},
    )
    app = web.Application()
    register_web_routes(
        app, store=FakeRecordStore(), honcho=FakeHoncho(),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"miles": MILES, "ada": ADA}, index={}),
        ai_peer="nora", started_at=0.0,
        mcp_tools={},
        presence=reader, presence_circles=(circle,),
    )
    return await aiohttp_client(app)


async def test_presence_route_lists_every_circle_member(client_with_presence):
    resp = await client_with_presence.get("/app/v1/presence")
    assert resp.status == 200
    body = await resp.json()
    by_id = {entry["personId"]: entry for entry in body}
    assert set(by_id) == {"miles", "ada"}
    assert by_id["miles"]["current"]["lat"] == 22.3
    assert by_id["miles"]["current"]["wireState"] == "live"
    assert by_id["miles"]["current"]["batteryPercent"] == 80.0
    assert by_id["ada"]["current"] is None


async def test_presence_history_route_snaps_stay_points_within_10m(client_with_presence):
    """The two fixes in the fake's window are ~1.4m apart — well under the
    10m stay-point radius — so the route must collapse them to ONE point,
    proving the snapping actually runs at the route layer, not just in a
    unit test of the pure function."""
    resp = await client_with_presence.get("/app/v1/presence/miles/history?hours=24")
    assert resp.status == 200
    body = await resp.json()
    assert len(body["points"]) == 1
    assert body["points"][0]["lat"] == 22.30001


async def test_presence_history_route_404s_for_a_non_circle_member(client_with_presence):
    resp = await client_with_presence.get("/app/v1/presence/stranger/history")
    assert resp.status == 404


async def test_presence_history_route_rejects_a_non_numeric_hours(client_with_presence):
    resp = await client_with_presence.get("/app/v1/presence/miles/history?hours=nope")
    assert resp.status == 400


async def test_presence_history_route_rejects_a_non_positive_hours(client_with_presence):
    resp = await client_with_presence.get("/app/v1/presence/miles/history?hours=0")
    assert resp.status == 400


async def test_presence_places_route_returns_empty_list_when_presence_is_disabled(client):
    resp = await client.get("/app/v1/presence/places")
    assert resp.status == 200
    assert await resp.json() == []
