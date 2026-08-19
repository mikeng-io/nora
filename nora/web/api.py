"""Registers GET /app/v1/status, GET /app/v1/memory, GET /app/v1/doctrine, the
GET /app/v1/games/servers* trio, GET /app/v1/presence*, and the read-only
GET /app/v1/events(/{id}) pair onto an existing aiohttp Application — same
process/container as Nora (design spec §3, §5.3).

Deliberately a `register_*` function taking an existing `web.Application`,
not a `build_*_app` returning a new one: these routes join the health app
nora/obs/health.py::build_health_app already builds and nora/__main__.py
already serves on :9100 — a second app would mean a second port, which the
spec's deployment section (§6) explicitly rules out.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from aiohttp import web

from nora.agent.whereis import Circle, PresenceReader
from nora.app.events import EventService
from nora.doctrine.loader import DoctrineStore
from nora.domain.ports import MemoryStore, RecordStore
from nora.domain.reminders import ReminderStore
from nora.persons import Persons
from nora.web.doctrine import build_doctrine_payload
from nora.web.events import build_event_payload, build_events_payload
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
from nora.web.memory import build_memory_payload
from nora.web.persons import build_person_profile_payload, build_persons_roster_payload
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

# Pulse's own defaults (game-server-monitor's web app never sends an
# explicit pageSize for its default views) — kept here so an absent query
# param behaves the same as Pulse's own first-party client.
_DEFAULT_PAGE = 1
_DEFAULT_PAGE_SIZE = 20
_DEFAULT_INSIGHTS_DAYS = 30


class _BadQueryError(Exception):
    """An int-typed query param didn't parse — the route turns this into a
    400, matching the existing `hours` param's own handling below."""


def _int_query(request: web.Request, name: str, default: int) -> int:
    raw = request.query.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise _BadQueryError(name) from None


def _optional_int_query(request: web.Request, name: str) -> int | None:
    raw = request.query.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise _BadQueryError(name) from None


def _optional_bool_query(request: web.Request, name: str) -> bool | None:
    raw = request.query.get(name)
    if raw is None:
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise _BadQueryError(name)


def register_web_routes(
    app: web.Application, *, store: RecordStore, honcho: MemoryStore,
    reminders: ReminderStore, persons: Persons, ai_peer: str, started_at: float,
    games_client: PulseGamesClient | None,
    presence: PresenceReader | None, presence_circles: tuple[Circle, ...],
    skills_dir: Path, events: EventService, doctrine_store: DoctrineStore,
) -> None:
    app["web_store"] = store
    app["web_honcho"] = honcho
    app["web_reminders"] = reminders
    app["web_persons"] = persons
    app["web_ai_peer"] = ai_peer
    app["web_started_at"] = started_at
    app["web_games_client"] = games_client
    app["web_presence"] = presence
    app["web_presence_circles"] = presence_circles
    app["web_skills_dir"] = skills_dir
    app["web_events"] = events
    app["web_doctrine_store"] = doctrine_store

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
            person_id=request.query.get("personId"),
        )
        return web.json_response(body)

    async def persons_roster(request: web.Request) -> web.Response:
        body = build_persons_roster_payload(persons=request.app["web_persons"])
        return web.json_response(body)

    async def person_profile(request: web.Request) -> web.Response:
        a = request.app
        body = await build_person_profile_payload(
            persons=a["web_persons"], games_client=a["web_games_client"],
            person_id=request.match_info["personId"],
        )
        if body is None:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response(body)

    async def games_servers(request: web.Request) -> web.Response:
        try:
            body = await build_games_servers_payload(client=request.app["web_games_client"])
        except GamesUnavailableError as exc:
            log.warning("games servers unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_status(request: web.Request) -> web.Response:
        try:
            body = await build_game_server_status_payload(
                client=request.app["web_games_client"],
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
                client=request.app["web_games_client"],
                server_id=request.match_info["id"], hours=hours,
            )
        except GamesUnavailableError as exc:
            log.warning("game server graph unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_weekly(request: web.Request) -> web.Response:
        try:
            body = await build_game_server_weekly_payload(
                client=request.app["web_games_client"], server_id=request.match_info["id"],
            )
        except GamesUnavailableError as exc:
            log.warning("game server weekly unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_world(request: web.Request) -> web.Response:
        try:
            body = await build_game_server_world_payload(
                client=request.app["web_games_client"], server_id=request.match_info["id"],
                persons=request.app["web_persons"],
            )
        except GamesUnavailableError as exc:
            log.warning("game server world unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_progress(request: web.Request) -> web.Response:
        try:
            body = await build_game_server_progress_payload(
                client=request.app["web_games_client"], server_id=request.match_info["id"],
            )
        except GamesUnavailableError as exc:
            log.warning("game server progress unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_save_history(request: web.Request) -> web.Response:
        try:
            hours = _int_query(request, "hours", _DEFAULT_GRAPH_HOURS)
        except _BadQueryError:
            return web.json_response({"error": "invalid hours"}, status=400)
        try:
            body = await build_game_server_save_history_payload(
                client=request.app["web_games_client"],
                server_id=request.match_info["id"], hours=hours,
            )
        except GamesUnavailableError as exc:
            log.warning("game server save-history unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_insights(request: web.Request) -> web.Response:
        try:
            days = _int_query(request, "days", _DEFAULT_INSIGHTS_DAYS)
        except _BadQueryError:
            return web.json_response({"error": "invalid days"}, status=400)
        try:
            body = await build_game_server_insights_payload(
                client=request.app["web_games_client"],
                server_id=request.match_info["id"], days=days,
            )
        except GamesUnavailableError as exc:
            log.warning("game server insights unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_players(request: web.Request) -> web.Response:
        try:
            page = _int_query(request, "page", _DEFAULT_PAGE)
            page_size = _int_query(request, "pageSize", _DEFAULT_PAGE_SIZE)
            online = _optional_bool_query(request, "online")
        except _BadQueryError as exc:
            return web.json_response({"error": f"invalid {exc}"}, status=400)
        try:
            body = await build_game_server_players_payload(
                client=request.app["web_games_client"], server_id=request.match_info["id"],
                page=page, page_size=page_size, online=online,
            )
        except GamesUnavailableError as exc:
            log.warning("game server players unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_player(request: web.Request) -> web.Response:
        try:
            body = await build_game_server_player_payload(
                client=request.app["web_games_client"], server_id=request.match_info["id"],
                player_key=request.match_info["playerKey"], persons=request.app["web_persons"],
            )
        except GamesUnavailableError as exc:
            log.warning("game server player unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_activity(request: web.Request) -> web.Response:
        try:
            page = _int_query(request, "page", _DEFAULT_PAGE)
            page_size = _int_query(request, "pageSize", _DEFAULT_PAGE_SIZE)
            since_ms = _optional_int_query(request, "from")
            until_ms = _optional_int_query(request, "to")
        except _BadQueryError as exc:
            return web.json_response({"error": f"invalid {exc}"}, status=400)
        try:
            body = await build_game_server_activity_payload(
                client=request.app["web_games_client"], server_id=request.match_info["id"],
                page=page, page_size=page_size, since_ms=since_ms, until_ms=until_ms,
            )
        except GamesUnavailableError as exc:
            log.warning("game server activity unavailable: %s", exc)
            return web.json_response(_GAMES_UNAVAILABLE_BODY, status=503)
        return web.json_response(body)

    async def games_server_sessions(request: web.Request) -> web.Response:
        try:
            page = _int_query(request, "page", _DEFAULT_PAGE)
            page_size = _int_query(request, "pageSize", _DEFAULT_PAGE_SIZE)
            since_ms = _optional_int_query(request, "from")
            until_ms = _optional_int_query(request, "to")
        except _BadQueryError as exc:
            return web.json_response({"error": f"invalid {exc}"}, status=400)
        try:
            body = await build_game_server_sessions_payload(
                client=request.app["web_games_client"], server_id=request.match_info["id"],
                page=page, page_size=page_size,
                player_key=request.query.get("playerKey"),
                since_ms=since_ms, until_ms=until_ms,
            )
        except GamesUnavailableError as exc:
            log.warning("game server sessions unavailable: %s", exc)
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

    async def doctrine(request: web.Request) -> web.Response:
        body = build_doctrine_payload(doctrine_store=request.app["web_doctrine_store"])
        return web.json_response(body)

    async def events_list(request: web.Request) -> web.Response:
        # `personId` is required — the SAME convention `/app/v1/memory`
        # already enforces (its own `person_id` param, resolved server-side by
        # the Next.js proxy from the session, never client-supplied) — and it
        # must resolve to a person this deployment actually knows, mirroring
        # `build_memory_payload`'s `persons.by_person.get(person_id)` check.
        # No `conversation_id` — this list is participant-scoped, not
        # conversation-scoped: `build_events_payload` already only returns
        # events `person_id` created or RSVP'd to, so there is nothing left
        # for a query param to filter or leak.
        person_id = request.query.get("personId")
        if not person_id or request.app["web_persons"].by_person.get(person_id) is None:
            return web.json_response(
                {"status": "refused", "note": "a valid personId is required"},
                status=400,
            )
        body = await build_events_payload(
            events=request.app["web_events"], person_id=person_id,
        )
        return web.json_response(body)

    async def events_detail(request: web.Request) -> web.Response:
        # Same personId-required minimum as `events_list` above, PLUS
        # participant scoping: `build_event_payload` returns `None` (→ 404,
        # never a distinct "forbidden") unless `person_id` created this
        # event or has an RSVP on it.
        person_id = request.query.get("personId")
        if not person_id or request.app["web_persons"].by_person.get(person_id) is None:
            return web.json_response(
                {"status": "refused", "note": "a valid personId is required"},
                status=400,
            )
        try:
            event_id = UUID(request.match_info["id"])
        except ValueError:
            return web.json_response({"status": "refused", "note": "bad id"}, status=400)
        body = await build_event_payload(
            events=request.app["web_events"], event_id=event_id, person_id=person_id,
        )
        if body is None:
            return web.json_response({"status": "not_found"}, status=404)
        return web.json_response(body)

    app.router.add_get("/app/v1/status", status)
    app.router.add_get("/app/v1/skills", skills)
    app.router.add_get("/app/v1/doctrine", doctrine)
    app.router.add_get("/app/v1/memory", memory)
    app.router.add_get("/app/v1/persons", persons_roster)
    app.router.add_get("/app/v1/persons/{personId}", person_profile)
    app.router.add_get("/app/v1/games/servers", games_servers)
    app.router.add_get("/app/v1/games/servers/{id}/status", games_server_status)
    app.router.add_get("/app/v1/games/servers/{id}/graph", games_server_graph)
    app.router.add_get("/app/v1/games/servers/{id}/weekly", games_server_weekly)
    app.router.add_get("/app/v1/games/servers/{id}/world", games_server_world)
    app.router.add_get("/app/v1/games/servers/{id}/progress", games_server_progress)
    app.router.add_get("/app/v1/games/servers/{id}/save-history", games_server_save_history)
    app.router.add_get("/app/v1/games/servers/{id}/insights", games_server_insights)
    app.router.add_get("/app/v1/games/servers/{id}/players", games_server_players)
    app.router.add_get(
        "/app/v1/games/servers/{id}/players/{playerKey}", games_server_player,
    )
    app.router.add_get("/app/v1/games/servers/{id}/activity", games_server_activity)
    app.router.add_get("/app/v1/games/servers/{id}/sessions", games_server_sessions)
    app.router.add_get("/app/v1/presence", presence_positions)
    app.router.add_get("/app/v1/presence/places", presence_places)
    app.router.add_get("/app/v1/presence/{person_id}/history", presence_history)
    app.router.add_get("/app/v1/events", events_list)
    app.router.add_get("/app/v1/events/{id}", events_detail)
