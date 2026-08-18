"""Registers GET /app/v1/status, GET /app/v1/memory, the
GET /app/v1/games/servers* trio, and GET /app/v1/presence* onto an existing
aiohttp Application — same process/container as Nora (design spec §3, §5.3).

Deliberately a `register_*` function taking an existing `web.Application`,
not a `build_*_app` returning a new one: these routes join the health app
nora/obs/health.py::build_health_app already builds and nora/__main__.py
already serves on :9100 — a second app would mean a second port, which the
spec's deployment section (§6) explicitly rules out.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from aiohttp import web

from nora.agent.mcp import McpToolGroup
from nora.agent.whereis import Circle, PresenceReader
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
from nora.web.presence import (
    build_presence_history_payload,
    build_presence_payload,
    build_presence_places_payload,
)
from nora.web.skills import build_skills_payload
from nora.web.status import build_status_payload

log = logging.getLogger("nora.web.api")

# A fixed, generic body for every GamesUnavailableError — the real exception
# (which legitimately carries diagnostic detail: tailnet hostnames, raw MCP
# payloads, Pulse's own error text — see games.py's docstring) is logged
# server-side instead. An authenticated portal user learns "games backend
# unavailable"; an operator reading nora.log gets the real `str(exc)`.
_GAMES_UNAVAILABLE_BODY = {"error": "games backend unavailable"}

# Default matches the previous REST proxy's `?hours=24` default
# (app/src/app/api/games/servers/[id]/graph/route.ts) — kept here so an
# absent query param behaves identically to before, even though the
# endpoint itself is unavailable today (nora/web/games.py's module
# docstring).
_DEFAULT_GRAPH_HOURS = 24

# The map's default history window when `?hours=` is absent — a full day's
# route, matching the games graph's own default in spirit (both answer "the
# last day" unless told otherwise).
_DEFAULT_HISTORY_HOURS = 24


def register_web_routes(
    app: web.Application, *, store: RecordStore, honcho: MemoryStore,
    reminders: ReminderStore, persons: Persons, ai_peer: str, started_at: float,
    mcp_tools: Mapping[str, McpToolGroup],
    presence: PresenceReader | None, presence_circles: tuple[Circle, ...],
    skills_dir: Path,
) -> None:
    app["web_store"] = store
    app["web_honcho"] = honcho
    app["web_reminders"] = reminders
    app["web_persons"] = persons
    app["web_ai_peer"] = ai_peer
    app["web_started_at"] = started_at
    app["web_mcp_tools"] = mcp_tools
    app["web_presence"] = presence
    app["web_presence_circles"] = presence_circles
    app["web_skills_dir"] = skills_dir

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
            log.warning("games servers unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_status(request: web.Request) -> web.Response:
        try:
            body = await build_game_server_status_payload(
                mcp_tools=request.app["web_mcp_tools"],
                server_id=request.match_info["id"],
            )
        except GamesUnavailableError as exc:
            log.warning("game server status unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_graph(request: web.Request) -> web.Response:
        raw_hours = request.query.get("hours")
        try:
            hours = int(raw_hours) if raw_hours is not None else _DEFAULT_GRAPH_HOURS
        except ValueError:
            return web.json_response({"error": "invalid hours"}, status=400)
        try:
            body = await build_game_server_graph_payload(
                mcp_tools=request.app["web_mcp_tools"],
                server_id=request.match_info["id"], hours=hours,
            )
        except GamesUnavailableError as exc:
            log.warning("game server graph unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def presence_positions(request: web.Request) -> web.Response:
        a = request.app
        body = await build_presence_payload(
            presence=a["web_presence"], circles=a["web_presence_circles"],
        )
        return web.json_response(body)

    async def presence_places(request: web.Request) -> web.Response:
        a = request.app
        body = await build_presence_places_payload(
            presence=a["web_presence"], circles=a["web_presence_circles"],
        )
        return web.json_response(body)

    async def presence_history(request: web.Request) -> web.Response:
        raw_hours = request.query.get("hours")
        try:
            hours = int(raw_hours) if raw_hours is not None else _DEFAULT_HISTORY_HOURS
        except ValueError:
            return web.json_response({"error": "invalid hours"}, status=400)
        if hours <= 0:
            return web.json_response({"error": "invalid hours"}, status=400)
        a = request.app
        body = await build_presence_history_payload(
            presence=a["web_presence"], circles=a["web_presence_circles"],
            person_id=request.match_info["person_id"], hours=hours,
        )
        if body is None:
            return web.json_response({"error": "no such circle member"}, status=404)
        return web.json_response(body)

    async def skills(request: web.Request) -> web.Response:
        body = await build_skills_payload(skills_dir=request.app["web_skills_dir"])
        return web.json_response(body)

    app.router.add_get("/app/v1/status", status)
    app.router.add_get("/app/v1/skills", skills)
    app.router.add_get("/app/v1/memory", memory)
    app.router.add_get("/app/v1/games/servers", games_servers)
    app.router.add_get("/app/v1/games/servers/{id}/status", games_server_status)
    app.router.add_get("/app/v1/games/servers/{id}/graph", games_server_graph)
    app.router.add_get("/app/v1/presence", presence_positions)
    app.router.add_get("/app/v1/presence/places", presence_places)
    app.router.add_get("/app/v1/presence/{person_id}/history", presence_history)
