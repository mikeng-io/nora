"""The pinned audio-controller endpoint semantics (design/voice-channel/07
§3) — driven through the REAL `VoiceControllerClient` over real HTTP against
an in-suite fake controller that implements the phase-1 state machine
(single guild, one serialized connection, generation-scoped verbs,
replace-not-enqueue play).

This suite is what the Node-side controller must agree with: if the real
controller and this fake ever disagree, one of them is off-contract.
"""

from __future__ import annotations

import pytest
from aiohttp import web

from nora.infra.voice.http_controller import (
    GenerationConflict,
    VoiceControllerClient,
    VoiceControllerRejected,
)

GUILD = "guild-1"
CHANNEL = "cortez-1"


class _FakeController:
    """The phase-1 controller semantics, minimal but faithful:

    - one connection: `idle` ↔ `connected` under a generation minted on
      join and invalidated on exit;
    - join is idempotent in the same channel, 409 `connected_elsewhere`
      otherwise; any `guild_id` ≠ the configured one is a 400 `wrong_guild`;
    - every generation-scoped verb 409s on a stale generation;
    - play REPLACES the music slot (a fresh `playback_id` each time);
    - `/music/stop` stops music but STAYS connected, and is a 200 no-op
      when idle.
    """

    def __init__(self, guild_id: str) -> None:
        self._guild_id = guild_id
        self.voice = "idle"
        self.generation: str | None = None
        self.channel_id: str | None = None
        self.playback_id: str | None = None
        self.playing = False
        self.plays: list[str] = []
        self._seq = 0

    def _state(self) -> dict:
        return {
            "voice": self.voice, "generation": self.generation,
            "channel_id": self.channel_id, "playback_id": self.playback_id,
            "player": {
                "status": "playing" if self.playing else "idle",
                "position_ms": 0, "current": None,
            },
            "speech_queue": 0, "crossfade_seconds": 0,
        }

    def _ok(self) -> web.Response:
        return web.json_response(
            {"ok": True, "request_id": "r", "state": self._state()},
        )

    def _conflict(self, reason: str = "generation_conflict") -> web.Response:
        return web.json_response(
            {"error": reason, "request_id": "r", "state": self._state()}, status=409,
        )

    def _check_guild(self, body: dict) -> web.Response | None:
        if body.get("guild_id") not in (None, self._guild_id):
            return web.json_response(
                {"error": "wrong_guild", "request_id": "r", "state": self._state()},
                status=400,
            )
        return None

    def _check_generation(self, body: dict) -> web.Response | None:
        if self.generation is None or body.get("generation") != self.generation:
            return self._conflict()
        return None

    async def _guarded(self, request: web.Request) -> tuple[dict, web.Response | None]:
        body = await request.json()
        return body, self._check_guild(body) or self._check_generation(body)

    async def join(self, request: web.Request) -> web.Response:
        body = await request.json()
        if (bad := self._check_guild(body)) is not None:
            return bad
        if self.voice == "connected":
            if self.channel_id == body["channel_id"]:
                return self._ok()  # idempotent re-join
            return self._conflict("connected_elsewhere")
        self._seq += 1
        self.voice = "connected"
        self.generation = f"gen-{self._seq}"
        self.channel_id = body["channel_id"]
        return self._ok()

    async def exit(self, request: web.Request) -> web.Response:
        _body, rejection = await self._guarded(request)
        if rejection is not None:
            return rejection
        self.voice = "idle"
        self.generation = None
        self.channel_id = None
        self.playing = False
        self.playback_id = None
        return self._ok()

    async def play(self, request: web.Request) -> web.Response:
        body, rejection = await self._guarded(request)
        if rejection is not None:
            return rejection
        self._seq += 1
        self.playback_id = f"pb-{self._seq}"  # REPLACE: a fresh playback identity
        self.playing = True
        self.plays.append(body["stream_url"])
        return self._ok()

    async def stop(self, request: web.Request) -> web.Response:
        _body, rejection = await self._guarded(request)
        if rejection is not None:
            return rejection
        # No-op when idle; either way the CONNECTION stays.
        self.playing = False
        self.playback_id = None
        return self._ok()

    async def pause(self, request: web.Request) -> web.Response:
        _body, rejection = await self._guarded(request)
        return rejection if rejection is not None else self._ok()

    async def resume(self, request: web.Request) -> web.Response:
        _body, rejection = await self._guarded(request)
        return rejection if rejection is not None else self._ok()

    async def volume(self, request: web.Request) -> web.Response:
        body, rejection = await self._guarded(request)
        if rejection is not None:
            return rejection
        percent = body.get("percent")
        if not isinstance(percent, int) or not 0 <= percent <= 200:
            return web.json_response(
                {"error": "percent_out_of_range", "request_id": "r",
                 "state": self._state()},
                status=400,
            )
        return self._ok()

    async def stream(self, request: web.Request) -> web.Response:
        _body, rejection = await self._guarded(request)
        return rejection if rejection is not None else self._ok()

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/voice/join", self.join)
        app.router.add_post("/voice/exit", self.exit)
        app.router.add_post("/music/play", self.play)
        app.router.add_post("/music/stop", self.stop)
        app.router.add_post("/music/pause", self.pause)
        app.router.add_post("/music/resume", self.resume)
        app.router.add_post("/music/volume", self.volume)
        app.router.add_post("/stream", self.stream)
        return app


@pytest.fixture
async def controller(aiohttp_client):
    fake = _FakeController(GUILD)
    test_client = await aiohttp_client(fake.app())
    client = VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))
    return fake, client


async def _join(client: VoiceControllerClient) -> str:
    state = await client.join(GUILD, CHANNEL, request_id="req-join")
    return state["generation"]


async def test_a_second_play_replaces_the_first(controller):
    """REPLACE semantics (§3.2): the second play supersedes the first — a
    fresh playback_id makes the old one stale for any later callback."""
    fake, client = controller
    generation = await _join(client)

    first = await client.play(generation, "https://cdn.example/a", request_id="r1")
    second = await client.play(generation, "https://cdn.example/b", request_id="r2")

    assert first["playback_id"] != second["playback_id"]
    assert fake.plays == ["https://cdn.example/a", "https://cdn.example/b"]
    assert fake.playback_id == second["playback_id"]


async def test_join_is_idempotent_in_the_same_channel(controller):
    fake, client = controller
    first = await _join(client)

    second = await _join(client)

    assert first == second
    assert fake.voice == "connected"


async def test_join_elsewhere_is_a_conflict(controller):
    _fake, client = controller
    await _join(client)

    with pytest.raises(GenerationConflict):
        await client.join(GUILD, "cortez-elsewhere", request_id="r")


async def test_music_stop_keeps_the_connection(controller):
    """`/music/stop` ≠ leaving (§3.2): after a stop, a play with the SAME
    generation still succeeds."""
    fake, client = controller
    generation = await _join(client)
    await client.play(generation, "https://cdn.example/a", request_id="r1")

    state = await client.stop_music(generation, request_id="r2")

    assert state["voice"] == "connected"
    assert fake.voice == "connected"
    again = await client.play(generation, "https://cdn.example/b", request_id="r3")
    assert again["playback_id"] is not None


async def test_music_stop_when_idle_is_a_noop_200(controller):
    _fake, client = controller
    generation = await _join(client)

    state = await client.stop_music(generation, request_id="r")

    assert state["voice"] == "connected"
    assert state["player"]["status"] == "idle"


async def test_exit_invalidates_the_generation(controller):
    """Every generation-scoped verb 409s once the connection is gone — and
    a re-join mints a NEW generation, never revives the old one."""
    fake, client = controller
    generation = await _join(client)

    state = await client.exit(generation, request_id="r-exit")

    assert state["voice"] == "idle"
    assert fake.generation is None
    with pytest.raises(GenerationConflict):
        await client.play(generation, "https://cdn.example/a", request_id="r")
    rejoined = await _join(client)
    assert rejoined != generation


async def test_every_generation_scoped_verb_rejects_a_stale_generation(controller):
    _fake, client = controller
    generation = await _join(client)
    await client.exit(generation, request_id="r-exit")

    with pytest.raises(GenerationConflict):
        await client.exit(generation, request_id="r")
    with pytest.raises(GenerationConflict):
        await client.play(generation, "https://cdn.example/a", request_id="r")
    with pytest.raises(GenerationConflict):
        await client.stop_music(generation, request_id="r")
    with pytest.raises(GenerationConflict):
        await client.pause(generation, request_id="r")
    with pytest.raises(GenerationConflict):
        await client.resume(generation, request_id="r")
    with pytest.raises(GenerationConflict):
        await client.set_volume(generation, 100, request_id="r")
    with pytest.raises(GenerationConflict):
        await client.stream(generation, b"mp3", media_type="audio/mpeg", request_id="r")


async def test_a_wrong_guild_is_rejected(controller):
    """Single-guild scope (§12.5): any other guild_id is a 400, surfaced as
    `VoiceControllerRejected` carrying the controller's reason."""
    _fake, client = controller

    with pytest.raises(VoiceControllerRejected) as excinfo:
        await client.join("guild-2", CHANNEL, request_id="r")
    assert excinfo.value.reason == "wrong_guild"


async def test_a_volume_out_of_range_is_rejected(controller):
    _fake, client = controller
    generation = await _join(client)

    with pytest.raises(VoiceControllerRejected) as excinfo:
        await client.set_volume(generation, 201, request_id="r")
    assert excinfo.value.reason == "percent_out_of_range"
