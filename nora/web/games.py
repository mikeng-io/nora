"""GET /app/v1/games/servers, .../{id}/status, .../{id}/graph, plus the
full Pulse REST surface (players, sessions, activity, insights, world,
weekly) — Pulse game-server data straight from Pulse's own REST API,
Bearer-authed with `PULSE_API_TOKEN`.

This is a plain web app reading a REST API, not an agent calling a tool —
Pulse's MCP mount (`[tools.mcp.pulse]`, `PULSE_MCP_TOKEN`) is a SEPARATE
credential for the LLM tool-calling path (`nora.agent.mcp`) and is not used
here. The REST API returns the full container-metrics shape
(`app/src/lib/api/games.ts`'s `ContainerMetricsSchema`) — cgroup memory
limit, network/block I/O, pids, restart count — that the MCP surface's
`pulse_server_status` tool does not expose.

Pulse's `/servers/{id}/graph` answers `image/png` (a rendered chart), NOT
JSON; `/servers/{id}/history` is its JSON time-series route (same point shape
`GraphPointSchema` expects). `PulseGamesClient.server_graph` calls `/history`,
keeping the `graph` name only for this module's own method/payload-builder,
matching the `/app/v1/.../graph` route name `nora/web/api.py` exposes.

Every response is passed through as Pulse returns it — Pulse is Nora's own
sibling service, its shapes are the documented, versioned wire contract
`games-schema.ts`'s zod schemas validate client-side, and this
module is a thin projection (same posture as `nora/web/status.py`,
`nora/web/memory.py`), not a second parser of a third-party format the way
`nora/infra/weather/hko.py` has to be.

Pulse is GAME-NEUTRAL (AGENTS.md's
"abstracted server model"): a server is either container-backed (Abiotic
Factor — Docker metrics, a game log, live sessions) or local-save
(Big Walk — the host PC uploads a save file over its own, Nora-unrelated
POST endpoint; Pulse derives progress/statistics from it). `/status` and
`/world` answer with a GAME-SPECIFIC shape depending on which kind a
server is (`games-schema.ts`'s zod schemas — a `.safeParse`/discriminated
handling per shape — are the client-side authority on telling them
apart); `/progress` and `/save-history` exist ONLY for local-save games,
the save-upload equivalent of a container's live status and CPU/memory/
network history. A local-save server with no upload yet answers `503`,
same fail-closed posture as everything else here."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from nora.persons import Persons

log = logging.getLogger("nora.web.games")

PULSE_API_BASE = "https://orion.tail0a1b2c3d.ts.net/pulse/api/v1"
_TIMEOUT_SECONDS = 8.0


def _seg(value: str) -> str:
    """A single URL path segment, percent-encoded — `server_id`/`player_key`
    both land in a path built by plain f-string interpolation, and
    `player_key`'s real shape (`id:eos:00021fe1d55b4d73ab57c2f70a1726a8`)
    is untrusted, route-supplied input, not a literal this module controls."""
    return quote(value, safe="")


class GamesUnavailableError(Exception):
    """Pulse's REST API is not configured, unreachable, unauthenticated, or
    returned a body that isn't the expected JSON shape. The web route
    (`nora.web.api`) turns this into a 503 — never a guessed 200."""


class PulseGamesClient:
    """Bearer-authed reader for Pulse's `/v1/servers*` routes. Constructed
    once per process and held by `Dependencies`, same lifetime as
    `HkoWeatherProvider`. NEVER returns a partial or invented body — every
    failure mode (transport error, non-200, non-JSON, wrong-shaped JSON)
    raises `GamesUnavailableError`."""

    def __init__(
        self, *, token: str, base_url: str = PULSE_API_BASE,
        timeout_seconds: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def servers(self) -> list[dict[str, Any]]:
        body = await self._get("/servers")
        if not isinstance(body, list):
            raise GamesUnavailableError(f"GET /servers returned a non-list body: {body!r}")
        return body

    async def server_status(self, server_id: str) -> dict[str, Any]:
        return await self._get_dict(f"/servers/{_seg(server_id)}/status")

    async def server_graph(self, server_id: str, hours: int) -> dict[str, Any]:
        return await self._get_dict(
            f"/servers/{_seg(server_id)}/history", params={"hours": str(hours)},
        )

    async def weekly(self, server_id: str) -> dict[str, Any]:
        return await self._get_dict(f"/servers/{_seg(server_id)}/weekly")

    async def world(self, server_id: str) -> dict[str, Any]:
        return await self._get_dict(f"/servers/{_seg(server_id)}/world")

    async def insights(self, server_id: str, *, days: int) -> dict[str, Any]:
        return await self._get_dict(
            f"/servers/{_seg(server_id)}/insights", params={"days": str(days)},
        )

    async def progress(self, server_id: str) -> dict[str, Any]:
        """Local-save games only (e.g. Big Walk) — a synthesized completion
        summary derived from the last uploaded save. A container-backed
        game has no such route."""
        return await self._get_dict(f"/servers/{_seg(server_id)}/progress")

    async def save_history(self, server_id: str, hours: int) -> dict[str, Any]:
        """Local-save games only — time series of playtime/completion
        counts per upload, the save-upload equivalent of `server_graph`'s
        CPU/memory/network history (which needs a container Big Walk
        doesn't have)."""
        return await self._get_dict(
            f"/servers/{_seg(server_id)}/save-history", params={"hours": str(hours)},
        )

    async def players(
        self, server_id: str, *, page: int, page_size: int, online: bool | None,
    ) -> dict[str, Any]:
        params = {"page": str(page), "pageSize": str(page_size)}
        if online is not None:
            params["online"] = "true" if online else "false"
        return await self._get_dict(f"/servers/{_seg(server_id)}/players", params=params)

    async def player(self, server_id: str, player_key: str) -> dict[str, Any]:
        return await self._get_dict(
            f"/servers/{_seg(server_id)}/players/{_seg(player_key)}"
        )

    async def activity(
        self, server_id: str, *, page: int, page_size: int,
        since_ms: int | None, until_ms: int | None,
    ) -> dict[str, Any]:
        params = {"page": str(page), "pageSize": str(page_size)}
        if since_ms is not None:
            params["from"] = str(since_ms)
        if until_ms is not None:
            params["to"] = str(until_ms)
        return await self._get_dict(f"/servers/{_seg(server_id)}/activity", params=params)

    async def sessions(
        self, server_id: str, *, page: int, page_size: int,
        player_key: str | None, since_ms: int | None, until_ms: int | None,
    ) -> dict[str, Any]:
        params = {"page": str(page), "pageSize": str(page_size)}
        if player_key is not None:
            params["playerKey"] = player_key
        if since_ms is not None:
            params["from"] = str(since_ms)
        if until_ms is not None:
            params["to"] = str(until_ms)
        return await self._get_dict(f"/servers/{_seg(server_id)}/sessions", params=params)

    async def _get_dict(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        body = await self._get(path, params=params)
        if not isinstance(body, dict):
            raise GamesUnavailableError(f"GET {path} returned a non-dict body: {body!r}")
        return body

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(url, headers=headers, params=params) as response,
            ):
                if response.status != 200:
                    raise GamesUnavailableError(f"GET {path} answered {response.status}")
                try:
                    return await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as exc:
                    raise GamesUnavailableError(
                        f"GET {path} did not return JSON: {exc}"
                    ) from exc
        except aiohttp.ClientError as exc:
            raise GamesUnavailableError(f"GET {path} request failed: {exc}") from exc


def _require_client(client: PulseGamesClient | None) -> PulseGamesClient:
    if client is None:
        raise GamesUnavailableError("PULSE_API_TOKEN is not configured")
    return client


async def build_games_servers_payload(*, client: PulseGamesClient | None) -> list[dict[str, Any]]:
    """`GET /app/v1/games/servers`."""
    return await _require_client(client).servers()


async def build_game_server_status_payload(
    *, client: PulseGamesClient | None, server_id: str,
) -> dict[str, Any]:
    """`GET /app/v1/games/servers/{id}/status`."""
    return await _require_client(client).server_status(server_id)


async def build_game_server_graph_payload(
    *, client: PulseGamesClient | None, server_id: str, hours: int,
) -> dict[str, Any]:
    """`GET /app/v1/games/servers/{id}/graph?hours=`."""
    return await _require_client(client).server_graph(server_id, hours)


async def build_game_server_weekly_payload(
    *, client: PulseGamesClient | None, server_id: str,
) -> dict[str, Any]:
    """`GET /app/v1/games/servers/{id}/weekly`."""
    return await _require_client(client).weekly(server_id)


async def build_game_server_world_payload(
    *, client: PulseGamesClient | None, server_id: str, persons: Persons,
) -> dict[str, Any]:
    """`GET /app/v1/games/servers/{id}/world`.

    A local-save game's `players` entries are enriched with `linkedPerson`
    the same way `build_game_server_player_payload` enriches Abiotic's —
    each entry there is only a bare `steamId` with no name from the save
    itself. The container-backed shape has no `players` key at all, so this
    is a no-op for it rather than a second code path."""
    payload = await _require_client(client).world(server_id)
    players = payload.get("players")
    if isinstance(players, list):
        enriched = []
        for player in players:
            steam_id = player.get("steamId") if isinstance(player, dict) else None
            ref = persons.resolve("pulse", steam_id) if isinstance(steam_id, str) else None
            if ref is not None:
                player = {
                    **player,
                    "linkedPerson": {"personId": ref.person_id, "displayName": ref.name},
                }
            enriched.append(player)
        payload = {**payload, "players": enriched}
    return payload


async def build_game_server_progress_payload(
    *, client: PulseGamesClient | None, server_id: str,
) -> dict[str, Any]:
    """`GET /app/v1/games/servers/{id}/progress` — local-save games only."""
    return await _require_client(client).progress(server_id)


async def build_game_server_save_history_payload(
    *, client: PulseGamesClient | None, server_id: str, hours: int,
) -> dict[str, Any]:
    """`GET /app/v1/games/servers/{id}/save-history?hours=` — local-save
    games only."""
    return await _require_client(client).save_history(server_id, hours)


async def build_game_server_insights_payload(
    *, client: PulseGamesClient | None, server_id: str, days: int,
) -> dict[str, Any]:
    """`GET /app/v1/games/servers/{id}/insights?days=`."""
    return await _require_client(client).insights(server_id, days=days)


async def build_game_server_players_payload(
    *, client: PulseGamesClient | None, server_id: str,
    page: int, page_size: int, online: bool | None,
) -> dict[str, Any]:
    """`GET /app/v1/games/servers/{id}/players?page=&pageSize=&online=`."""
    return await _require_client(client).players(
        server_id, page=page, page_size=page_size, online=online,
    )


async def build_game_server_player_payload(
    *, client: PulseGamesClient | None, server_id: str, player_key: str,
    persons: Persons,
) -> dict[str, Any]:
    """`GET /app/v1/games/servers/{id}/players/{playerKey}`.

    Enriched with `linkedPerson` when the response's `steamId` resolves to
    an identity.toml person (`persons.resolve("pulse", steam_id)`) — the
    one deliberate deviation from this module's pure-passthrough posture,
    matching `nora/web/memory.py`'s own precedent for combining an external
    payload with `Persons`. Absent when there is no `steamId` (Pulse's own
    login-line pairing never captured one for this player) or it resolves
    to nobody identity.toml names."""
    payload = await _require_client(client).player(server_id, player_key)
    steam_id = payload.get("steamId")
    if isinstance(steam_id, str):
        ref = persons.resolve("pulse", steam_id)
        if ref is not None:
            payload = {
                **payload,
                "linkedPerson": {"personId": ref.person_id, "displayName": ref.name},
            }
    return payload


async def build_game_server_activity_payload(
    *, client: PulseGamesClient | None, server_id: str,
    page: int, page_size: int, since_ms: int | None, until_ms: int | None,
) -> dict[str, Any]:
    """`GET /app/v1/games/servers/{id}/activity?page=&pageSize=&from=&to=`."""
    return await _require_client(client).activity(
        server_id, page=page, page_size=page_size, since_ms=since_ms, until_ms=until_ms,
    )


async def build_game_server_sessions_payload(
    *, client: PulseGamesClient | None, server_id: str,
    page: int, page_size: int, player_key: str | None,
    since_ms: int | None, until_ms: int | None,
) -> dict[str, Any]:
    """`GET /app/v1/games/servers/{id}/sessions?page=&pageSize=&playerKey=&from=&to=`."""
    return await _require_client(client).sessions(
        server_id, page=page, page_size=page_size, player_key=player_key,
        since_ms=since_ms, until_ms=until_ms,
    )
