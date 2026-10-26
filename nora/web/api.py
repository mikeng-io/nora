"""Registers GET /app/v1/status, GET /app/v1/memory, GET /app/v1/doctrine, the
GET /app/v1/games/servers* trio, GET /app/v1/presence*, the read-only
GET /app/v1/events(/{id}) pair, the documents routes and the Artifacts routes
(`nora.web.artifacts`) onto an existing aiohttp Application — same
process/container as Nora.

Deliberately a `register_*` function taking an existing `web.Application`,
not a `build_*_app` returning a new one: these routes join the health app
nora/obs/health.py::build_health_app already builds and nora/__main__.py
already serves on :9100 — a second app would mean a second port.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from aiohttp import web

from nora.agent.whereis import Circle, PresenceReader
from nora.app.gatherings import GatheringService
from nora.doctrine.loader import DoctrineStore
from nora.domain.computer_exports import ComputerExportStore
from nora.domain.documents import DOCUMENT_QUERY_MAX, DocumentStore, RoomKey, is_handle
from nora.domain.ports import BlobStore, MemoryStore, RecordStore
from nora.domain.reminders import ReminderStore
from nora.domain.rollup import NoRollupBuildError
from nora.infra.sql.mobility import SqlMobilityStore
from nora.persons import Persons
from nora.web.artifacts import register_artifact_routes
from nora.web.doctrine import build_doctrine_payload
from nora.web.document_history import register_document_history_routes
from nora.web.documents import (
    build_document_media,
    build_document_payload,
    build_document_payload_by_handle,
    build_documents_payload,
    change_document_state,
)
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
from nora.web.gatherings import build_gathering_payload, build_gatherings_payload
from nora.web.memory import CitedMessages, ProfilePortal, build_memory_payload
from nora.web.persons import build_person_profile_payload, build_persons_roster_payload
from nora.web.presence import (
    build_presence_history_payload,
    build_presence_payload,
    build_presence_places_payload,
)
from nora.web.skills import build_skills_payload
from nora.web.status import build_status_payload
from nora.web.tools import build_tools_payload

log = logging.getLogger("nora.web.api")

# A fixed, generic body for every GamesUnavailableError — the real exception
# (which legitimately carries diagnostic detail: tailnet hostnames, raw MCP
# payloads, Pulse's own error text — see games.py's docstring) is logged
# server-side instead. An authenticated portal user learns "games backend
# unavailable"; an operator reading nora.log gets the real `str(exc)`.
_GAMES_UNAVAILABLE_BODY = {"error": "games backend unavailable"}

# Default `?hours=24`; the portal forwards `hours` as given
# (app/src/server/upstream.ts), so this applies only when the query param is
# absent. The endpoint itself is unavailable today (nora/web/games.py's module
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
_DEFAULT_ANALYTICS_DAYS = 30
_MAX_ANALYTICS_DAYS = 90
_DAY_MS = 24 * 60 * 60 * 1000


def _needs_a_build(
    handler: Callable[[web.Request], Awaitable[web.Response]],
) -> Callable[[web.Request], Awaitable[web.Response]]:
    """The derived-presence endpoints read the CURRENT Rollup build. With none
    yet (a fresh database, before the analytics process's first rebuild
    switches one in) the answer is 503 «no build yet» — R-27's
    `unavailable` — never an empty 200, which would say the person has no
    places, no rhythm and no routes."""

    async def wrapped(request: web.Request) -> web.Response:
        try:
            return await handler(request)
        except NoRollupBuildError:
            return web.json_response(
                {"error": "unavailable", "reason": "no Rollup build yet"}, status=503,
            )

    return wrapped


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


def _analytics_subject_ids(circles: tuple[Circle, ...]) -> list[str]:
    """Return known circle subjects once, preserving roster order."""
    seen: set[str] = set()
    subject_ids: list[str] = []
    for circle in circles:
        for ref in circle.people:
            if ref.person_id not in seen:
                seen.add(ref.person_id)
                subject_ids.append(ref.person_id)
    return subject_ids


def _place_payload(place: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": place["kind"],
        "label": place["label"],
        "saved": place["saved"],
        "lat": place["lat"],
        "lng": place["lng"],
        "radiusM": place["radius_m"],
        "confidence": place["confidence"],
        "firstSeenTs": place["first_seen_ts"],
        "lastSeenTs": place["last_seen_ts"],
        "stayCount": place["stay_count"],
        "totalDwellMs": place["total_dwell_ms"],
    }


def _trip_payload(trip: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": trip["id"],
        "startedTs": trip["started_ts"],
        "endedTs": trip["ended_ts"],
        "distanceM": trip["distance_m"],
        "avgSpeedKmh": trip["avg_speed_kmh"],
        "fixCount": trip["fix_count"],
        "corridorId": trip["corridor_id"],
        "corridorSampleCount": trip["corridor_sample_count"],
    }


def _corridor_payload(corridor: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": corridor["id"],
        "sampleCount": corridor["sample_count"],
        "firstSeenTs": corridor["first_seen_ts"],
        "lastSeenTs": corridor["last_seen_ts"],
        # The address-sequence ends are the corridor's provisional NAME ("A →
        # B") on the portal's person profile until the stay-adjacency place
        # join lands — corridors are directional (a reversed sequence clusters
        # apart), so from/to is honest, not decorative.
        "sequence": corridor["sequence"],
    }


def register_web_routes(
    app: web.Application, *, store: RecordStore, honcho: MemoryStore,
    reminders: ReminderStore, persons: Persons, ai_peer: str, started_at: float,
    games_client: PulseGamesClient | None,
    presence: PresenceReader | None, presence_circles: tuple[Circle, ...],
    skills_dir: Path, events: GatheringService, doctrine_store: DoctrineStore,
    mobility: SqlMobilityStore | None = None,
    workspaces_of: Callable[[str], tuple[str, ...]] | None = None,
    documents: DocumentStore | None = None,
    rooms_of: Callable[[str], frozenset[RoomKey]] | None = None,
    room_event_shown: Callable[[Any, str, str, str], bool] | None = None,
    blobs: BlobStore | None = None,
    room_label: Callable[[str, str], str] | None = None,
    artifacts: ComputerExportStore | None = None,
    profile: ProfilePortal | None = None,
    profile_scopes_of: Callable[[str], tuple[tuple[str, str], ...]] | None = None,
    profile_cited: CitedMessages | None = None,
) -> None:
    app["web_store"] = store
    app["web_artifacts"] = artifacts
    app["web_workspaces_of"] = workspaces_of
    app["web_profile"] = profile
    app["web_profile_scopes_of"] = profile_scopes_of
    app["web_profile_cited"] = profile_cited
    app["web_documents"] = documents
    app["web_rooms_of"] = rooms_of
    app["web_room_event_shown"] = room_event_shown
    app["web_room_label"] = room_label
    app["web_blobs"] = blobs
    app["web_honcho"] = honcho
    app["web_reminders"] = reminders
    app["web_persons"] = persons
    app["web_ai_peer"] = ai_peer
    app["web_started_at"] = started_at
    app["web_games_client"] = games_client
    app["web_presence"] = presence
    app["web_presence_circles"] = presence_circles
    app["web_mobility"] = mobility
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
            workspaces_of=a["web_workspaces_of"],
            profile=a["web_profile"], profile_scopes_of=a["web_profile_scopes_of"],
            cited=a["web_profile_cited"], rooms_of=a["web_rooms_of"],
        )
        return web.json_response(body)

    async def persons_roster(request: web.Request) -> web.Response:
        body = build_persons_roster_payload(persons=request.app["web_persons"])
        return web.json_response(body)

    async def tools(request: web.Request) -> web.Response:
        # The tool catalog is a pure function over the registry — no per-request
        # state, no app-stored collaborators, no I/O until a tool would be
        # CALLED (which this endpoint never does). Same posture as `skills`:
        # a malformed tool must not sink the endpoint, handled inside
        # `build_tools_payload` (R-27).
        return web.json_response(build_tools_payload())

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

    async def presence_analytics_places(request: web.Request) -> web.Response:
        a = request.app
        # Bind through a local NAME, not `a["web_mobility"].method(...)` — the
        # store-caller architecture walk only credits attribute calls on a
        # Name/Attribute receiver, so a subscript receiver reads as "no
        # production caller" (fail-closed by design).
        mobility = a["web_mobility"]
        if a["web_presence"] is None or mobility is None:
            return web.json_response({"subjects": []})
        subjects = []
        for subject_id in _analytics_subject_ids(a["web_presence_circles"]):
            places = await mobility.places_with_aggregates(
                subject_id=subject_id,
            )
            subjects.append(
                {
                    "subjectId": subject_id,
                    "places": [_place_payload(place) for place in places],
                }
            )
        return web.json_response({"subjects": subjects})

    async def presence_analytics_rhythm(request: web.Request) -> web.Response:
        subject_id = request.query.get("subjectId")
        try:
            days = _int_query(request, "days", _DEFAULT_ANALYTICS_DAYS)
        except _BadQueryError:
            return web.json_response({"error": "invalid days"}, status=400)
        if days <= 0:
            return web.json_response({"error": "invalid days"}, status=400)
        days = min(days, _MAX_ANALYTICS_DAYS)
        empty: dict[str, object] = {
            "subjectId": subject_id, "bins": [], "windowDays": days,
        }
        a = request.app
        mobility = a["web_mobility"]  # local NAME — see presence_analytics_places
        if (
            a["web_presence"] is None
            or mobility is None
            or subject_id is None
            or subject_id not in _analytics_subject_ids(a["web_presence_circles"])
        ):
            return web.json_response(empty)
        bins = await mobility.rhythm_bins(
            subject_id=subject_id, window_ms=days * _DAY_MS,
        )
        return web.json_response(
            {
                "subjectId": subject_id,
                "bins": [
                    {
                        "hourOfWeek": bucket["hour_of_week"],
                        "stayCount": bucket["stay_count"],
                        "totalDwellMs": bucket["total_dwell_ms"],
                    }
                    for bucket in bins
                ],
                "windowDays": days,
            }
        )

    async def presence_analytics_routes(request: web.Request) -> web.Response:
        subject_id = request.query.get("subjectId")
        try:
            days = _int_query(request, "days", _DEFAULT_ANALYTICS_DAYS)
        except _BadQueryError:
            return web.json_response({"error": "invalid days"}, status=400)
        if days <= 0:
            return web.json_response({"error": "invalid days"}, status=400)
        days = min(days, _MAX_ANALYTICS_DAYS)
        empty: dict[str, object] = {
            "subjectId": subject_id, "trips": [], "corridors": [],
        }
        a = request.app
        mobility = a["web_mobility"]  # local NAME — see presence_analytics_places
        if (
            a["web_presence"] is None
            or mobility is None
            or subject_id is None
            or subject_id not in _analytics_subject_ids(a["web_presence_circles"])
        ):
            return web.json_response(empty)
        result = await mobility.trips_with_corridors(
            subject_id=subject_id, window_ms=days * _DAY_MS,
        )
        return web.json_response(
            {
                "subjectId": subject_id,
                "trips": [_trip_payload(trip) for trip in result["trips"]],
                "corridors": [
                    _corridor_payload(corridor) for corridor in result["corridors"]
                ],
            }
        )

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
        # No `conversation_id` query param: the scope is the viewer's own,
        # events they created or RSVP'd to plus every event in a room that
        # names them (`web_rooms_of`), so there is nothing left for a param to
        # filter or leak.
        person_id = request.query.get("personId")
        if not person_id or request.app["web_persons"].by_person.get(person_id) is None:
            return web.json_response(
                {"status": "refused", "note": "a valid personId is required"},
                status=400,
            )
        body = await build_gatherings_payload(
            events=request.app["web_events"], person_id=person_id,
            rooms_of=request.app["web_rooms_of"], shown=request.app["web_room_event_shown"],
        )
        return web.json_response(body)

    async def events_detail(request: web.Request) -> web.Response:
        # Same personId-required minimum and scope as `events_list` above:
        # `build_gathering_payload` returns `None` (→ 404, never a distinct
        # "forbidden") unless `person_id` created this event, has an RSVP on
        # it, or is in the room it is held in.
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
        body = await build_gathering_payload(
            events=request.app["web_events"], event_id=event_id, person_id=person_id,
            rooms_of=request.app["web_rooms_of"], shown=request.app["web_room_event_shown"],
        )
        if body is None:
            return web.json_response({"status": "not_found"}, status=404)
        return web.json_response(body)

    def _documents_unavailable() -> web.Response:
        return web.json_response(
            {"status": "unavailable", "note": "documents are not available"}, status=503,
        )

    async def documents_list(request: web.Request) -> web.Response:
        # Viewer-scoped like `events_list`: `personId` comes from the session through the
        # portal's proxy (a browser-supplied one is dropped there) and must be a person
        # this deployment knows.
        a = request.app
        if a["web_documents"] is None or a["web_rooms_of"] is None:
            return _documents_unavailable()
        person_id = request.query.get("personId")
        if not person_id or a["web_persons"].by_person.get(person_id) is None:
            return web.json_response(
                {"status": "refused", "note": "a valid personId is required"}, status=400,
            )
        try:
            page = _int_query(request, "page", _DEFAULT_PAGE)
            page_size = _int_query(request, "pageSize", _DEFAULT_PAGE_SIZE)
        except _BadQueryError as exc:
            return web.json_response({"error": f"invalid {exc}"}, status=400)
        # The default list is the active documents; `state=archived` is the Archived
        # filter. A deleted document is never listed, so `deleted` is a bad query.
        listed = request.query.get("state", "active")
        if listed not in ("active", "archived"):
            return web.json_response({"error": "invalid state"}, status=400)
        # `q` narrows the same list: the current revision's title or text, ignoring case.
        # A longer one is refused, never cut, so a query never silently means something else.
        query = request.query.get("q", "")
        # A control character (NUL above all: Postgres refuses it in text) is as invalid as
        # an over-long one, so it is a 400 here and never a 500 from the store.
        if len(query) > DOCUMENT_QUERY_MAX or any(
            ord(ch) < 32 or ord(ch) == 127 for ch in query
        ):
            return web.json_response({"error": "invalid q"}, status=400)
        body = await build_documents_payload(
            store=a["web_documents"], rooms_of=a["web_rooms_of"], persons=a["web_persons"],
            person_id=person_id, page=page, page_size=page_size,
            state="archived" if listed == "archived" else "active", query=query,
            room_label=a["web_room_label"],
        )
        return web.json_response(body)

    async def documents_detail(request: web.Request) -> web.Response:
        a = request.app
        if a["web_documents"] is None or a["web_rooms_of"] is None:
            return _documents_unavailable()
        person_id = request.query.get("personId")
        if not person_id or a["web_persons"].by_person.get(person_id) is None:
            return web.json_response(
                {"status": "refused", "note": "a valid personId is required"}, status=400,
            )
        ref = request.match_info["id"]
        if is_handle(ref):
            # A link's 12-hex handle, resolved among what this person may read.
            body = await build_document_payload_by_handle(
                room_label=a["web_room_label"],
                store=a["web_documents"], rooms_of=a["web_rooms_of"],
                persons=a["web_persons"], person_id=person_id, handle=ref,
            )
        else:
            try:
                document_id = UUID(ref)
            except ValueError:
                return web.json_response({"status": "refused", "note": "bad id"}, status=400)
            body = await build_document_payload(
                room_label=a["web_room_label"],
                store=a["web_documents"], rooms_of=a["web_rooms_of"],
                persons=a["web_persons"], person_id=person_id, document_id=document_id,
            )
        if body is None:
            # Never a distinct "forbidden": an id must not confirm a document exists.
            return web.json_response({"status": "not_found"}, status=404)
        return web.json_response(body)

    async def documents_media(request: web.Request) -> web.Response:
        # One picture of one document. The same viewer rule as the detail route, plus: the
        # picture must be one the document names, and its bytes must BE a raster picture
        # (read off the bytes). Every refusal is the same 404; `Content-Security-Policy:
        # sandbox` and `nosniff` keep a mislabelled file from ever running on this origin.
        a = request.app
        if a["web_documents"] is None or a["web_rooms_of"] is None:
            return _documents_unavailable()
        person_id = request.query.get("personId")
        if not person_id or a["web_persons"].by_person.get(person_id) is None:
            return web.json_response(
                {"status": "refused", "note": "a valid personId is required"}, status=400,
            )
        try:
            document_id = UUID(request.match_info["id"])
        except ValueError:
            return web.json_response({"status": "refused", "note": "bad id"}, status=400)
        found = await build_document_media(
            store=a["web_documents"], rooms_of=a["web_rooms_of"], blobs=a["web_blobs"],
            person_id=person_id, document_id=document_id, sha=request.match_info["sha"],
        )
        if found.status == "unavailable":
            return _documents_unavailable()
        if found.status == "not_found":
            return web.json_response({"status": "not_found"}, status=404)
        return web.Response(
            body=found.data,
            content_type=found.media_type,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cache-Control": "private, no-store",
                "Content-Disposition": "inline",
            },
        )

    async def documents_state(request: web.Request) -> web.Response:
        # Archive, delete or restore (the portal's one write). The viewer comes from the
        # session through the portal's proxy, exactly as on the reads, and the gate is the
        # read gate: a document the viewer may not open, or a deleted one, is the same 404
        # as no such document. Body: {"state": "active" | "archived" | "deleted"}.
        a = request.app
        if a["web_documents"] is None or a["web_rooms_of"] is None:
            return _documents_unavailable()
        person_id = request.query.get("personId")
        if not person_id or a["web_persons"].by_person.get(person_id) is None:
            return web.json_response(
                {"status": "refused", "note": "a valid personId is required"}, status=400,
            )
        try:
            document_id = UUID(request.match_info["id"])
        except ValueError:
            return web.json_response({"status": "refused", "note": "bad id"}, status=400)
        try:
            payload = await request.json()
        except ValueError:
            return web.json_response({"status": "refused", "note": "bad body"}, status=400)
        state = payload.get("state") if isinstance(payload, dict) else None
        try:
            change = await change_document_state(
                store=a["web_documents"], rooms_of=a["web_rooms_of"],
                persons=a["web_persons"], person_id=person_id,
                document_id=document_id, state=str(state),
                room_label=a["web_room_label"],
            )
        except Exception:  # a store that is down is "could not check", never "not found"
            log.exception("document state change failed")
            return _documents_unavailable()
        if change.status == "bad_state":
            return web.json_response({"status": "refused", "note": "bad state"}, status=400)
        if change.status == "not_found":
            return web.json_response({"status": "not_found"}, status=404)
        return web.json_response(change.body)

    app.router.add_get("/app/v1/status", status)
    app.router.add_get("/app/v1/skills", skills)
    app.router.add_get("/app/v1/tools", tools)
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
    app.router.add_get(
        "/app/v1/presence/analytics/places", _needs_a_build(presence_analytics_places),
    )
    app.router.add_get(
        "/app/v1/presence/analytics/rhythm", _needs_a_build(presence_analytics_rhythm),
    )
    app.router.add_get(
        "/app/v1/presence/analytics/routes", _needs_a_build(presence_analytics_routes),
    )
    app.router.add_get("/app/v1/presence/{person_id}/history", presence_history)
    app.router.add_get("/app/v1/events", events_list)
    app.router.add_get("/app/v1/events/{id}", events_detail)
    app.router.add_get("/app/v1/documents", documents_list)
    app.router.add_get("/app/v1/documents/{id}", documents_detail)
    app.router.add_get("/app/v1/documents/{id}/media/{sha}", documents_media)
    app.router.add_post("/app/v1/documents/{id}/state", documents_state)
    register_document_history_routes(app)
    register_artifact_routes(app)
