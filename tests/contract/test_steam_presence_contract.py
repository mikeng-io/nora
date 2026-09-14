"""The `SteamPresenceProvider` contract — one behavioural suite BOTH
implementations pass (`nora.infra.steam.SteamPresenceClient` against a fake
Steam Web API host, and `nora.testing.FakeSteamPresenceProvider`). The repo
rule: a port never gains a second implementation — test fakes included —
without a shared contract suite both pass.

The real side is driven through its REAL HTTP path against a `TestServer`
that answers in Steam's own envelope (`{"response": {"players": [...]}}`),
including Steam's own failure shapes — a 403 for a bad key, an HTML error
page, a body missing `response` — so the client cannot answer the wrong
shape and still pass. The port's promise, pinned here for both sides:

- a good read answers a dict KEYED BY STEAM ID, and an id the server did
  not answer for is simply ABSENT (that account's `nothing_found`);
- the WHOLE READ failing is `None`, never an exception and never `{}` —
  the tool's `unavailable` and an id's `nothing_found` never share a shape;
- `personastate` travels RAW (3 stays 3 — "away" is an answer, never
  flattened to offline), and a private profile is `profile_public=False`
  with no game, never "not playing".
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.ports import SteamPlayerState, SteamPresenceProvider
from nora.infra.steam import SteamPresenceClient
from nora.testing import FakeSteamPresenceProvider

# What the fake server "knows" — Steam's own wire shape, verbatim keys.
_PLAYERS = {
    "111": {
        "steamid": "111",
        "personastate": 1,
        "communityvisibilitystate": 3,
        "gameextrainfo": "Counter-Strike 2",
        "gameid": "730",
    },
    "222": {
        "steamid": "222",
        "personastate": 3,
        "communityvisibilitystate": 1,
    },
}

# The fake provider's equivalent of the same world.
_FAKE_PLAYERS = {
    "111": SteamPlayerState(
        steam_id="111", personastate=1, profile_public=True,
        game="Counter-Strike 2", game_id="730",
    ),
    "222": SteamPlayerState(steam_id="222", personastate=3, profile_public=False),
}


class _SteamAdapter:
    """`SteamPresenceClient` against a fake Steam Web API host, with a mode
    switch for each of Steam's own failure shapes."""

    async def start(self) -> None:
        self.requests: list[str] = []
        self.keys: list[str] = []
        self.mode = "ok"

        async def handler(request: web.Request) -> web.Response:
            self.requests.append(request.query.get("steamids", ""))
            self.keys.append(request.query.get("key", ""))
            if self.mode == "unauth":
                return web.Response(status=403, text="forbidden")
            if self.mode == "notjson":
                return web.Response(text="<html>bad gateway</html>", content_type="text/html")
            if self.mode == "odd":
                return web.json_response({"something": "else"})
            if self.mode == "malformed":
                return web.json_response({"response": {"players": [
                    {"steamid": "111"},  # no personastate — must be skipped
                    dict(_PLAYERS["222"]),
                ]}})
            wanted = request.query.get("steamids", "").split(",")
            players = [_PLAYERS[sid] for sid in wanted if sid in _PLAYERS]
            return web.json_response({"response": {"players": players}})

        app = web.Application()
        app.router.add_get("/ISteamUser/GetPlayerSummaries/v2/", handler)
        self._server = TestServer(app)
        await self._server.start_server()
        self.provider = SteamPresenceClient(
            api_key="k", base_url=str(self._server.make_url("")),
        )

    async def stop(self) -> None:
        await self._server.close()

    def break_it(self) -> None:
        self.mode = "unauth"


class _FakeAdapter:
    """`FakeSteamPresenceProvider`, in the same harness shape as the real
    client so the port-level tests below read identically across both."""

    async def start(self) -> None:
        self.provider = FakeSteamPresenceProvider(players=_FAKE_PLAYERS)

    async def stop(self) -> None:
        return None

    def break_it(self) -> None:
        self.provider.fail = True


_ADAPTERS = {"real": _SteamAdapter, "fake": _FakeAdapter}


@pytest.fixture(params=["real", "fake"])
async def harness(request):
    """Every implementation of the PORT, the shared fake included."""
    adapter = _ADAPTERS[request.param]()
    await adapter.start()
    yield adapter
    await adapter.stop()


@pytest.fixture
async def real():
    adapter = _SteamAdapter()
    await adapter.start()
    yield adapter
    await adapter.stop()


def test_every_implementation_satisfies_the_port() -> None:
    for impl in (
        SteamPresenceClient(api_key="k"),
        FakeSteamPresenceProvider(),
    ):
        assert isinstance(impl, SteamPresenceProvider), type(impl).__name__


async def test_known_ids_answer_keyed_states(harness) -> None:
    """The port's positive half, for every implementation: one batched call
    answers a dict keyed by steam id, with the state carried RAW."""
    out = await harness.provider.summaries(steam_ids=frozenset({"111", "222"}))
    assert out is not None
    assert set(out) == {"111", "222"}
    online = out["111"]
    assert online.personastate == 1
    assert online.profile_public is True
    assert online.game == "Counter-Strike 2"
    assert online.game_id == "730"
    away = out["222"]
    assert away.personastate == 3  # away stays away — never flattened
    assert away.profile_public is False
    assert away.game is None


async def test_an_id_the_server_does_not_answer_is_absent(harness) -> None:
    """An unanswered id is that account's `nothing_found` — an absent key,
    distinguishable from the whole read failing."""
    out = await harness.provider.summaries(steam_ids=frozenset({"111", "999"}))
    assert out is not None
    assert set(out) == {"111"}


async def test_an_answered_nothing_is_an_empty_dict_not_none(harness) -> None:
    """Steam answered fine and knew none of the ids: `{}`, never `None` —
    an empty result is not a failure."""
    out = await harness.provider.summaries(steam_ids=frozenset({"999"}))
    assert out == {}


async def test_an_empty_request_answers_an_empty_dict(harness) -> None:
    assert await harness.provider.summaries(steam_ids=frozenset()) == {}


async def test_a_failed_read_is_none_and_never_raises(harness) -> None:
    """Fail-closed by contract: the whole read failing (Steam's 403 for the
    real client, the fake's `fail` switch) is `None` — the tool's
    `unavailable` — never an exception into the agent loop and never a
    confident empty."""
    harness.break_it()
    assert await harness.provider.summaries(steam_ids=frozenset({"111"})) is None


async def test_one_call_is_one_batched_request(real) -> None:
    """The cost bound the tool promises: the room's whole id set goes out
    as ONE GetPlayerSummaries request, comma-joined, key attached — never
    one request per person."""
    await real.provider.summaries(steam_ids=frozenset({"222", "111"}))
    assert real.requests == ["111,222"]  # sorted: one reproducible question
    assert real.keys == ["k"]


async def test_a_non_json_body_is_none(real) -> None:
    real.mode = "notjson"
    assert await real.provider.summaries(steam_ids=frozenset({"111"})) is None


async def test_a_body_without_the_envelope_is_none(real) -> None:
    real.mode = "odd"
    assert await real.provider.summaries(steam_ids=frozenset({"111"})) is None


async def test_a_malformed_player_entry_is_skipped_not_fatal(real) -> None:
    """One garbage entry never sinks the batch — and never invents a state
    for the id it garbled."""
    real.mode = "malformed"
    out = await real.provider.summaries(steam_ids=frozenset({"111", "222"}))
    assert out is not None
    assert set(out) == {"222"}
