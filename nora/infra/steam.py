"""`SteamPresenceClient` — the `SteamPresenceProvider` port's real
implementation. One batched `GetPlayerSummaries`
call against the Steam Web API answers "who is online / playing what" for
the whole room: Steam accepts up to 100 comma-separated SteamID64s per
request, so `online_now`'s one question costs one network round trip, never
one per person (that is why the port's method takes the whole frozenset).

Keyed (the key comes from `STEAM_WEB_API_KEY` by default — `[online]
api_key_env` names the variable, never holds the value), so unlike
`HkoWeatherProvider` the composition root builds this only when the key is
actually set; a missing key is a wiring gap the tool reports as
`unavailable`, not a request Steam would 403.

Like every outbound helper here it NEVER raises: a non-200, a non-JSON
body, a body that is not Steam's envelope (`response.players` missing or
the wrong shape), or a transport error is `None` — the WHOLE READ
unavailable (R-5), never an exception into the agent loop and never a
confident "everyone is offline". Within a good envelope, a malformed
player entry is skipped rather than sinking the batch; an id Steam did
not return is simply absent from the dict, which is that account's
`nothing_found` — the two never share a shape.

The raw `personastate` integer is carried through unflattened: "away" and
"snooze" are answers the model must be able to give, and `game` is only
trusted when the profile is public (`communityvisibilitystate == 3`) — a
private profile with no `gameextrainfo` is "cannot see", never "not
playing".
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from nora.domain.ports import SteamPlayerState

log = logging.getLogger("nora.steam")

STEAM_SUMMARIES_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"

_MAX_IDS_PER_REQUEST = 100


class SteamPresenceClient:
    """Satisfies `nora.domain.ports.SteamPresenceProvider`. NEVER raises."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.steampowered.com",
        timeout_seconds: float = 8.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def summaries(
        self, *, steam_ids: frozenset[str],
    ) -> dict[str, SteamPlayerState] | None:
        """Live summaries for `steam_ids`, keyed by id. None when the read
        itself failed; an id Steam did not answer for is simply absent."""
        if not steam_ids:
            return {}
        try:
            return await self._summaries(steam_ids=steam_ids)
        except Exception:  # the never-raises backstop (R-5)
            log.exception("steam summaries failed")
            return None

    async def _summaries(
        self, *, steam_ids: frozenset[str],
    ) -> dict[str, SteamPlayerState] | None:
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        params = {
            "key": self._api_key,
            # Sorted so one room asks one reproducible question (a stable
            # URL is what makes a trace re-derivable); capped at Steam's
            # documented 100 ids per request.
            "steamids": ",".join(sorted(steam_ids)[:_MAX_IDS_PER_REQUEST]),
        }
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
            f"{self._base_url}/ISteamUser/GetPlayerSummaries/v2/",
            params=params,
        ) as response:
            if response.status != 200:
                log.warning("steam summaries answered %s", response.status)
                return None
            body: Any = await response.json(content_type=None)
        if not isinstance(body, dict):
            return None
        envelope = body.get("response")
        if not isinstance(envelope, dict):
            return None
        players = envelope.get("players")
        if not isinstance(players, list):
            return None

        out: dict[str, SteamPlayerState] = {}
        for entry in players:
            state = _parse_player(entry)
            if state is not None:
                out[state.steam_id] = state
        return out


def _parse_player(entry: Any) -> SteamPlayerState | None:
    """One `players[]` entry, or None when it is not Steam's shape — a
    malformed entry is skipped, never sinks the batch, and never invents a
    state for an id Steam answered garbage about."""
    if not isinstance(entry, dict):
        return None
    steam_id = entry.get("steamid")
    personastate = entry.get("personastate")
    if not isinstance(steam_id, str) or not steam_id:
        return None
    if not isinstance(personastate, int) or isinstance(personastate, bool):
        return None
    visibility = entry.get("communityvisibilitystate")
    game = entry.get("gameextrainfo")
    game_id = entry.get("gameid")
    return SteamPlayerState(
        steam_id=steam_id,
        personastate=personastate,
        profile_public=(visibility == 3),
        game=game if isinstance(game, str) and game else None,
        game_id=str(game_id) if isinstance(game_id, (str, int)) else None,
    )
