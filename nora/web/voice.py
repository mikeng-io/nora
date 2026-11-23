"""`register_voice` — the `intake` contract's `record` of a woken utterance, the
voice channel's text entry point, and the `voice` contract's `report` where the
app holds the hub.

The `voice-wake` service (`deploy/voice-wake/`) hears 「喂 Nora」 in a
Discord voice channel, transcribes the utterance locally, and POSTs the
TRANSCRIPT here. This route builds the `NormalizedMessage` the Discord
adapter's parser would have built for a typed message in that guild's own
text room and hands it to the SAME entry point the adapter's `listen()` loop
calls — the `Dispatcher`'s `on_message` — so a spoken utterance walks the identical
path a typed one does: audit row, the engage gate, and (when the gate admits
it) the reactive turn that answers aloud through the TTS path.

Deliberately a `register_*` function taking an existing `web.Application`,
like `nora.web.api.register_web_routes`: the route joins the health app
`nora/obs/health.py::build_health_app` already builds and `nora/__main__.py`
already serves — a second app would mean a second port.

Two postures, stated here because they are decisions, not omissions:

- **Text only.** Audio never reaches this process — the wake service sends
  `{event_id, kind: "utterance", user_id, text, guild_id}` plus an optional
  `voice_channel_id` and nothing else. There is no audio storage anywhere on this path, by design.
- **Bearer, not network.** The only caller is the wake service on the private
  `nora` docker network, and that is not authentication: any container there
  could reach the port. The route requires the shared `NORA_BRIDGE_TOKEN`
  bearer, and an unset token refuses every utterance. What a forged but
  authenticated POST could do is exactly what any message in an admitted
  conversation does — policy and the engage gate still apply downstream.

An accepted utterance is `complete`: ingest runs inline (it is deterministic —
the same reasoning as `Dispatcher.on_message`), and downstream agent work is
spawned by the dispatcher, never awaited here. An `event_id` recorded in the
last five minutes is `complete` again and is not ingested twice.

**The GUILD decides the room, not the channel.** A server has many voice
channels and gains more, so the caller sends `guild_id` and this route
resolves it to the ONE room the operator marked `voice = true` — see
`nora.policy.Policy.voice_room` for why that is explicit rather than inferred,
and `Conversation.voice` for why speech is a surface of a room rather than a
room of its own. An unresolvable guild is `rejected` and nothing is ingested
or kept: the resolution IS the admission check, so failing it must not fall through to a
room somebody else's speech belongs in.

The real `voice_channel_id` survives on `raw` as provenance, and is OPTIONAL:
nothing routes on it — it is there so a trace can say which channel a sentence
was spoken in, which the resolved `conversation_id` no longer carries.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, get_args

from aiohttp import web

from nora.domain.models import NormalizedMessage
from nora.domain.service_api import OperationOutcome
from nora.domain.service_api_voice import IntakeOperationKind, VoiceOperationKind
from nora.infra.service_auth import service_auth_ok
from nora.platform.bridge.answers import (
    EventOnce,
    InvalidOperation,
    OperationLedger,
    now_ms,
    operation_request,
    pending_body,
    report_answer,
    settled_body,
    trace_of,
)

log = logging.getLogger("nora.web.voice")

INTAKE_KINDS = frozenset(get_args(IntakeOperationKind))
VOICE_KINDS = frozenset(get_args(VoiceOperationKind))
SOURCE = "nora"
Answer = tuple[OperationOutcome, dict[str, Any]]


def _text(effect: dict[str, Any], key: str) -> str:
    value = effect.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperation("event_id, user_id, text and guild_id are required")
    return value


def register_voice(
    app: web.Application,
    *,
    ingest: Callable[[NormalizedMessage], Awaitable[None]],
    resolve_voice_room: Callable[[str], str | None],
    on_voice_event: Callable[[object], Awaitable[tuple[int, dict[str, Any]]]] | None = None,
) -> None:
    """`ingest` is the SAME callable a platform adapter's `listen()` loop
    receives — production wires `deps.dispatcher.on_message` (see
    `nora/__main__.py`), so nothing about the voice path re-implements any
    part of the typed-message path.

    `resolve_voice_room` maps a Discord guild id to the `conversation_id` its
    speech belongs in, or `None` to refuse. Production wires
    `Policy.voice_room`; it is a callable rather than the `Policy` itself so
    this module keeps knowing nothing about policy loading, and so a test can
    state the mapping in one line.

    `on_voice_event`, when provided, also registers `POST /v1/voice/operations`
    and its read-back: the audio-controller's `report`s, answered `unsupported`
    for every other `voice` kind. The consumer owns every validation and
    routing decision; this route reads its (status, payload) verdict as an
    outcome. When `None`, the voice routes do not exist at all."""
    ledger = OperationLedger()
    #: Utterances ingested in the last five minutes, by `event_id`.
    recorded = EventOnce(bound=4096, ttl_s=300.0)

    async def record(effect: dict[str, Any]) -> Answer:
        event_id, user_id = _text(effect, "event_id"), _text(effect, "user_id")
        text, guild_id = _text(effect, "text"), _text(effect, "guild_id")
        if effect.get("kind") != "utterance":
            raise InvalidOperation("kind must be utterance")
        # OPTIONAL on purpose: the guild DECIDES the room, the channel only
        # records where the words were said; an absent channel is honest.
        raw_channel = effect.get("voice_channel_id")
        voice_channel_id = raw_channel if isinstance(raw_channel, str) and raw_channel else None
        conversation_id = resolve_voice_room(guild_id)
        if conversation_id is None:
            # Not a fallback to the channel id: an unmarked guild has no room
            # the operator chose for speech, and guessing one writes a spoken
            # sentence into somebody else's notebook.
            log.warning(
                "voice utterance from guild %s refused: no conversation in "
                "policy.toml marks voice = true for it (channel %s)",
                guild_id, voice_channel_id,
            )
            return "rejected", {"error": {"kind": "no_voice_room",
                                          "message": "no voice room for this guild"}}
        message = NormalizedMessage(
            platform="discord",
            conversation_id=conversation_id,
            # A voice channel is a shared room — "group", never "dm".
            conversation_type="group",
            sender_id=user_id,
            # The snowflake says WHO spoke; the display name is Discord's to
            # give and this path never saw it — None, not a guess.
            sender_name=None,
            timestamp=int(time.time() * 1000),
            body=text,
            raw={
                "voice": True,
                "user_id": user_id,
                # PROVENANCE, never routing: `conversation_id` is the guild's
                # text room, so these are the only record of where it was said.
                "guild_id": guild_id,
                "voice_channel_id": voice_channel_id,
            },
            voice=True,
        )
        await recorded.once(event_id, lambda: ingest(message))
        return "complete", {}

    async def report(kind: str, effect: dict[str, Any]) -> Answer:
        if kind != "report" or on_voice_event is None:
            return "unsupported", {"error": {"kind": "unsupported",
                                             "message": f"the app does not serve {kind}"}}
        return report_answer(*await on_voice_event(effect))

    def serve(kinds: frozenset[str],
              apply: Callable[[str, dict[str, Any]], Awaitable[Answer]],
              running: dict[str, Any],
              ) -> Callable[[web.Request], Awaitable[web.Response]]:
        async def submit(request: web.Request) -> web.Response:
            # Checked before the body is read: an unauthenticated caller gets
            # no parse, no resolution and no effect.
            if not service_auth_ok(request.headers.get("Authorization")):
                return web.json_response({"error": "unauthorized"}, status=401)
            parsed = await operation_request(request, kinds)
            if isinstance(parsed, web.Response):
                return parsed
            operation_id, kind, effect = parsed
            started, trace = now_ms(), trace_of(request.headers)
            target = str(effect.get("guild_id") or "")

            async def work() -> dict[str, Any]:
                outcome, payload = await apply(kind, effect)
                return settled_body(outcome, payload, target=target, trace=trace,
                                    started_ms=started)

            return await ledger.respond(request, operation_id, kind, effect, work, pending_body(
                running, target=target, trace=trace, started_ms=started))

        return submit

    async def read(request: web.Request) -> web.Response:
        if not service_auth_ok(request.headers.get("Authorization")):
            return web.json_response({"error": "unauthorized"}, status=401)
        return ledger.read(request, source=SOURCE)

    app.router.add_post("/v1/intake/operations",
                        serve(INTAKE_KINDS, lambda _kind, effect: record(effect), {}))
    app.router.add_get("/v1/intake/operations/{operation_id}", read)
    if on_voice_event is not None:
        app.router.add_post("/v1/voice/operations", serve(VOICE_KINDS, report, {"state": None}))
        app.router.add_get("/v1/voice/operations/{operation_id}", read)
