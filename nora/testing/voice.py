"""`FakeVoiceController` — the in-process stand-in for the audio-controller's
`voice` contract (implemented for real by `deploy/voice-sidecar/src/`).

It exists so the contract suite (`tests/contract/test_voice_controller_
contract.py`) and the wired-pipeline tests (`tests/unit/test_voice_pipeline
.py`) drive the REAL `VoiceControllerClient`/`VoiceControllerHub`/
`MusicPlayerManager` over REAL aiohttp HTTP against a controller whose
WRONG-CALL shapes and outcomes match the Node implementation: a fake that
accepts what the real controller refuses lets the contract suite pass while
the real calls fail.

The wire (`docs/service-api/openapi.json`, «Outcomes on the controller»):

- `POST /v1/voice/operations` `{operation_id, kind, effect}`, checked in the
  controller's order: a body that is not a JSON object is 400; an
  `operation_id` outside 1-64 of `[A-Za-z0-9._:-]` is 400; a kind outside the
  closed set is 422; an effect that is not an object is 400; a body over the
  64 KiB control bound on any kind but `speak` is 413; a `guild_id` that
  is not a snowflake is 400 `wrong_guild`; `report` is `unsupported`;
- one ledger for the whole fake, five minutes from settlement: the same id and
  effect answers the kept envelope without running again (a duplicate in
  flight awaits the first answer), another effect is 409;
  `GET /v1/voice/operations/{operation_id}` reads it back, `pending` in flight;
- admission: a guild in `forbidden_guilds` is `denied`; an admitted guild the
  fake holds no core for (never joined, or any guild but its own) is
  `rejected` `unknown_guild` on every kind but `join`; both carry null `state`.
  The fake holds one core, so a `join` for another admitted guild is
  `unavailable` `join_failed`, an answer the real controller also gives;
- `join` admits ANY channel in the guild, is idempotent in the same channel
  and `rejected` `connected_elsewhere` otherwise, its `channel_id` at most 64
  characters; `play.resumable` is a
  boolean or absent (null reads as absent);
- every generation-scoped kind is `rejected` `generation_conflict` on a stale
  generation, with the current one in the payload's `state`;
- `play` REPLACES the music slot (a fresh `playback_id` each time); `stop`
  STAYS connected and is `complete` when idle; `pause`/`resume` during speech
  are `rejected` `pause_during_speech`; `set_volume` outside 0-200 is 400;
- `speak` over the bound is 400; a full speech queue is `unavailable`;
  `fault` set to `internal_error` answers `uncertain`, to any other reason
  `unavailable`; every settled answer is kept, these too;
- `POST /v1/voice/queries` `status`: a missing, empty or non-string
  `guild_id` is 400; `ok` with the full snapshot the hub reconciles from;
  `nothing_found` for any other string: no core, forbidden, not a snowflake.

Deliberately NOT emulated: the outbox's event DELIVERY (tests POST events to
nora's consumer directly), ffmpeg, and the Discord socket. `speech_active`
stays set after `speak` until the test calls `finish_speech()`, which is what
lets a test pin "pause/resume DURING speech" deterministically. The ledger
is unbounded, so the real one's "no room" `unavailable` is never answered.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
import time
from typing import Any

from aiohttp import web

from nora.domain.service_api import OperationOutcome, ReadOutcome, status_for

_MAX_SPEECH_BYTES = 2 * 1024 * 1024
_KINDS = frozenset({"join", "leave", "play", "stop", "pause", "resume",
                    "set_volume", "speak", "report"})
_LEDGER_S = 300.0
_MAX_CONTROL_BYTES = 64 * 1024
_OPERATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_SNOWFLAKE = re.compile(r"[0-9]{15,25}")


def _snowflake(value: object) -> bool:
    return isinstance(value, str) and _SNOWFLAKE.fullmatch(value) is not None


def _refusal(kind: str) -> dict[str, Any]:
    """A refusal that resolved no connection: `state` is null."""
    return {"state": None, "error": {"kind": kind, "message": kind}}


def _respond(answered: dict[str, Any] | str) -> web.Response:
    """A kept envelope under its outcome's status, or a 400 with no envelope."""
    if isinstance(answered, str):
        return web.json_response({"error": answered}, status=400)
    return web.json_response(answered, status=status_for(answered["outcome"]))


class _BadRequestError(Exception):
    """A 400 with no envelope; carries the controller's reason."""


class FakeVoiceController:
    def __init__(
        self, guild_id: str, channel_id: str = "", *, speech_queue_max: int = 4,
        forbidden_guilds: frozenset[str] = frozenset(),
    ) -> None:
        self._guild_id = guild_id
        # The FIRST-BOOT channel, mirroring the real controller's CHANNEL_ID:
        # a default for where a restart reconnects, never a gate on what
        # `join` will accept. Kept so a test can construct the fake
        # already-connected the way a rebooted controller is; defaulted to ""
        # because nothing checks it.
        self._channel_id = channel_id
        self._speech_queue_max = speech_queue_max
        self._forbidden = forbidden_guilds
        #: Whether the fake holds a connection core for its guild: made by the
        #: first `join`, kept across a `leave`.
        self.has_core = False
        #: A reason the next operations fail with (`internal_error` is `uncertain`).
        self.fault: str | None = None
        #: operation_id -> (expires_at, effect hash, answer, started_at_ms); the
        #: answer is an envelope, or a 400's reason, once it settles.
        self._ledger: dict[str, tuple[
            float, str, asyncio.Future[dict[str, Any] | str], int]] = {}
        #: How many operations reached a handler, the ledger's replays excluded.
        self.applied = 0
        self.voice = "idle"
        self.generation: str | None = None
        self.channel_id: str | None = None
        self.playback_id: str | None = None
        self.playing = False
        self.paused = False
        self.speech_active = False
        #: Requests QUEUED behind the currently-active one — i.e. this
        #: never counts the active stream itself (`_state()`'s
        #: `speech_queue` adds `+1` for that). A `speak` while
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
        #: When set, `play` waits on it before answering — the
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

    # --- the wire ---------------------------------------------------------------

    def _read(self, outcome: ReadOutcome, payload: object, trace: str) -> web.Response:
        return web.json_response(read_envelope(outcome, payload, trace=trace),
                                 status=status_for(outcome))

    def _answer(self, outcome: OperationOutcome,
                reason: str | None = None) -> tuple[OperationOutcome, dict[str, Any]]:
        payload: dict[str, Any] = {"state": self._state()}
        if reason is not None:
            payload["error"] = {"kind": reason, "message": reason}
        return outcome, payload

    async def operations(self, request: web.Request) -> web.Response:
        raw = await request.read()
        try:
            body = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "invalid_json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "invalid_body"}, status=400)
        operation_id, kind, effect = body.get("operation_id"), body.get("kind"), body.get("effect")
        if not (isinstance(operation_id, str) and _OPERATION_ID.fullmatch(operation_id)):
            return web.json_response({"error": "invalid_operation_id"}, status=400)
        if not isinstance(kind, str) or kind not in _KINDS:
            return web.json_response({"error": "unknown_kind"}, status=422)
        if not isinstance(effect, dict):
            return web.json_response({"error": "invalid_body"}, status=400)
        if kind != "speak" and len(raw) > _MAX_CONTROL_BYTES:
            return web.json_response({"error": "body_too_large"}, status=413)
        if kind != "report" and not _snowflake(effect.get("guild_id")):
            return web.json_response({"error": "wrong_guild"}, status=400)
        now = time.monotonic()
        self._ledger = {k: v for k, v in self._ledger.items() if v[0] > now}
        digest = hashlib.sha256(json.dumps([kind, effect], sort_keys=True).encode()).hexdigest()
        held = self._ledger.get(operation_id)
        if held is not None:
            if held[1] != digest:
                return web.json_response({"error": "operation_id_reused"}, status=409)
            # A duplicate in flight awaits the first execution's answer.
            answered = await asyncio.shield(held[2])
            return _respond(answered)
        started = int(time.time() * 1000)
        answer: asyncio.Future[dict[str, Any] | str] = asyncio.get_running_loop().create_future()
        self._ledger[operation_id] = (now + _LEDGER_S, digest, answer, started)
        try:
            outcome, payload = await self._apply(kind, effect)
        except _BadRequestError as exc:
            del self._ledger[operation_id]
            answer.set_result(str(exc))
            return _respond(str(exc))
        envelope = operation_envelope(outcome, payload, guild_id=str(
            effect.get("guild_id") or ""), trace=_trace(request), started_ms=started)
        # Every settled answer is kept, `unavailable` and `uncertain` too.
        self._ledger[operation_id] = (time.monotonic() + _LEDGER_S, digest, answer, started)
        answer.set_result(envelope)
        return _respond(envelope)

    async def read_operation(self, request: web.Request) -> web.Response:
        held = self._ledger.get(request.match_info["operation_id"])
        if held is None or held[0] <= time.monotonic():
            return self._read("nothing_found", None, _trace(request))
        answered = held[2].result() if held[2].done() else _pending(held[3])
        return self._read("ok", answered, _trace(request))

    async def queries(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            return web.json_response({"error": "invalid_body"}, status=400)
        if body.get("kind") != "status":
            return web.json_response({"error": "unknown_kind"}, status=422)
        params = body.get("params")
        guild_id = params.get("guild_id") if isinstance(params, dict) else None
        if not isinstance(guild_id, str) or not guild_id:
            return web.json_response({"error": "guild_id is required"}, status=400)
        if guild_id in self._forbidden or not self._holds(guild_id):
            return self._read("nothing_found", None, _trace(request))
        return self._read("ok", {
            **self._state(),
            "last_event_seq": self.last_event_seq,
            "outbox_dropped": 0,
            "streaming": self.playing,
            "last_error": None,
            "state_write_ok": self.state_write_ok,
            "wake": {},
            "pump": {},
        }, _trace(request))

    def _holds(self, guild_id: str) -> bool:
        """Whether the fake holds a connection core for this guild."""
        return guild_id == self._guild_id and (self.has_core or self.voice == "connected")

    async def _apply(self, kind: str,
                     effect: dict[str, Any]) -> tuple[OperationOutcome, dict[str, Any]]:
        if kind == "report":
            return "unsupported", _refusal("unsupported")
        guild_id = effect["guild_id"]
        if guild_id in self._forbidden:
            return "denied", _refusal("forbidden_guild")
        if guild_id != self._guild_id:
            # Another admitted guild: no core, and this fake holds only one.
            return ("unavailable", _refusal("join_failed")) if kind == "join" else (
                "rejected", _refusal("unknown_guild"))
        if self.fault is not None:
            return self._answer(
                "uncertain" if self.fault == "internal_error" else "unavailable", self.fault)
        self.applied += 1
        if kind == "join":
            return self._join(effect)
        if not self._holds(guild_id):
            return "rejected", _refusal("unknown_guild")
        if self.generation is None or effect.get("generation") != self.generation:
            return self._answer("rejected", "generation_conflict")
        handler = {"leave": self._leave, "play": self._play, "stop": self._stop,
                   "pause": self._pause, "resume": self._resume,
                   "set_volume": self._volume, "speak": self._speak}[kind]
        return await handler(effect)

    # --- kinds ----------------------------------------------------------------

    def _join(self, effect: dict[str, Any]) -> tuple[OperationOutcome, dict[str, Any]]:
        channel = effect.get("channel_id")
        if not isinstance(channel, str) or not channel or len(channel) > 64:
            raise _BadRequestError("invalid_body")
        self.has_core = True
        if self.voice == "connected":
            if channel == self.channel_id:
                return self._answer("complete")  # idempotent re-join, same channel
            # Discord allows ONE voice connection per bot per guild, so a join
            # to a second channel is a real conflict, not a move.
            return self._answer("rejected", "connected_elsewhere")
        self._seq += 1
        self.voice = "connected"
        self.generation = f"gen-{self._seq}"
        self.channel_id = channel
        return self._answer("complete")

    async def _leave(self, effect: dict[str, Any]) -> tuple[OperationOutcome, dict[str, Any]]:
        self.voice = "idle"
        self.generation = None
        self.channel_id = None
        self.playing = False
        self.paused = False
        self.playback_id = None
        self.speech_active = False
        self.speech_queue = 0
        return self._answer("complete")

    async def _play(self, effect: dict[str, Any]) -> tuple[OperationOutcome, dict[str, Any]]:
        stream_url = effect.get("stream_url")
        if not isinstance(stream_url, str) or not stream_url:
            raise _BadRequestError("invalid_body")
        if effect.get("resumable") is not None and not isinstance(effect["resumable"], bool):
            raise _BadRequestError("invalid_body")
        if self.play_gate is not None:
            await self.play_gate.wait()
        self._seq += 1
        self.playback_id = f"pb-{self._seq}"  # REPLACE: a fresh playback identity
        self.playing = True
        self.paused = False
        self.plays.append(effect)
        return self._answer("complete")

    async def _stop(self, effect: dict[str, Any]) -> tuple[OperationOutcome, dict[str, Any]]:
        # A no-op when idle; either way the CONNECTION stays.
        self.playing = False
        self.paused = False
        self.playback_id = None
        self.stops.append(effect)
        return self._answer("complete")

    async def _pause(self, effect: dict[str, Any]) -> tuple[OperationOutcome, dict[str, Any]]:
        if self.speech_active:
            return self._answer("rejected", "pause_during_speech")
        if self.playing:
            self.paused = True
            return self._answer("complete")
        return self._answer("rejected", "no_music_playing")

    async def _resume(self, effect: dict[str, Any]) -> tuple[OperationOutcome, dict[str, Any]]:
        if self.speech_active:
            return self._answer("rejected", "pause_during_speech")
        if self.paused:
            self.paused = False
            return self._answer("complete")
        if self.playing:
            return self._answer("complete")
        return self._answer("rejected", "no_music_playing")

    async def _volume(self, effect: dict[str, Any]) -> tuple[OperationOutcome, dict[str, Any]]:
        percent = effect.get("percent")
        if (
            isinstance(percent, bool)
            or not isinstance(percent, (int, float))
            or not 0 <= percent <= 200
        ):
            raise _BadRequestError("percent_out_of_range")
        self.volume_percent = percent
        return self._answer("complete")

    async def _speak(self, effect: dict[str, Any]) -> tuple[OperationOutcome, dict[str, Any]]:
        try:
            audio = base64.b64decode(str(effect.get("audio_b64") or ""), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise _BadRequestError("invalid_body") from exc
        if not audio:
            raise _BadRequestError("invalid_body")
        if len(audio) > _MAX_SPEECH_BYTES:
            raise _BadRequestError("audio_too_large")
        if self.speech_active:
            # Another stream is already playing: this one QUEUES behind it,
            # bounded by `_speech_queue_max`, rather than becoming active itself.
            if self.speech_queue >= self._speech_queue_max:
                return self._answer("unavailable", "speech_queue_full")
            self.speech_queue += 1
            return self._answer("complete")
        # Speech hard-cuts music: `finish_speech()` is the drain.
        self.speech_active = True
        return self._answer("complete")

    def app(self) -> web.Application:
        app = web.Application(client_max_size=4 * _MAX_SPEECH_BYTES)
        app.router.add_post("/v1/voice/operations", self.operations)
        app.router.add_get("/v1/voice/operations/{operation_id}", self.read_operation)
        app.router.add_post("/v1/voice/queries", self.queries)
        return app


def operation_envelope(outcome: OperationOutcome, payload: object, *, guild_id: str = "",
                       trace: str = "", started_ms: int | None = None) -> dict[str, Any]:
    """A settled `voice` operation envelope as the controller answers it."""
    now = int(time.time() * 1000)
    return {"outcome": outcome, "payload": payload,
            "when": {"state": "settled", "started_at_ms": started_ms or now,
                     "settled_at_ms": now},
            "from": {"target": guild_id, "platform_ref": None},
            "scope": {"level": "installation"}, "trace": trace}


def read_envelope(outcome: ReadOutcome, payload: object, *, trace: str = "") -> dict[str, Any]:
    """A read envelope as the controller answers it, by its own clock."""
    return {"outcome": outcome, "payload": payload if outcome == "ok" else None,
            "when": {"observed_at_ms": int(time.time() * 1000),
                     "known_by": "controller_clock"},
            "from": {"source": "voice-controller", "healthy": outcome != "unavailable",
                     "failed_sources": []},
            "scope": {"level": "installation"}, "trace": trace}


def _pending(started_ms: int) -> dict[str, Any]:
    return {**operation_envelope("complete", {"state": None}, started_ms=started_ms),
            "outcome": "pending",
            "when": {"state": "started", "started_at_ms": started_ms, "settled_at_ms": None}}


def _trace(request: web.Request) -> str:
    parts = (request.headers.get("traceparent") or "").split("-")
    return parts[1] if len(parts) == 4 and len(parts[1]) == 32 else ""
