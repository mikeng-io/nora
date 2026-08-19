"""`nora.web.persons` — the identity roster and each person's public
profile. The game-profile cross-reference is proven against a real
aiohttp fake standing in for Pulse (CLAUDE.md: never a fake that answers
every request the same way), not a mock."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.web.games import PulseGamesClient
from nora.web.persons import build_person_profile_payload, build_persons_roster_payload

MILES = PersonRef(person_id="miles_novak", name="Miles Novak", honcho_peer_id="miles_novak")
AARON = PersonRef(person_id="aaron_larsen", name="Aaron Larsen", honcho_peer_id="aaron_larsen")


def _persons(*refs: PersonRef, index: dict[tuple[str, str], str] | None = None) -> Persons:
    return Persons(by_person={r.person_id: r for r in refs}, index=index or {})


def test_roster_lists_everyone_sorted_by_display_name():
    persons = _persons(
        PersonRef(person_id="zack", name="Zack", honcho_peer_id="zack"),
        PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada"),
    )

    roster = build_persons_roster_payload(persons=persons)

    assert roster == [
        {"personId": "ada", "displayName": "Ada"},
        {"personId": "zack", "displayName": "Zack"},
    ]


async def test_profile_is_none_for_an_unrecognized_person():
    persons = _persons(MILES)

    profile = await build_person_profile_payload(
        persons=persons, games_client=None, person_id="stranger",
    )

    assert profile is None


async def test_profile_has_no_game_profiles_without_a_games_client():
    persons = _persons(
        MILES, index={("pulse", "772857399"): "miles_novak"},
    )

    profile = await build_person_profile_payload(
        persons=persons, games_client=None, person_id="miles_novak",
    )

    assert profile == {"personId": "miles_novak", "displayName": "Miles Novak", "gameProfiles": []}


async def test_profile_has_no_game_profiles_when_the_person_has_no_pulse_steam_id():
    persons = _persons(MILES)  # no pulse index entry at all

    profile = await build_person_profile_payload(
        persons=persons, games_client=None, person_id="miles_novak",
    )

    assert profile is not None
    assert profile["gameProfiles"] == []


class _FakePulseForProfiles:
    """A container-backed server (`current` set) and a local-save server
    (`current: None`) — the profile builder must cross-reference the
    local-save one via its world payload's steamIds and leave the
    container one alone (module docstring: no N+1 loop over player detail
    for the container case)."""

    def __init__(self) -> None:
        self.servers_body: object = [
            {
                "id": "abiotic_factor", "displayName": "Abiotic Factor",
                "color": "#000", "current": {"online": True},
            },
            {"id": "big_walk", "displayName": "Big Walk", "color": "#000", "current": None},
        ]
        self.world_by_server: dict[str, object] = {
            "big_walk": {
                "game": "big-walk",
                "players": [{"steamId": "772857399", "walkingProven": True}],
            },
        }
        self.seen: list[str] = []

    async def servers(self, request: web.Request) -> web.Response:
        self.seen.append(request.path)
        return web.json_response(self.servers_body)

    async def world(self, request: web.Request) -> web.Response:
        self.seen.append(request.path)
        server_id = request.match_info["id"]
        body = self.world_by_server.get(server_id)
        if body is None:
            return web.Response(status=503)
        return web.json_response(body)


@pytest.fixture
async def pulse_for_profiles():
    fake = _FakePulseForProfiles()
    app = web.Application()
    app.router.add_get("/v1/servers", fake.servers)
    app.router.add_get("/v1/servers/{id}/world", fake.world)
    server = TestServer(app)
    await server.start_server()
    base_url = str(server.make_url("/v1")).rstrip("/")
    client = PulseGamesClient(
        token="test-token", base_url=base_url, timeout_seconds=5.0,  # noqa: S106
    )
    yield fake, client
    await server.close()


async def test_profile_finds_a_matching_local_save_server_by_steam_id(pulse_for_profiles):
    fake, client = pulse_for_profiles
    persons = _persons(MILES, index={("pulse", "772857399"): "miles_novak"})

    profile = await build_person_profile_payload(
        persons=persons, games_client=client, person_id="miles_novak",
    )

    assert profile is not None
    assert profile["gameProfiles"] == [
        {"serverId": "big_walk", "displayName": "Big Walk", "game": "big-walk"},
    ]
    # The container-backed server's world was never fetched — no N+1 cross-
    # reference attempted for it (module docstring's documented limitation).
    assert "/v1/servers/abiotic_factor/world" not in fake.seen


async def test_profile_has_no_game_profiles_when_the_steam_id_does_not_match(pulse_for_profiles):
    _fake, client = pulse_for_profiles
    persons = _persons(AARON, index={("pulse", "some-other-steam-id"): "aaron_larsen"})

    profile = await build_person_profile_payload(
        persons=persons, games_client=client, person_id="aaron_larsen",
    )

    assert profile is not None
    assert profile["gameProfiles"] == []


async def test_profile_is_fail_soft_when_pulse_is_unreachable():
    persons = _persons(MILES, index={("pulse", "772857399"): "miles_novak"})
    client = PulseGamesClient(
        token="test-token", base_url="http://127.0.0.1:1", timeout_seconds=1.0,  # noqa: S106
    )

    profile = await build_person_profile_payload(
        persons=persons, games_client=client, person_id="miles_novak",
    )

    assert profile == {"personId": "miles_novak", "displayName": "Miles Novak", "gameProfiles": []}
