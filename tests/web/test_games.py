"""`nora.web.games` against a fake that behaves like Pulse's REAL REST API —
real aiohttp routes, real status codes, real JSON bodies, not a mocked
transport (CLAUDE.md: never a fake that answers every request the same
way). Reverting the never-raises-a-raw-exception rule or the non-200/
non-JSON handling turns a test here red — the fake answers wrong the way
the real host would.

`SERVERS_BODY` below matches the real shape of Pulse's
`/pulse/api/v1/servers` (the full `ContainerMetricsSchema` cgroup fields
`app/src/lib/api/games.ts` validates client-side). `PULSE_API_TOKEN` is
bearer-authed, and distinct from the LLM tool-calling path's
`PULSE_MCP_TOKEN`.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.web.games import (
    GamesUnavailableError,
    PulseGamesClient,
    build_game_server_activity_payload,
    build_game_server_graph_payload,
    build_game_server_insights_payload,
    build_game_server_player_payload,
    build_game_server_players_payload,
    build_game_server_progress_payload,
    build_game_server_save_history_payload,
    build_game_server_sessions_payload,
    build_game_server_status_payload,
    build_game_server_weekly_payload,
    build_game_server_world_payload,
    build_games_servers_payload,
)

NO_PERSONS = Persons(by_person={}, index={})

CURRENT = {
    "serverId": "abiotic_factor",
    "timestamp": 1_755_000_000,
    "online": True,
    "status": "running",
    "cpuPercent": 47.0,
    "memoryBytes": 5_800_000_000,
    "memoryLimitBytes": 8_000_000_000,
    "networkRxBytes": 12_345,
    "networkTxBytes": 6_789,
    "networkRxBps": 100.0,
    "networkTxBps": 50.0,
    "blockReadBytes": 0,
    "blockWriteBytes": 0,
    "pids": 12,
    "restartCount": 0,
    "playerCount": 0,
    "startedAt": "2026-08-01T00:00:00.000Z",
}

SERVERS_BODY = [
    {
        "id": "abiotic_factor",
        "displayName": "Abiotic Factor",
        "color": "#f59e0b",
        "maxPlayers": 12,
        "current": CURRENT,
    }
]

STATUS_BODY = {
    "server": {
        "id": "abiotic_factor", "displayName": "Abiotic Factor",
        "color": "#f59e0b", "maxPlayers": 12,
    },
    "current": CURRENT,
    "activePlayers": [],
}

GRAPH_BODY = {
    "hours": 24, "from": 1_754_913_600, "to": 1_755_000_000,
    "data": [
        {
            "timestamp": 1_754_913_600, "online": 1, "cpuAvg": 40.0, "cpuMax": 55.0,
            "memoryAvg": 5_000_000_000.0, "memoryMax": 6_000_000_000.0,
            "memoryLimit": 8_000_000_000, "networkRxBpsAvg": 90.0, "networkTxBpsAvg": 40.0,
            "playerAvg": 0.0, "playerMax": 1.0,
        },
    ],
}


class _FakePulse:
    """Real aiohttp routes standing in for Pulse's REST API, keyed with a
    request log so a test can prove `server_id`/`hours` actually reach
    Pulse, plus per-route failure modes (a non-200, a non-JSON body) so the
    never-raises-past-GamesUnavailableError rule can be proven."""

    def __init__(self) -> None:
        self.servers_status = 200
        self.servers_body: object = SERVERS_BODY
        self.status_status = 200
        self.status_body: object = STATUS_BODY
        self.graph_status = 200
        self.graph_body: object = GRAPH_BODY
        self.seen: list[tuple[str, str]] = []
        # Merged into every `echo` response — lets a test simulate Pulse
        # sending a field (e.g. `steamId`) without a bespoke handler.
        self.echo_extra_fields: dict[str, object] = {}

    async def servers(self, request: web.Request) -> web.Response:
        self.seen.append((request.path, request.query_string))
        if self.servers_status != 200:
            return web.Response(status=self.servers_status)
        if self.servers_body == "notjson":
            return web.Response(text="<html>not json</html>", content_type="text/html")
        return web.json_response(self.servers_body)

    async def status(self, request: web.Request) -> web.Response:
        self.seen.append((request.path, request.query_string))
        if self.status_status != 200:
            return web.Response(status=self.status_status)
        return web.json_response(self.status_body)

    async def graph(self, request: web.Request) -> web.Response:
        self.seen.append((request.path, request.query_string))
        if self.graph_status != 200:
            return web.Response(status=self.graph_status)
        return web.json_response(self.graph_body)

    async def echo(self, request: web.Request) -> web.Response:
        """A generic pass-through handler for the routes that are pure
        proxies with no client-side reshaping (weekly, world, insights,
        players, player, activity, sessions) — echoes back the path and
        every query param it received, so a test can prove the client
        forwards `page`/`pageSize`/`online`/`from`/`to`/`playerKey`
        correctly without one bespoke handler per route."""
        self.seen.append((request.path, request.query_string))
        return web.json_response({
            "path": request.path, "query": dict(request.query), **self.echo_extra_fields,
        })


@pytest.fixture
async def pulse():
    fake = _FakePulse()
    app = web.Application()
    app.router.add_get("/v1/servers", fake.servers)
    app.router.add_get("/v1/servers/{id}/status", fake.status)
    app.router.add_get("/v1/servers/{id}/history", fake.graph)
    app.router.add_get("/v1/servers/{id}/weekly", fake.echo)
    app.router.add_get("/v1/servers/{id}/world", fake.echo)
    app.router.add_get("/v1/servers/{id}/progress", fake.echo)
    app.router.add_get("/v1/servers/{id}/save-history", fake.echo)
    app.router.add_get("/v1/servers/{id}/insights", fake.echo)
    app.router.add_get("/v1/servers/{id}/players", fake.echo)
    app.router.add_get("/v1/servers/{id}/players/{playerKey}", fake.echo)
    app.router.add_get("/v1/servers/{id}/activity", fake.echo)
    app.router.add_get("/v1/servers/{id}/sessions", fake.echo)
    server = TestServer(app)
    await server.start_server()
    base_url = str(server.make_url("/v1")).rstrip("/")
    client = PulseGamesClient(
        token="test-token", base_url=base_url, timeout_seconds=5.0,  # noqa: S106
    )
    yield fake, client
    await server.close()


async def test_servers_are_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_games_servers_payload(client=None)


async def test_status_is_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_game_server_status_payload(client=None, server_id="abiotic_factor")


async def test_graph_is_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_game_server_graph_payload(client=None, server_id="abiotic_factor", hours=24)


async def test_servers_calls_the_real_route_and_passes_the_body_through(pulse):
    fake, client = pulse
    servers = await build_games_servers_payload(client=client)
    assert fake.seen == [("/v1/servers", "")]
    assert servers == SERVERS_BODY


async def test_status_calls_the_server_scoped_route_and_passes_the_body_through(pulse):
    fake, client = pulse
    status = await build_game_server_status_payload(client=client, server_id="abiotic_factor")
    assert fake.seen == [("/v1/servers/abiotic_factor/status", "")]
    assert status == STATUS_BODY


async def test_graph_forwards_the_hours_param_and_passes_the_body_through(pulse):
    fake, client = pulse
    graph = await build_game_server_graph_payload(
        client=client, server_id="abiotic_factor", hours=48,
    )
    assert fake.seen == [("/v1/servers/abiotic_factor/history", "hours=48")]
    assert graph == GRAPH_BODY


async def test_a_non_200_from_pulse_becomes_unavailable_not_a_raw_exception(pulse):
    fake, client = pulse
    fake.servers_status = 500
    with pytest.raises(GamesUnavailableError, match="answered 500"):
        await build_games_servers_payload(client=client)


async def test_a_non_json_body_becomes_unavailable_not_a_raw_exception(pulse):
    fake, client = pulse
    fake.servers_body = "notjson"
    with pytest.raises(GamesUnavailableError, match="did not return JSON"):
        await build_games_servers_payload(client=client)


async def test_servers_route_returning_a_non_list_body_is_unavailable(pulse):
    fake, client = pulse
    fake.servers_body = {"not": "a list"}
    with pytest.raises(GamesUnavailableError, match="non-list"):
        await build_games_servers_payload(client=client)


async def test_status_route_returning_a_non_dict_body_is_unavailable(pulse):
    fake, client = pulse
    fake.status_body = ["not", "a", "dict"]
    with pytest.raises(GamesUnavailableError, match="non-dict"):
        await build_game_server_status_payload(client=client, server_id="abiotic_factor")


async def test_a_connection_failure_becomes_unavailable_not_a_raw_exception():
    client = PulseGamesClient(
        token="test-token", base_url="http://127.0.0.1:1", timeout_seconds=1.0,  # noqa: S106
    )
    with pytest.raises(GamesUnavailableError, match="request failed"):
        await build_games_servers_payload(client=client)


async def test_weekly_calls_the_server_scoped_route(pulse):
    fake, client = pulse
    body = await build_game_server_weekly_payload(client=client, server_id="abiotic_factor")
    assert body["path"] == "/v1/servers/abiotic_factor/weekly"
    assert fake.seen == [("/v1/servers/abiotic_factor/weekly", "")]


async def test_world_calls_the_server_scoped_route(pulse):
    _fake, client = pulse
    body = await build_game_server_world_payload(
        client=client, server_id="abiotic_factor", persons=NO_PERSONS,
    )
    assert body["path"] == "/v1/servers/abiotic_factor/world"


async def test_world_players_are_enriched_with_a_linked_person_when_steam_id_resolves(pulse):
    fake, client = pulse
    fake.echo_extra_fields = {"players": [{"steamId": "76561190000000001", "walkingProven": True}]}
    persons = Persons(
        by_person={
            "aaron_larsen": PersonRef(
                person_id="aaron_larsen", name="Aaron", honcho_peer_id="aaron_larsen",
            ),
        },
        index={("pulse", "76561190000000001"): "aaron_larsen"},
    )

    body = await build_game_server_world_payload(
        client=client, server_id="big_walk", persons=persons,
    )

    assert body["players"][0]["linkedPerson"] == {
        "personId": "aaron_larsen", "displayName": "Aaron"
    }
    assert body["players"][0]["walkingProven"] is True


async def test_world_players_have_no_linked_person_when_steam_id_does_not_resolve(pulse):
    fake, client = pulse
    fake.echo_extra_fields = {"players": [{"steamId": "76561190000000001"}]}

    body = await build_game_server_world_payload(
        client=client, server_id="big_walk", persons=NO_PERSONS,
    )

    assert "linkedPerson" not in body["players"][0]


async def test_world_with_no_players_key_passes_through_unchanged(pulse):
    _fake, client = pulse
    body = await build_game_server_world_payload(
        client=client, server_id="abiotic_factor", persons=NO_PERSONS,
    )
    assert "players" not in body


async def test_insights_forwards_the_days_param(pulse):
    _fake, client = pulse
    body = await build_game_server_insights_payload(
        client=client, server_id="abiotic_factor", days=7,
    )
    assert body["query"] == {"days": "7"}


async def test_players_forwards_page_page_size_and_online(pulse):
    _fake, client = pulse
    body = await build_game_server_players_payload(
        client=client, server_id="abiotic_factor", page=2, page_size=10, online=True,
    )
    assert body["query"] == {"page": "2", "pageSize": "10", "online": "true"}


async def test_players_omits_online_when_not_given(pulse):
    _fake, client = pulse
    body = await build_game_server_players_payload(
        client=client, server_id="abiotic_factor", page=1, page_size=20, online=None,
    )
    assert "online" not in body["query"]


async def test_player_url_encodes_a_player_key_containing_colons(pulse):
    _fake, client = pulse
    body = await build_game_server_player_payload(
        client=client, server_id="abiotic_factor",
        player_key="id:eos:00021fe1d55b4d73ab57c2f70a1726a8", persons=NO_PERSONS,
    )
    assert body["path"] == (
        "/v1/servers/abiotic_factor/players/id:eos:00021fe1d55b4d73ab57c2f70a1726a8"
    )


async def test_player_is_enriched_with_a_linked_person_when_steam_id_resolves(pulse):
    fake, client = pulse
    fake.echo_extra_fields = {"steamId": "76561190000000001"}
    persons = Persons(
        by_person={
            "aaron_larsen": PersonRef(
                person_id="aaron_larsen", name="Aaron", honcho_peer_id="aaron_larsen",
            ),
        },
        index={("pulse", "76561190000000001"): "aaron_larsen"},
    )

    body = await build_game_server_player_payload(
        client=client, server_id="abiotic_factor", player_key="id:eos:x", persons=persons,
    )

    assert body["linkedPerson"] == {"personId": "aaron_larsen", "displayName": "Aaron"}


async def test_player_has_no_linked_person_when_steam_id_does_not_resolve(pulse):
    fake, client = pulse
    fake.echo_extra_fields = {"steamId": "76561190000000001"}

    body = await build_game_server_player_payload(
        client=client, server_id="abiotic_factor", player_key="id:eos:x", persons=NO_PERSONS,
    )

    assert "linkedPerson" not in body


async def test_player_has_no_linked_person_when_pulse_sends_no_steam_id(pulse):
    _fake, client = pulse
    persons = Persons(
        by_person={
            "aaron_larsen": PersonRef(
                person_id="aaron_larsen", name="Aaron", honcho_peer_id="aaron_larsen",
            ),
        },
        index={("pulse", "76561190000000001"): "aaron_larsen"},
    )

    body = await build_game_server_player_payload(
        client=client, server_id="abiotic_factor", player_key="id:eos:x", persons=persons,
    )

    assert "linkedPerson" not in body


async def test_activity_forwards_pagination_and_time_range(pulse):
    _fake, client = pulse
    body = await build_game_server_activity_payload(
        client=client, server_id="abiotic_factor",
        page=3, page_size=50, since_ms=1_000, until_ms=2_000,
    )
    assert body["query"] == {"page": "3", "pageSize": "50", "from": "1000", "to": "2000"}


async def test_sessions_forwards_player_key_and_time_range(pulse):
    _fake, client = pulse
    body = await build_game_server_sessions_payload(
        client=client, server_id="abiotic_factor", page=1, page_size=50,
        player_key="id:eos:0002aa7e6a4e454395f5427c1c61daa3", since_ms=None, until_ms=None,
    )
    assert body["query"] == {
        "page": "1", "pageSize": "50", "playerKey": "id:eos:0002aa7e6a4e454395f5427c1c61daa3",
    }


async def test_weekly_is_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_game_server_weekly_payload(client=None, server_id="abiotic_factor")


async def test_world_is_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_game_server_world_payload(
            client=None, server_id="abiotic_factor", persons=NO_PERSONS,
        )


async def test_insights_is_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_game_server_insights_payload(client=None, server_id="abiotic_factor", days=30)


async def test_players_is_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_game_server_players_payload(
            client=None, server_id="abiotic_factor", page=1, page_size=20, online=None,
        )


async def test_player_is_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_game_server_player_payload(
            client=None, server_id="abiotic_factor", player_key="x", persons=NO_PERSONS,
        )


async def test_activity_is_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_game_server_activity_payload(
            client=None, server_id="abiotic_factor",
            page=1, page_size=20, since_ms=None, until_ms=None,
        )


async def test_sessions_is_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_game_server_sessions_payload(
            client=None, server_id="abiotic_factor", page=1, page_size=20,
            player_key=None, since_ms=None, until_ms=None,
        )


async def test_progress_calls_the_server_scoped_route(pulse):
    fake, client = pulse
    body = await build_game_server_progress_payload(client=client, server_id="big_walk")
    assert body["path"] == "/v1/servers/big_walk/progress"
    assert fake.seen == [("/v1/servers/big_walk/progress", "")]


async def test_progress_is_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_game_server_progress_payload(client=None, server_id="big_walk")


async def test_save_history_forwards_the_hours_param(pulse):
    fake, client = pulse
    body = await build_game_server_save_history_payload(
        client=client, server_id="big_walk", hours=72,
    )
    assert body["query"] == {"hours": "72"}
    assert fake.seen == [("/v1/servers/big_walk/save-history", "hours=72")]


async def test_save_history_is_unavailable_when_no_client_is_configured():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_game_server_save_history_payload(client=None, server_id="big_walk", hours=168)
