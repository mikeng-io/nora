"""`register_voice` — POST /app/v1/voice/utterance, the voice channel's text
entry point.

The `voice-wake` service (`deploy/voice-wake/`) hears 「喂 Nora」 in a
Discord voice channel, transcribes the utterance locally, and POSTs the
TRANSCRIPT here. This route builds the `NormalizedMessage` the Discord
adapter's parser would have built for a typed message in that guild's own
text room and hands it to the SAME entry point the adapter's `listen()` loop
calls — the `Dispatcher`'s `on_message` — so a spoken utterance walks the identical
path a typed one does: audit row, Honcho memory feed as a user input, the
engage gate, and (when the gate admits it) the reactive turn that answers
aloud through the TTS path.

Deliberately a `register_*` function taking an existing `web.Application`,
like `nora.web.api.register_web_routes`: the route joins the health app
`nora/obs/health.py::build_health_app` already builds and `nora/__main__.py`
already serves — a second app would mean a second port.

Two postures, stated here because they are decisions, not omissions:

- **Text only.** Audio never reaches this process — the wake service sends
  `{user_id, text, guild_id}` plus an optional `voice_channel_id` and nothing
  else. There is no audio storage anywhere on this path, by design.
- **No auth.** The only caller is the wake service on the private `nora`
  docker network; the port is published to the host on loopback only, same
  as every other route on this app. What a forged POST could do is exactly
  what any message in an admitted conversation does — policy and the engage
  gate still apply downstream, unchanged.

Returns 202 Accepted: ingest runs inline (it is deterministic — the same
reasoning as `Dispatcher.on_message`), and downstream agent work is spawned
by the dispatcher, never awaited here.

**The GUILD decides the room, not the channel.** A server has many voice
channels and gains more, so the caller sends `guild_id` and this route
resolves it to the ONE room the operator marked `voice = true` — see
`nora.policy.Policy.voice_room` for why that is explicit rather than inferred,
and `Conversation.voice` for why speech is a surface of a room rather than a
room of its own. An unresolvable guild is a 400 and nothing is ingested: the
resolution IS the admission check, so failing it must not fall through to a
room somebody else's speech belongs in.

The real `voice_channel_id` survives on `raw` as provenance, and is OPTIONAL:
nothing routes on it — it is there so a trace can say which channel a sentence
was spoken in, which the resolved `conversation_id` no longer carries.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from nora.domain.models import NormalizedMessage

log = logging.getLogger("nora.web.voice")


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

    `on_voice_event`, when provided, also registers
    `POST /app/v1/voice/event` — the audio-controller's callback channel
    The consumer owns every validation and
    routing decision; this route only parses JSON and passes the
    (status, payload) verdict through. When `None`, the event route does
    not exist at all — a deployment without the controller wired has no
    endpoint to poke."""

    async def voice_utterance(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "invalid json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "invalid body"}, status=400)
        user_id = body.get("user_id")
        text = body.get("text")
        guild_id = body.get("guild_id")
        if not (
            isinstance(user_id, str) and user_id
            and isinstance(text, str) and text.strip()
            and isinstance(guild_id, str) and guild_id
        ):
            return web.json_response(
                {"error": "user_id, text and guild_id are required"},
                status=400,
            )
        # OPTIONAL on purpose: the guild DECIDES the room, the channel only
        # records where the words were said. Only the controller knows which
        # channel it is currently connected to, and a caller that could not
        # reach it must be able to say so — an absent channel is honest, a
        # stale configured one is a lie in the one field a trace would be
        # read for.
        raw_channel = body.get("voice_channel_id")
        voice_channel_id = raw_channel if isinstance(raw_channel, str) and raw_channel else None
        conversation_id = resolve_voice_room(guild_id)
        if conversation_id is None:
            # Not a fallback to the channel id: an unmarked guild has no room
            # the operator chose for speech, and guessing one writes a spoken
            # sentence into somebody else's notebook. Loud, and counted where
            # every other room refusal is — the scope stage never sees this
            # message, so it cannot count it for us.
            log.warning(
                "voice utterance from guild %s refused: no conversation in "
                "policy.toml marks voice = true for it (channel %s)",
                guild_id, voice_channel_id,
            )
            return web.json_response(
                {"error": "no voice room for this guild"}, status=400,
            )
        message = NormalizedMessage(
            platform="discord",
            conversation_id=conversation_id,
            # A voice channel is a shared room — "group", never "dm" (the
            # wake service only ever joins a guild channel).
            conversation_type="group",
            sender_id=user_id,
            # The Discord snowflake says WHO spoke (protocol-native identity); the
            # display name is Discord's to give and this path never saw it —
            # None, not a guess.
            sender_name=None,
            timestamp=int(time.time() * 1000),
            body=text,
            raw={
                "voice": True,
                "user_id": user_id,
                # PROVENANCE, never routing: `conversation_id` above is the
                # guild's text room, so these two are the only record of
                # where the sentence was actually spoken.
                "guild_id": guild_id,
                "voice_channel_id": voice_channel_id,
            },
            voice=True,
        )
        await ingest(message)
        return web.json_response({"status": "accepted"}, status=202)

    app.router.add_post("/app/v1/voice/utterance", voice_utterance)

    if on_voice_event is not None:

        async def voice_event(request: web.Request) -> web.Response:
            try:
                body = await request.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                return web.json_response({"error": "invalid json"}, status=400)
            status, payload = await on_voice_event(body)
            return web.json_response(payload, status=status)

        app.router.add_post("/app/v1/voice/event", voice_event)
