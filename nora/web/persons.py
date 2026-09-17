"""GET /app/v1/persons, .../{personId} — the identity roster and each
person's public profile: their cross-platform identity plus (where the
data is cheaply available) which game servers know them.

Public here means "visible to any logged-in, allowlisted person" — the
portal has no anonymous access, so there is no second, weaker trust tier
to build; the Next.js proxy routes gate this the same way every other
`/app/v1/*` route is gated (checkSession()), not a new one.

`Persons.by_person` already IS identity.toml's roster; this module is a
thin projection over it, plus a cross-reference into Pulse to find which
servers know a person. A local-save game's `/world` payload already
carries every crew member's steamId (one call, no extra cost). A
container-backed game (Abiotic) only exposes steamId on a per-player
detail call, so it costs one `players()` list call plus one `player()`
detail call per player on that server — an N+1 loop, deliberately
accepted rather than built around: this is a personal-scale roster (a
handful of players per server, called on a profile page view, not a
poll tick), where the honest per-player lookup is simpler and no less
correct than adding a bulk steamId endpoint to Pulse for one caller.
`_CONTAINER_PLAYER_LOOKUP_CAP` bounds it if that scale assumption is
ever wrong.

Memory stays on its own existing endpoint (`nora/web/memory.py`'s
`/app/v1/memory?personId=`) rather than being duplicated here — a
person's private section is that route, gated by the frontend to render
only on the viewer's own profile."""

from __future__ import annotations

from typing import Any

from nora.persons import Persons
from nora.web.games import GamesUnavailableError, PulseGamesClient

# One page of Abiotic's player list is fetched per profile view (not per
# poll tick), then at most this many are detail-looked-up for a steamId
# match — a personal-scale bound, not a real pagination story.
_CONTAINER_PLAYER_LOOKUP_CAP = 50


def build_persons_roster_payload(*, persons: Persons) -> list[dict[str, Any]]:
    """`GET /app/v1/persons` — everyone identity.toml knows, opt-out (not
    opt-in): matches how the games/players pages already list every known
    person by default."""
    return sorted(
        (
            {"personId": ref.person_id, "displayName": ref.name}
            for ref in persons.by_person.values()
        ),
        key=lambda entry: entry["displayName"],
    )


async def build_person_profile_payload(
    *, persons: Persons, games_client: PulseGamesClient | None, person_id: str,
) -> dict[str, Any] | None:
    """`GET /app/v1/persons/{personId}` — `None` for an unrecognized person
    (the route answers 404, fail-closed rather than an empty-but-200 body).

    `gameProfiles` is fail-soft, not fail-closed: Pulse being unreachable
    or unconfigured must not blank the identity half of the page, so a
    `GamesUnavailableError` anywhere in the cross-reference just yields an
    empty list rather than propagating."""
    ref = persons.by_person.get(person_id)
    if ref is None:
        return None

    game_profiles = await _game_profiles(games_client, persons, person_id)
    return {
        "personId": ref.person_id,
        "displayName": ref.name,
        "gameProfiles": game_profiles,
    }


async def _game_profiles(
    games_client: PulseGamesClient | None, persons: Persons, person_id: str,
) -> list[dict[str, Any]]:
    steam_ids = persons.platform_ids("pulse", person_id)
    if not steam_ids or games_client is None:
        return []
    try:
        servers = await games_client.servers()
    except GamesUnavailableError:
        return []

    profiles: list[dict[str, Any]] = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        server_id = server.get("id")
        if not isinstance(server_id, str):
            continue
        display_name = server.get("displayName", server_id)
        is_local_save = server.get("current") is None
        matched, game = (
            await _local_save_match(games_client, server_id, steam_ids)
            if is_local_save
            else await _container_match(games_client, server_id, steam_ids)
        )
        if matched:
            profiles.append({"serverId": server_id, "displayName": display_name, "game": game})
    return profiles


async def _local_save_match(
    games_client: PulseGamesClient, server_id: str, steam_ids: frozenset[str],
) -> tuple[bool, str | None]:
    try:
        world = await games_client.world(server_id)
    except GamesUnavailableError:
        return False, None
    players = world.get("players")
    if not isinstance(players, list):
        return False, None
    matched = any(isinstance(p, dict) and p.get("steamId") in steam_ids for p in players)
    return matched, world.get("game")


async def _container_match(
    games_client: PulseGamesClient, server_id: str, steam_ids: frozenset[str],
) -> tuple[bool, str | None]:
    try:
        page = await games_client.players(
            server_id, page=1, page_size=_CONTAINER_PLAYER_LOOKUP_CAP, online=None,
        )
    except GamesUnavailableError:
        return False, None
    entries = page.get("data")
    if not isinstance(entries, list):
        return False, None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        player_key = entry.get("playerKey")
        if not isinstance(player_key, str):
            continue
        try:
            detail = await games_client.player(server_id, player_key)
        except GamesUnavailableError:
            continue
        if detail.get("steamId") in steam_ids:
            return True, None
    return False, None
