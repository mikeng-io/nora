"""GET /app/v1/persons, .../{personId} — the identity roster and each
person's public profile: their cross-platform identity plus (where the
data is cheaply available) which game servers know them.

Public here means "visible to any logged-in, allowlisted person" — the
portal has no anonymous access, so there is no second, weaker trust tier
to build; the Next.js proxy routes gate this the same way every other
`/app/v1/*` route is gated (checkSession()), not a new one.

`Persons.by_person` already IS identity.toml's roster; this module is a
thin projection over it, plus one cross-reference into Pulse for the one
case that's cheap: a local-save game's `/world` payload already carries
every crew member's steamId (no extra Pulse call beyond one already
needed elsewhere). Container-backed games (Abiotic) only expose steamId
on a per-player detail call, so cross-referencing them would mean either
an N+1 loop over every player or a new Pulse-side endpoint — left as a
known limitation rather than either, until Pulse exposes its steamId map
the way it already does for a local-save game's world payload.

Memory stays on its own existing endpoint (`nora/web/memory.py`'s
`/app/v1/memory?personId=`) rather than being duplicated here — a
person's private section is that route, gated by the frontend to render
only on the viewer's own profile."""

from __future__ import annotations

from nora.persons import Persons
from nora.web.games import GamesUnavailableError, PulseGamesClient


def build_persons_roster_payload(*, persons: Persons) -> list[dict]:
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
) -> dict | None:
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
) -> list[dict]:
    steam_ids = persons.platform_ids("pulse", person_id)
    if not steam_ids or games_client is None:
        return []
    try:
        servers = await games_client.servers()
    except GamesUnavailableError:
        return []

    profiles: list[dict] = []
    for server in servers:
        if not isinstance(server, dict) or server.get("current") is not None:
            continue  # container-backed cross-ref not available yet — see module docstring
        server_id = server.get("id")
        if not isinstance(server_id, str):
            continue
        try:
            world = await games_client.world(server_id)
        except GamesUnavailableError:
            continue
        players = world.get("players")
        if not isinstance(players, list):
            continue
        matched = any(
            isinstance(p, dict) and p.get("steamId") in steam_ids for p in players
        )
        if matched:
            profiles.append({
                "serverId": server_id,
                "displayName": server.get("displayName", server_id),
                "game": world.get("game"),
            })
    return profiles
