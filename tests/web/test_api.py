import pytest
from aiohttp import web

from nora.agent.mcp import McpToolGroup
from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.testing import FakeRecordStore
from nora.testing.reminders import FakeReminderStore
from nora.web.api import register_web_routes
from tests.fakes import FakeHoncho

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")


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
        {"person_id": "miles", "display_name": "Miles", "reachable": True, "conclusions": ["x"]},
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
    )
    return await aiohttp_client(app)


async def test_games_servers_route_is_still_503_with_pulse_mounted_but_no_tool(
    client_with_pulse,
):
    """The mount exists but advertises no tools (e.g. discovery partially
    failed) — still an honest 503, not a crash and not a fabricated 200."""
    resp = await client_with_pulse.get("/app/v1/games/servers")
    assert resp.status == 503
