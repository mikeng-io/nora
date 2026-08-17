"""Registers GET /app/v1/status, GET /app/v1/memory and the
GET /app/v1/games/servers* trio onto an existing aiohttp Application — same
process/container as Nora (design spec §3, §5.3).

Deliberately a `register_*` function taking an existing `web.Application`,
not a `build_*_app` returning a new one: these routes join the health app
nora/obs/health.py::build_health_app already builds and nora/__main__.py
already serves on :9100 — a second app would mean a second port, which the
spec's deployment section (§6) explicitly rules out.
"""

from __future__ import annotations

from collections.abc import Mapping

from aiohttp import web

from nora.agent.mcp import McpToolGroup
from nora.domain.ports import MemoryStore, RecordStore
from nora.domain.reminders import ReminderStore
from nora.persons import Persons
from nora.web.games import (
    GamesUnavailableError,
    build_game_server_graph_payload,
    build_game_server_status_payload,
    build_games_servers_payload,
)
from nora.web.memory import build_memory_payload
from nora.web.status import build_status_payload

# Default matches the previous REST proxy's `?hours=24` default
# (app/src/app/api/games/servers/[id]/graph/route.ts) — kept here so an
# absent query param behaves identically to before, even though the
# endpoint itself is unavailable today (nora/web/games.py's module
# docstring).
_DEFAULT_GRAPH_HOURS = 24


def register_web_routes(
    app: web.Application, *, store: RecordStore, honcho: MemoryStore,
    reminders: ReminderStore, persons: Persons, ai_peer: str, started_at: float,
    mcp_tools: Mapping[str, McpToolGroup],
) -> None:
    app["web_store"] = store
    app["web_honcho"] = honcho
    app["web_reminders"] = reminders
    app["web_persons"] = persons
    app["web_ai_peer"] = ai_peer
    app["web_started_at"] = started_at
    app["web_mcp_tools"] = mcp_tools

    async def status(request: web.Request) -> web.Response:
        a = request.app
        body = await build_status_payload(
            store=a["web_store"], honcho=a["web_honcho"], reminders=a["web_reminders"],
            started_at=a["web_started_at"],
        )
        return web.json_response(body)

    async def memory(request: web.Request) -> web.Response:
        a = request.app
        body = await build_memory_payload(
            honcho=a["web_honcho"], persons=a["web_persons"], ai_peer=a["web_ai_peer"],
        )
        return web.json_response(body)

    async def games_servers(request: web.Request) -> web.Response:
        try:
            body = await build_games_servers_payload(mcp_tools=request.app["web_mcp_tools"])
        except GamesUnavailableError as exc:
            return web.json_response({"error": str(exc)}, status=503)
        return web.json_response(body)

    async def games_server_status(request: web.Request) -> web.Response:
        try:
            body = await build_game_server_status_payload(
                mcp_tools=request.app["web_mcp_tools"],
                server_id=request.match_info["id"],
            )
        except GamesUnavailableError as exc:
            return web.json_response({"error": str(exc)}, status=503)
        return web.json_response(body)

    async def games_server_graph(request: web.Request) -> web.Response:
        raw_hours = request.query.get("hours")
        try:
            hours = int(raw_hours) if raw_hours is not None else _DEFAULT_GRAPH_HOURS
        except ValueError:
            return web.json_response({"error": f"invalid hours={raw_hours!r}"}, status=400)
        try:
            body = await build_game_server_graph_payload(
                mcp_tools=request.app["web_mcp_tools"],
                server_id=request.match_info["id"], hours=hours,
            )
        except GamesUnavailableError as exc:
            return web.json_response({"error": str(exc)}, status=503)
        return web.json_response(body)

    app.router.add_get("/app/v1/status", status)
    app.router.add_get("/app/v1/memory", memory)
    app.router.add_get("/app/v1/games/servers", games_servers)
    app.router.add_get("/app/v1/games/servers/{id}/status", games_server_status)
    app.router.add_get("/app/v1/games/servers/{id}/graph", games_server_graph)
