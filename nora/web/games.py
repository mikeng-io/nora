"""GET /app/v1/games/servers, .../{id}/status, .../{id}/graph — Pulse
game-server data through Nora's ALREADY-WORKING MCP connection
(`[tools.mcp.pulse]`, `nora.agent.mcp.build_mcp_tools`), replacing the
previous direct-REST proxy the Next.js `app` service used
(`GAMES_API_BASE` -> Pulse's HTTP port), which was fragile because that port
is host-loopback-only by design and unreachable from a separate compose
project.

Pulse's live MCP tool inventory (discovered by connecting to the deployed
`[tools.mcp.pulse]` mount and calling `list_tools_sync()`) is seven
read-only tools: `pulse_server_status`, `pulse_player_profile`,
`pulse_squad_activity`, `pulse_leaderboards`, `pulse_world_state`,
`pulse_recent_events`, `pulse_weekly`. Every one of them speaks for ONE
server — the tool descriptions say "defaults to the only configured
server" — so there is no fleet to list, matching Pulse's own README ("one
game adapter: `abiotic_factor`" at the time of writing).

`pulse_server_status`'s real, observed response (captured live 2026-08-17,
after Pulse's own Caddy-sidecar routing fix — see the module's git history
for the discovery process) is a JSON string inside `content[0].text`, not
`structuredContent`:

    {"server":"Abiotic Factor","online":true,"status":"running",
     "uptimeHours":394.3,"cpuPercent":47,"memoryGb":5.4,
     "playersOnline":[],"playerCount":0,"maxPlayers":12,
     "lastSampleAt":"2026-08-17T18:53:52.293Z"}

This is a coarse, human/LLM-facing read — no Docker cgroup counters
(memory limit, network/block I/O, pids, restart count). That's a real,
structural difference from Pulse's REST API (`/pulse/api/v1/servers`,
documented in game-server-monitor's README), which does expose those.
Reaching the REST API directly would need a second Pulse-issued token
(`pulse-api-token`) Nora was never given — its README frames MCP as
Nora's specifically intended, least-privileged path — so this module
works within what the MCP surface actually offers rather than requesting
broader access. `app/src/lib/games-schema.ts`'s `ServerMetricsSchema` was
narrowed to match (see that file's own comment).

There is still no MCP tool for an hourly time series (`pulse_weekly` is
one aggregate per week; `pulse_recent_events` is a discrete feed) — the
graph endpoint stays unavailable by construction, a Pulse-side gap, not a
client-side one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nora.agent.mcp import McpToolGroup

_MOUNT = "pulse"
_STATUS_TOOL = "pulse_server_status"

# Pulse currently monitors exactly one game adapter. Its MCP tools take no
# server-selecting argument beyond an optional override ("defaults to the
# only configured server"), and return no stable slug — only a display
# name ("Abiotic Factor"). `abiotic_factor` is the adapter id Pulse's own
# README documents, used here as the one routable id; `_SERVER_COLOR` is a
# presentational choice made on Nora's side, since Pulse's MCP surface has
# no color field at all (unlike its REST API's `ServerSchema`).
_SERVER_ID = "abiotic_factor"
_SERVER_COLOR = "#f59e0b"


class GamesUnavailableError(Exception):
    """Pulse's MCP tools are absent, unreachable, returned an error, or
    their result could not be parsed as the observed response shape. The
    web route (`nora.web.api`) turns this into a 503 — never a guessed 200."""


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


def _parse_status_payload(result: dict) -> dict:
    """Extract and parse `pulse_server_status`'s real response: a JSON
    string in `content[0].text` (no `structuredContent` — observed live,
    not assumed). Raises `GamesUnavailableError` rather than returning a
    partial dict if the shape ever stops matching what was actually seen."""
    content = result.get("content") or []
    if not content or "text" not in content[0]:
        raise GamesUnavailableError(
            f"{_STATUS_TOOL} succeeded but its result had no content[0].text "
            f"— got: {result!r}"
        )
    try:
        payload = json.loads(content[0]["text"])
    except json.JSONDecodeError as exc:
        raise GamesUnavailableError(
            f"{_STATUS_TOOL} succeeded but content[0].text was not valid "
            f"JSON: {exc}"
        ) from exc
    required = {
        "server", "online", "status", "uptimeHours", "cpuPercent",
        "memoryGb", "playersOnline", "playerCount", "lastSampleAt",
    }
    missing = required - payload.keys()
    if missing:
        raise GamesUnavailableError(
            f"{_STATUS_TOOL}'s response is missing field(s) {sorted(missing)} "
            "the last-observed shape had — see nora/web/games.py's module "
            f"docstring; got: {payload!r}"
        )
    return payload


def _summary(payload: dict) -> dict:
    return {
        "id": _SERVER_ID,
        "displayName": payload["server"],
        "color": _SERVER_COLOR,
        "maxPlayers": payload.get("maxPlayers"),
    }


def _metrics(payload: dict) -> dict:
    return {
        "online": payload["online"],
        "status": payload["status"],
        "uptimeHours": payload["uptimeHours"],
        "cpuPercent": payload["cpuPercent"],
        "memoryGb": payload["memoryGb"],
        "playerCount": payload["playerCount"],
        "lastSampleAt": payload["lastSampleAt"],
    }


async def build_games_servers_payload(*, mcp_tools: Mapping[str, McpToolGroup]) -> list[dict]:
    """`GET /app/v1/games/servers` — one entry per Pulse-monitored server.

    Calls the real `pulse_server_status` tool with no `server` argument:
    the tool's own description says it defaults to the only configured
    server, so that IS the full list this mount can answer for."""
    result = await _call_status_tool(mcp_tools=mcp_tools, server=None)
    payload = _parse_status_payload(result)
    return [{**_summary(payload), "current": _metrics(payload)}]


async def build_game_server_status_payload(
    *, mcp_tools: Mapping[str, McpToolGroup], server_id: str,
) -> dict:
    """`GET /app/v1/games/servers/{id}/status` — one server's live detail.

    Rejects any `server_id` other than `_SERVER_ID` before it ever reaches
    Pulse: `_call_status_tool`'s `server` argument is forwarded verbatim as
    an MCP tool argument to an external service, and Pulse currently
    monitors exactly one adapter — there is no legitimate id this route
    should ever forward besides the one it already knows about."""
    if server_id != _SERVER_ID:
        raise GamesUnavailableError(f"no such server: {server_id!r}")
    result = await _call_status_tool(mcp_tools=mcp_tools, server=server_id)
    payload = _parse_status_payload(result)
    return {
        "server": _summary(payload),
        "current": _metrics(payload),
        "activePlayers": payload["playersOnline"],
    }


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
