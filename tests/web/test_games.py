"""nora/web/games.py against a fake that behaves like the REAL
`strands.tools.mcp.mcp_client.MCPClient.call_tool_async` — status
"success"/"error", a `content` list carrying a JSON-string `text` (the
real, observed `pulse_server_status` shape — no `structuredContent`) — not
a fake that answers every call the same way (CLAUDE.md). It also
reproduces the real wrong-call shape: an unknown tool name comes back
`status="error"` from the session, it does not raise.

`SUCCESS_RESULT` below is the ACTUAL response captured live from Pulse's
`pulse_server_status` tool on 2026-08-17, after Pulse's own Caddy-sidecar
routing fix — not an invented shape. `_parse_status_payload`'s required-field
check is what would catch a future Pulse response that silently dropped a
field this deployment currently depends on.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nora.agent.mcp import McpToolGroup
from nora.web.games import (
    GamesUnavailableError,
    build_game_server_graph_payload,
    build_game_server_status_payload,
    build_games_servers_payload,
)


class FakeMcpClient:
    """Mirrors `MCPClient.call_tool_async`'s real return shape (an
    `MCPToolResult`-like dict) and its real failure shapes: an unconfigured
    response name comes back `status="error"` (the live session's own
    behaviour for an unrecognised call), and `raise_for` simulates the
    transport itself failing (a dropped connection, a 502) — a DIFFERENT
    failure mode from a tool-level error, and `nora.web.games` must handle
    both without raising past its own `GamesUnavailableError`."""

    def __init__(
        self, *, responses: dict[str, dict] | None = None,
        raise_for: frozenset[str] = frozenset(),
    ) -> None:
        self._responses = responses or {}
        self._raise_for = raise_for
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call_tool_async(
        self, tool_use_id: str, name: str, arguments: dict[str, Any] | None = None,
    ) -> dict:
        self.calls.append((tool_use_id, name, dict(arguments or {})))
        if name in self._raise_for:
            raise RuntimeError(f"transport failed calling {name!r}")
        if name not in self._responses:
            return {
                "status": "error", "toolUseId": tool_use_id,
                "content": [{"text": f"Unknown tool: {name}"}],
            }
        return self._responses[name]


class FakeMcpTool:
    def __init__(self, tool_name: str, client: FakeMcpClient) -> None:
        self.tool_name = tool_name
        self.mcp_client = client


def _group(*, tool_name: str = "pulse_server_status", client: FakeMcpClient) -> McpToolGroup:
    return McpToolGroup(budget=5, tools=(FakeMcpTool(tool_name, client),))


# Captured live from https://orion.tail0a1b2c3d.ts.net/pulse/mcp, 2026-08-17.
REAL_STATUS_PAYLOAD = {
    "server": "Abiotic Factor",
    "online": True,
    "status": "running",
    "uptimeHours": 394.3,
    "cpuPercent": 47,
    "memoryGb": 5.4,
    "playersOnline": [],
    "playerCount": 0,
    "maxPlayers": 12,
    "lastSampleAt": "2026-08-17T18:53:52.293Z",
}

SUCCESS_RESULT = {
    "status": "success",
    "toolUseId": "nora-web-games",
    "content": [{"text": json.dumps(REAL_STATUS_PAYLOAD)}],
    "isError": False,
}

ERROR_RESULT = {
    "status": "error",
    "toolUseId": "nora-web-games",
    "content": [{"text": "server is unreachable"}],
}


async def test_servers_unavailable_when_pulse_mount_absent():
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_games_servers_payload(mcp_tools={})


async def test_servers_unavailable_when_status_tool_missing_from_group():
    empty_group = McpToolGroup(budget=5, tools=())
    with pytest.raises(GamesUnavailableError, match="not configured"):
        await build_games_servers_payload(mcp_tools={"pulse": empty_group})


async def test_servers_calls_the_real_tool_with_no_server_argument_and_maps_it():
    client = FakeMcpClient(responses={"pulse_server_status": SUCCESS_RESULT})
    servers = await build_games_servers_payload(mcp_tools={"pulse": _group(client=client)})
    assert client.calls == [("nora-web-games", "pulse_server_status", {})]
    assert servers == [
        {
            "id": "abiotic_factor",
            "displayName": "Abiotic Factor",
            "color": "#f59e0b",
            "maxPlayers": 12,
            "current": {
                "online": True,
                "status": "running",
                "uptimeHours": 394.3,
                "cpuPercent": 47,
                "memoryGb": 5.4,
                "playerCount": 0,
                "lastSampleAt": "2026-08-17T18:53:52.293Z",
            },
        }
    ]


async def test_status_passes_the_server_id_as_the_server_argument_and_maps_it():
    client = FakeMcpClient(responses={"pulse_server_status": SUCCESS_RESULT})
    status = await build_game_server_status_payload(
        mcp_tools={"pulse": _group(client=client)}, server_id="abiotic_factor",
    )
    assert client.calls == [
        ("nora-web-games", "pulse_server_status", {"server": "abiotic_factor"}),
    ]
    assert status["server"]["displayName"] == "Abiotic Factor"
    assert status["current"]["cpuPercent"] == 47
    assert status["activePlayers"] == []


async def test_a_tool_level_error_result_becomes_unavailable_with_content():
    client = FakeMcpClient(responses={"pulse_server_status": ERROR_RESULT})
    with pytest.raises(GamesUnavailableError, match="server is unreachable"):
        await build_games_servers_payload(mcp_tools={"pulse": _group(client=client)})


async def test_a_transport_failure_becomes_unavailable_not_a_raw_exception():
    client = FakeMcpClient(raise_for=frozenset({"pulse_server_status"}))
    with pytest.raises(GamesUnavailableError, match="transport failed"):
        await build_games_servers_payload(mcp_tools={"pulse": _group(client=client)})


async def test_a_response_missing_a_depended_on_field_is_unavailable_not_partial():
    """The load-bearing test: if Pulse's response ever drops a field this
    module maps, that must surface as GamesUnavailableError, never a dict
    silently missing a key or filled with an invented default."""
    incomplete = dict(REAL_STATUS_PAYLOAD)
    del incomplete["cpuPercent"]
    result = {
        "status": "success", "toolUseId": "x",
        "content": [{"text": json.dumps(incomplete)}],
    }
    client = FakeMcpClient(responses={"pulse_server_status": result})
    with pytest.raises(GamesUnavailableError, match="missing field"):
        await build_games_servers_payload(mcp_tools={"pulse": _group(client=client)})


async def test_a_non_json_text_body_is_unavailable_not_a_raw_exception():
    result = {
        "status": "success", "toolUseId": "x",
        "content": [{"text": "not json"}],
    }
    client = FakeMcpClient(responses={"pulse_server_status": result})
    with pytest.raises(GamesUnavailableError, match="not valid JSON"):
        await build_games_servers_payload(mcp_tools={"pulse": _group(client=client)})


async def test_graph_is_unavailable_by_construction_no_matter_what_pulse_says():
    """There is no Pulse MCP tool for an hourly time series at all — this
    must stay unavailable even when the pulse mount is otherwise healthy,
    and it must never call a tool trying to find one."""
    client = FakeMcpClient(responses={"pulse_server_status": SUCCESS_RESULT})
    with pytest.raises(GamesUnavailableError, match="no hourly time-series tool"):
        await build_game_server_graph_payload(
            mcp_tools={"pulse": _group(client=client)}, server_id="abiotic_factor", hours=24,
        )
    assert client.calls == []
