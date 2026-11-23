"""The Discord bridge's `voice` contract routes (`nora.domain.service_api_voice`).

The bridge holds the voice hub, so it relays `join`, `leave` and `speak` to it,
takes the controller's `report`s into the injected event consumer, and answers
the `status` query from what the hub knows. Every other kind is `unsupported`.
"""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Callable
from typing import Any, Protocol, get_args

from aiohttp import web

from nora.domain.service_api import OperationOutcome
from nora.domain.service_api_voice import VoiceOperationKind
from nora.infra.voice.http_controller import (
    GenerationConflict,
    VoiceControllerError,
    VoiceControllerNoConnection,
)
from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.bridge.answers import (
    INSTALLATION,
    InvalidOperation,
    OperationLedger,
    envelope_response,
    now_ms,
    operation_request,
    pending_body,
    read_body,
    report_answer,
    settled_body,
    trace_of,
)

from .music_manager import MusicPlayerManager

log = logging.getLogger("discord-bridge")

VOICE_KINDS = frozenset(get_args(VoiceOperationKind))
_RELAYED = frozenset({"join", "leave", "speak", "report"})
SOURCE = "discord"


class VoiceEventSink(Protocol):
    """`VoiceEventConsumer`'s shape: it lives in `nora.app`, which this package
    may not import, so the composition entry injects it."""

    async def consume(self, body: object) -> tuple[int, dict[str, Any]]: ...


def _field(effect: dict[str, Any], key: str) -> str:
    value = effect.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidOperation(f"{key} is required")
    return value


class BridgeVoiceSurface:
    """`POST /v1/voice/operations`, its read-back, and `POST /v1/voice/queries`.

    `hub`, `manager` and `sink` are read on each request; `None` is voice (or
    reports) not configured on this bridge.
    """

    def __init__(self, *, hub: Callable[[], VoiceControllerHub | None],
                 manager: Callable[[], MusicPlayerManager | None],
                 sink: Callable[[], VoiceEventSink | None],
                 max_speech_bytes: Callable[[], int]) -> None:
        self._hub = hub
        self._manager = manager
        self._sink = sink
        self._max_speech_bytes = max_speech_bytes
        self._ledger = OperationLedger()

    def add_routes(self, app: web.Application) -> None:
        app.router.add_post("/v1/voice/operations", self.submit_operation)
        app.router.add_get("/v1/voice/operations/{operation_id}", self.read_operation)
        app.router.add_post("/v1/voice/queries", self.post_query)

    def _state(self, guild_id: str) -> dict[str, Any]:
        hub = self._hub()
        session = hub.session_for(guild_id) if hub is not None else None
        return {"generation": session.generation if session is not None else None,
                "channel_id": hub.channel_for(guild_id) if hub is not None else None}

    async def submit_operation(self, req: web.Request) -> web.Response:
        # The JSON body carries a `speak`'s audio as base64: 4/3 of the bound, and room.
        limit = self._max_speech_bytes() * 4 // 3 + 64 * 1024
        parsed = await operation_request(req, VOICE_KINDS, max_bytes=limit)
        if isinstance(parsed, web.Response):
            return parsed
        operation_id, kind, effect = parsed
        started, trace = now_ms(), trace_of(req.headers)
        target = str(effect.get("guild_id") or "")

        async def work() -> dict[str, Any]:
            outcome, payload = await self._apply(kind, effect)
            return settled_body(outcome, payload, target=target, trace=trace, started_ms=started)

        return await self._ledger.respond(req, operation_id, kind, effect, work, pending_body(
            {"state": None}, target=target, trace=trace, started_ms=started))

    async def read_operation(self, req: web.Request) -> web.Response:
        return self._ledger.read(req, source=SOURCE)

    async def post_query(self, req: web.Request) -> web.Response:
        try:
            body = await req.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(params := body.get("params"), dict):
            return web.json_response({"error": "body must be a JSON object"}, status=400)
        if body.get("kind") != "status":
            return web.json_response({"error": "unknown query kind"}, status=422)
        guild_id = params.get("guild_id")
        if not isinstance(guild_id, str) or not guild_id:
            return web.json_response({"error": "guild_id is required"}, status=400)
        state = self._state(guild_id)
        connected = state["generation"] is not None
        return envelope_response(read_body(
            "ok", {"connected": connected, "session": connected, **state},
            source=SOURCE, trace=trace_of(req.headers), scope=INSTALLATION))

    async def _apply(self, kind: str, effect: dict[str, Any]
                     ) -> tuple[OperationOutcome, dict[str, Any]]:
        if kind == "report":
            sink = self._sink()
            if sink is None:
                return "unsupported", _error("unsupported", "reports are not enabled")
            return report_answer(*await sink.consume(effect))
        hub = self._hub()
        if kind not in _RELAYED or hub is None:
            return "unsupported", _error("unsupported", f"this bridge does not relay {kind}")
        guild_id = _field(effect, "guild_id")
        if kind == "join":
            channel_id = _field(effect, "channel_id")
            try:
                await hub.join(guild_id, channel_id)
            except VoiceControllerError as exc:
                log.error("voice join failed: %s", type(exc).__name__)
                return "unavailable", self._refusal(guild_id, "controller_unavailable")
            return "complete", {"state": self._state(guild_id)}
        audio = _audio(effect, self._max_speech_bytes()) if kind == "speak" else b""
        session = hub.session_for(guild_id)
        if session is None:
            return "rejected", self._refusal(guild_id, "no_connection")
        if effect.get("generation") != session.generation:
            return "rejected", self._refusal(guild_id, "generation_conflict")
        try:
            if kind == "speak":
                await hub.speak(guild_id, audio, _field(effect, "media_type"))
            else:
                await hub.leave(guild_id)
        except GenerationConflict:
            # The controller restarted or replaced the connection behind the
            # cache: adopt what it holds now, so the state names the generation.
            if not await session.reconcile_generation():
                hub.drop(guild_id)
            return "rejected", self._refusal(guild_id, "generation_conflict")
        except VoiceControllerNoConnection:
            hub.drop(guild_id)
            return "rejected", self._refusal(guild_id, "no_connection")
        except VoiceControllerError as exc:
            log.error("voice %s failed: %s", kind, type(exc).__name__)
            return "unavailable", self._refusal(guild_id, "controller_unavailable")
        manager = self._manager()
        if kind == "leave" and manager is not None:
            # The controller confirmed the exit; what is left is the player's
            # local teardown, whose controller calls meet a generation that is gone.
            try:
                await manager.leave_voice(guild_id)
            except VoiceControllerError as exc:
                log.warning("music teardown after a leave failed: %s", type(exc).__name__)
        return "complete", {"state": self._state(guild_id)}

    def _refusal(self, guild_id: str, kind: str) -> dict[str, Any]:
        return {"state": self._state(guild_id), **_error(kind, kind.replace("_", " "))}


def _error(kind: str, message: str) -> dict[str, Any]:
    return {"error": {"kind": kind, "message": message}}


def _audio(effect: dict[str, Any], bound: int) -> bytes:
    """A `speak` effect's audio, decoded; empty, malformed or over `bound` is a 400."""
    _field(effect, "media_type")
    try:
        audio = base64.b64decode(_field(effect, "audio_b64"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidOperation("audio_b64 is not base64") from exc
    if not audio:
        raise InvalidOperation("audio is empty")
    if len(audio) > bound:
        raise InvalidOperation(f"audio is over {bound} bytes")
    return audio
