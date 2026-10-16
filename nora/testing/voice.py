"""`FakeVoiceController` — the in-process stand-in for the audio-controller's
HTTP control plane (implemented for real by
`deploy/voice-sidecar/src/controller.mjs`).

It exists so the contract suite (`tests/contract/test_voice_controller_
contract.py`) and the wired-pipeline tests (`tests/unit/test_voice_pipeline
.py`) drive the REAL `VoiceControllerClient`/`VoiceControllerHub`/
`MusicPlayerManager` over REAL aiohttp HTTP against a controller whose
WRONG-CALL shapes match the Node implementation: a fake that accepts what the
real controller refuses lets the contract suite pass while the real calls fail.

Mirrored from `controller.mjs` (read it before changing this — one of them
is off-contract the moment they disagree):

- `guild_id` is REQUIRED on EVERY control verb, not just `/voice/join`: one
  process serves several guilds, so the front door reads the guild out of the
  body to know which controller a request is for. Absent
  or mismatched is 400 `wrong_guild`; a guild the process holds no connection
  for is 404 `unknown_guild`;
- ANY channel in the guild: `/voice/join` does not check `channel_id`
  against a configured one — a missing/empty
  `channel_id` is still 400 `invalid_body`, and joining a SECOND channel
  while connected is still 409 `connected_elsewhere`;
- a body that is not a JSON OBJECT is 400 `invalid_body` (the real
  controller rejects `null`/arrays/scalars before any field access);
- one serialized connection: `idle` ↔ `connected` under a generation minted
  on join and invalidated on exit; join is idempotent in the same channel
  and 409 `connected_elsewhere` otherwise;
- every generation-scoped verb 409s `generation_conflict` on a stale
  generation;
- play REPLACES the music slot (a fresh `playback_id` each time) and
  accepts the optional `resumable` boolean (position policy);
- `/music/stop` stops music but STAYS connected, and is a 200 no-op when
  idle; `/music/pause`/`/music/resume` during speech are 409
  `pause_during_speech`;
- `/music/volume` accepts 0-200, else 400 `percent_out_of_range`;
- `/voice/status` carries the full snapshot the hub reconciles from,
  including `last_event_seq`, `state_write_ok` and
  `player.position_policy`.

Deliberately NOT emulated (the consumers under test never rely on them):
the 5-minute `request_id` retry memo, the outbox's event DELIVERY (tests
POST events to nora's consumer directly), ffmpeg, and the Discord socket.
`speech_active` stays set after `/stream` until the test calls
`finish_speech()` — the real arbiter drains its speech queue on playback
completion; holding it is what lets a test pin "pause/resume DURING
speech" deterministically.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from aiohttp import web

_MAX_SPEECH_BYTES = 2 * 1024 * 1024


class FakeVoiceController:
    def __init__(
        self, guild_id: str, channel_id: str = "", *, speech_queue_max: int = 4,
    ) -> None:
        self._guild_id = guild_id
        # The FIRST-BOOT channel, mirroring the real controller's CHANNEL_ID:
        # a default for where a restart reconnects, never a gate on what
        # `/voice/join` will accept. Kept so a test can construct the fake
        # already-connected the way a rebooted controller is; defaulted to ""
        # because nothing checks it.
        self._channel_id = channel_id
        self._speech_queue_max = speech_queue_max
        self.voice = "idle"
        self.generation: str | None = None
        self.channel_id: str | None = None
        self.playback_id: str | None = None
        self.playing = False
        self.paused = False
        self.speech_active = False
        #: Requests QUEUED behind the currently-active one — i.e. this
        #: never counts the active stream itself (`_state()`'s
        #: `speech_queue` adds `+1` for that). A `/stream` call while
        #: `speech_active` increments this; it 503s `speech_queue_full`
        #: once it reaches `_speech_queue_max`. `finish_speech()` resets it
        #: to 0 as the "queue fully drained" simplification — per-item
        #: draining is deliberately NOT emulated (see module docstring).
        self.speech_queue = 0
        self.last_event_seq = 0
        self.state_write_ok = True
        #: The last accepted volume percent (0-200); `None` until set.
        self.volume_percent: int | float | None = None
        #: Every accepted play body, in arrival order.
        self.plays: list[dict[str, Any]] = []
        #: Every accepted stop, in arrival order.
        self.stops: list[dict[str, Any]] = []
        #: When set, `/music/play` waits on it before answering — the
        #: "controller is hung mid-play" fault.
        self.play_gate: asyncio.Event | None = None
        self._seq = 0

    # --- test-side controls -------------------------------------------------

    def finish_speech(self) -> None:
        """Drain the speech queue — the real arbiter does this when the
        AudioPlayer finishes the last queued speech resource."""
        self.speech_active = False
        self.speech_queue = 0

    def restart(self) -> None:
        """A controller restart whose desired-state says `connected`: the
        connection is re-joined under a NEW generation and the player state
        is gone (the process died) — the stale-generation scenario the
        hub/session reconciliation exists for."""
        if self.voice == "connected":
            self._seq += 1
            self.generation = f"gen-{self._seq}"
        self.playback_id = None
        self.playing = False
        self.paused = False
        self.speech_active = False
        self.speech_queue = 0

    # --- snapshot -------------------------------------------------------------

    def _player(self) -> dict[str, Any]:
        status = (
            "playing" if self.playing and not self.paused
            else "paused" if self.paused
            else "idle"
        )
        return {
            "status": status,
            "position_ms": 0,
            # Position policy: a preempted track restarts from 0; a live
            # source gets none.
            "position_policy": "approximate_seek" if self.playing else None,
            "current": None,
        }

    def _state(self) -> dict[str, Any]:
        return {
            "voice": self.voice,
            "generation": self.generation,
            "channel_id": self.channel_id,
            "playback_id": self.playback_id,
            "player": self._player(),
            "speech_queue": self.speech_queue + (1 if self.speech_active else 0),
            "crossfade_seconds": 0,
        }

    # --- response helpers -----------------------------------------------------

    def _ok(self, request: dict[str, Any]) -> web.Response:
        return web.json_response(
            {"ok": True, "request_id": request.get("request_id"), "state": self._state()},
        )

    def _error(self, status: int, reason: str, request: dict[str, Any]) -> web.Response:
        return web.json_response(
            {"error": reason, "request_id": request.get("request_id"),
             "state": self._state()},
            status=status,
        )

    def _conflict(
        self, reason: str, request: dict[str, Any],
    ) -> web.Response:
        return self._error(409, reason, request)

    @staticmethod
    async def _body(request: web.Request) -> dict[str, Any] | web.Response:
        """The real controller's first gate: a body that is not a JSON
        OBJECT (`null`, an array, a scalar, unparseable) is 400
        `invalid_body` before any field is read."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — any parse failure maps to invalid_json
            return web.json_response(
                {"error": "invalid_json", "request_id": None, "state": None}, status=400,
            )
        if not isinstance(body, dict):
            return web.json_response(
                {"error": "invalid_body", "request_id": None, "state": None}, status=400,
            )
        return body

    def _check_guild(self, body: dict[str, Any]) -> web.Response | None:
        """`guild_id` is REQUIRED on every control verb, not just on
        `/voice/join`. One controller process serves several guilds, so the
        front door has to read the guild out of the body to know which
        controller a request is even for — there is no "the" guild to fall
        back to. An absent guild must be refused like a mismatched one: this
        fake is what `tests/contract/` drives, so accepting it would let the
        contract suite pass while the REAL controller refuses the call."""
        if body.get("guild_id") is None:
            return self._error(400, "wrong_guild", body)
        if body["guild_id"] != self._guild_id:
            return self._error(400, "wrong_guild", body)
        return None

    def _check_generation(self, body: dict[str, Any]) -> web.Response | None:
        if self.generation is None or body.get("generation") != self.generation:
            return self._conflict("generation_conflict", body)
        return None

    async def _guarded(self, request: web.Request) -> tuple[dict[str, Any], web.Response | None]:
        parsed = await self._body(request)
        if isinstance(parsed, web.Response):
            return {}, parsed
        return parsed, self._check_guild(parsed) or self._check_generation(parsed)

    # --- routes -----------------------------------------------------------------

    async def join(self, request: web.Request) -> web.Response:
        parsed = await self._body(request)
        if isinstance(parsed, web.Response):
            return parsed
        body = parsed
        # `/voice/join` is the one route where guild_id is REQUIRED: the
        # real controller compares `body.guild_id !== guildId`, so absent
        # fails the same way as mismatched.
        if body.get("guild_id") != self._guild_id:
            return self._error(400, "wrong_guild", body)
        channel = body.get("channel_id")
        if not isinstance(channel, str) or not channel:
            return self._error(400, "invalid_body", body)
        if self.voice == "connected":
            if channel == self.channel_id:
                return self._ok(body)  # idempotent re-join, same channel
            # Discord allows ONE voice connection per bot per guild, so a join
            # to a second channel is a real conflict, not a move
            # (`lifecycle.mjs` answers the same way).
            return self._error(409, "connected_elsewhere", body)
        self._seq += 1
        self.voice = "connected"
        self.generation = f"gen-{self._seq}"
        self.channel_id = channel
        return self._ok(body)

    async def exit(self, request: web.Request) -> web.Response:
        body, rejection = await self._guarded(request)
        if rejection is not None:
            return rejection
        self.voice = "idle"
        self.generation = None
        self.channel_id = None
        self.playing = False
        self.paused = False
        self.playback_id = None
        self.speech_active = False
        self.speech_queue = 0
        return self._ok(body)

    async def play(self, request: web.Request) -> web.Response:
        body, rejection = await self._guarded(request)
        if rejection is not None:
            return rejection
        stream_url = body.get("stream_url")
        if not isinstance(stream_url, str) or not stream_url:
            return self._error(400, "invalid_body", body)
        if self.play_gate is not None:
            await self.play_gate.wait()
        self._seq += 1
        self.playback_id = f"pb-{self._seq}"  # REPLACE: a fresh playback identity
        self.playing = True
        self.paused = False
        self.plays.append(body)
        return self._ok(body)

    async def stop(self, request: web.Request) -> web.Response:
        body, rejection = await self._guarded(request)
        if rejection is not None:
            return rejection
        # No-op when idle; either way the CONNECTION stays.
        self.playing = False
        self.paused = False
        self.playback_id = None
        self.stops.append(body)
        return self._ok(body)

    async def pause(self, request: web.Request) -> web.Response:
        body, rejection = await self._guarded(request)
        if rejection is not None:
            return rejection
        if self.speech_active:
            return self._conflict("pause_during_speech", body)
        if self.playing:
            self.paused = True
            return self._ok(body)
        return self._conflict("no_music_playing", body)

    async def resume(self, request: web.Request) -> web.Response:
        body, rejection = await self._guarded(request)
        if rejection is not None:
            return rejection
        if self.speech_active:
            return self._conflict("pause_during_speech", body)
        if self.paused:
            self.paused = False
            return self._ok(body)
        if self.playing:
            return self._ok(body)
        return self._conflict("no_music_playing", body)

    async def volume(self, request: web.Request) -> web.Response:
        body, rejection = await self._guarded(request)
        if rejection is not None:
            return rejection
        percent = body.get("percent")
        if (
            isinstance(percent, bool)
            or not isinstance(percent, (int, float))
            or not 0 <= percent <= 200
        ):
            return self._error(400, "percent_out_of_range", body)
        self.volume_percent = percent
        return self._ok(body)

    async def stream(self, request: web.Request) -> web.Response:
        body, rejection = await self._guarded(request)
        if rejection is not None:
            return rejection
        audio_b64 = body.get("audio_b64")
        if not isinstance(audio_b64, str) or not audio_b64:
            return self._error(400, "invalid_body", body)
        audio = base64.b64decode(audio_b64)
        if not audio:
            return self._error(400, "invalid_body", body)
        if len(audio) > _MAX_SPEECH_BYTES:
            return self._error(400, "audio_too_large", body)
        if self.speech_active:
            # Another stream is already playing: this one QUEUES behind it
            # — bounded by `_speech_queue_max` — rather than becoming
            # active itself.
            if self.speech_queue >= self._speech_queue_max:
                return self._error(503, "speech_queue_full", body)
            self.speech_queue += 1
            return self._ok(body)
        # Speech hard-cuts music: the music slot is remembered
        # as suspended; `finish_speech()` is the drain.
        self.speech_active = True
        return self._ok(body)

    async def status(self, request: web.Request) -> web.Response:
        # The guild rides the QUERY STRING on this GET: one controller
        # process serves several guilds, so an
        # unqualified status could only answer for whichever it picked.
        # Mirrored here because a fake that accepts a shape the real
        # controller refuses teaches nora's client a lie.
        guild_id = request.query.get("guild_id")
        if not guild_id:
            return self._error(400, "guild_id is required", {})
        if guild_id != self._guild_id:
            return self._error(404, "unknown_guild", {})
        return web.json_response({
            **self._state(),
            "last_event_seq": self.last_event_seq,
            "outbox_dropped": 0,
            "streaming": self.playing,
            "last_error": None,
            "state_write_ok": self.state_write_ok,
            "wake": {},
            "pump": {},
        })

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
        app.router.add_get("/voice/status", self.status)
        return app
