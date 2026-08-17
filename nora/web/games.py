"""GET /app/v1/games/servers, .../{id}/status, .../{id}/graph — Pulse
game-server data through Nora's ALREADY-WORKING MCP connection
(`[tools.mcp.pulse]`, `nora.agent.mcp.build_mcp_tools`), replacing the
previous direct-REST proxy the Next.js `app` service used
(`GAMES_API_BASE` -> Pulse's HTTP port), which was fragile because that port
is host-loopback-only by design and unreachable from a separate compose
project.

BLOCKED on the response shape — read before changing this file
=================================================================
Pulse's live MCP tool inventory was discovered for real (not guessed) by
connecting `nora.agent.mcp.build_mcp_tools` to the deployed
`[tools.mcp.pulse]` mount and calling `list_tools_sync()`. It advertises
seven tools: `pulse_server_status`, `pulse_player_profile`,
`pulse_squad_activity`, `pulse_leaderboards`, `pulse_world_state`,
`pulse_recent_events`, `pulse_weekly`. Their `inputSchema`s were captured
live. That surface does not line up with what
`app/src/lib/games-schema.ts` needs, on its own description alone:

  - No tool lists MULTIPLE servers — every one of the seven takes an
    optional `server` argument documented as "Defaults to the only
    configured server; omit it unless asked about another." This MCP mount
    speaks for ONE server, not a fleet the way `ServerSchema[]` implies.
  - No tool returns an hourly time series. The closest is `pulse_weekly`
    (one aggregate per week) and `pulse_recent_events` (a discrete feed) —
    neither is `GraphSchema`'s per-hour cpuAvg/cpuMax/memoryAvg/memoryMax/
    networkRxBpsAvg/networkTxBpsAvg/playerAvg/playerMax buckets.
  - `pulse_server_status`'s own description promises "online or not,
    uptime, CPU and memory, and who is playing right now" — not the
    low-level Docker cgroup counters `ContainerMetricsSchema` requires as
    non-optional fields (memoryLimitBytes, networkRxBytes/TxBytes,
    blockReadBytes/WriteBytes, pids, restartCount, image).

What is genuinely UNKNOWN is the tool's OUTPUT shape: after that one
successful `list_tools_sync()` call,
`https://orion.tail0a1b2c3d.ts.net/pulse/mcp` returned `502 Bad Gateway` on
every one of 15 retry attempts spread over roughly five minutes, so no real
`pulse_server_status` response was ever observed in this environment.
Guessing its field names to satisfy `ServerSchema`/`ServerStatusSchema`
would be exactly the fabrication CLAUDE.md's fake-that-answers-everything
rule forbids ("never assert what you cannot ground").

So: this module wires the REAL call — the same `McpToolGroup`/`MCPClient`
path `nora.agent.registry.ToolRegistry` already uses for the LLM
tool-calling loop, reached through `Dependencies.mcp_tools` — and fails
closed. Every endpoint raises `GamesUnavailableError` (the route layer turns
that into a 503, R-27's "unavailable", never a fabricated 200) until:

  1. `pulse_server_status`'s real output has been observed (fill in
     `_server_from_status_result` below with its ACTUAL field names, not
     invented ones), for `build_games_servers_payload` and
     `build_game_server_status_payload`; and
  2. Pulse's MCP surface grows a tool that can answer an hourly graph
     (there is currently no such tool at all — `build_game_server_graph_payload`
     is unavailable by construction, not by connectivity).

None of the frontend's zod schemas needed to change: a 503 makes
`fetchServers()`/`fetchServerStatus()`/`fetchServerGraph()`
(`app/src/lib/games-schema.ts`) throw on `!res.ok`, before `.parse()` ever
runs — the existing "throw on non-2xx" contract IS the honest state here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nora.agent.mcp import McpToolGroup

_MOUNT = "pulse"
_STATUS_TOOL = "pulse_server_status"


class GamesUnavailableError(Exception):
    """Pulse's MCP tools are absent, unreachable, returned an error, or
    their output shape is not yet mapped (see module docstring). The web
    route (`nora.web.api`) turns this into a 503 — never a guessed 200."""


def _find_tool(mcp_tools: Mapping[str, McpToolGroup], tool_name: str) -> Any | None:
    group = mcp_tools.get(_MOUNT)
    if group is None:
        return None
    for tool in group.tools:
        if tool.tool_name == tool_name:
            return tool
    return None


async def _call_status_tool(
    *, mcp_tools: Mapping[str, McpToolGroup], server: str | None,
) -> dict:
    """Call the real `pulse_server_status` MCP tool and return its raw
    `MCPToolResult` (a `TypedDict` with `status`/`content`/`toolUseId` and,
    when the server declares one, `structuredContent`) — the exact shape
    `strands.tools.mcp.mcp_client.MCPClient.call_tool_async` returns,
    verified against that library's source (`_handle_tool_result`).
    Raises `GamesUnavailableError` for every failure mode; never returns a
    partial or invented result."""
    tool = _find_tool(mcp_tools, _STATUS_TOOL)
    if tool is None:
        raise GamesUnavailableError(
            "the pulse MCP mount is not configured, unreachable, or its "
            f"{_STATUS_TOOL!r} tool did not resolve — see build_mcp_tools()"
        )
    arguments: dict[str, Any] = {"server": server} if server else {}
    try:
        result = await tool.mcp_client.call_tool_async(
            tool_use_id="nora-web-games", name=_STATUS_TOOL, arguments=arguments,
        )
    except Exception as exc:
        # Never raise a raw transport/library exception from a web read — the
        # same posture status.py/memory.py already hold, folded into our own
        # typed failure so the route layer has exactly one thing to catch.
        raise GamesUnavailableError(f"{_STATUS_TOOL} call raised: {exc}") from exc
    if result.get("status") == "error":
        raise GamesUnavailableError(f"{_STATUS_TOOL} returned an error: {result.get('content')}")
    return result


def _server_from_status_result(result: dict) -> dict:
    """TODO(games-hub, blocked): map `result["structuredContent"]` onto
    `ServerSchema`'s shape (id/displayName/color/maxPlayers/current) once a
    REAL `pulse_server_status` response has been observed — see the module
    docstring for why nothing is guessed here yet. Deliberately raises
    rather than returning a partially-invented dict."""
    raise GamesUnavailableError(
        f"{_STATUS_TOOL} succeeded but its response shape has never been "
        "observed live in this deployment — mapping onto ServerSchema is "
        "deliberately not guessed (see nora/web/games.py's module "
        "docstring); fill in _server_from_status_result once a real "
        "response is available"
    )


async def build_games_servers_payload(*, mcp_tools: Mapping[str, McpToolGroup]) -> list[dict]:
    """`GET /app/v1/games/servers` — one entry per Pulse-monitored server.

    Calls the real `pulse_server_status` tool (no `server` argument: the
    tool's own description says it "defaults to the only configured
    server", so that IS the list this mount can answer for). Always raises
    `GamesUnavailableError` today — see module docstring."""
    result = await _call_status_tool(mcp_tools=mcp_tools, server=None)
    return [_server_from_status_result(result)]


async def build_game_server_status_payload(
    *, mcp_tools: Mapping[str, McpToolGroup], server_id: str,
) -> dict:
    """`GET /app/v1/games/servers/{id}/status` — one server's live detail."""
    result = await _call_status_tool(mcp_tools=mcp_tools, server=server_id)
    return _server_from_status_result(result)


async def build_game_server_graph_payload(
    *, mcp_tools: Mapping[str, McpToolGroup], server_id: str, hours: int,
) -> dict:
    """`GET /app/v1/games/servers/{id}/graph` — always unavailable, and not
    because of connectivity: Pulse's live MCP tool inventory (confirmed by
    listing it) has no tool that returns an hourly time series at all —
    `pulse_weekly` is one aggregate per week, `pulse_recent_events` is a
    discrete feed. There is nothing to call here yet; a real fix is a new
    Pulse-side MCP tool, not a client-side guess at data that doesn't
    exist."""
    raise GamesUnavailableError(
        "Pulse's MCP tool surface has no hourly time-series tool (confirmed "
        "live: pulse_server_status/player_profile/squad_activity/"
        "leaderboards/world_state/recent_events/weekly — none returns "
        "hourly buckets) — this is a structural gap, not a connectivity one"
    )
