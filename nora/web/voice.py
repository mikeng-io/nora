"""`register_voice` — POST /app/v1/voice/utterance, the voice channel's text
entry point (design/voice-channel/, stage 3).

The `voice-wake` service (`deploy/voice-wake/`) hears 「喂 Nora」 in a
Discord voice channel, transcribes the utterance locally, and POSTs the
TRANSCRIPT here. This route builds the `NormalizedMessage` the Discord
adapter's parser would have built for a typed message in the same channel
and hands it to the SAME entry point the adapter's `listen()` loop calls —
the `Dispatcher`'s `on_message` — so a spoken utterance walks the identical
path a typed one does: audit row, Honcho memory feed as a user input, the
engage gate, and (when the gate admits it) the reactive turn that answers
aloud through the stage-1 TTS path.

Deliberately a `register_*` function taking an existing `web.Application`,
like `nora.web.api.register_web_routes`: the route joins the health app
`nora/obs/health.py::build_health_app` already builds and `nora/__main__.py`
already serves — a second app would mean a second port.

Two postures, stated here because they are decisions, not omissions:

- **Text only.** Audio never reaches this process — the wake service sends
  `{user_id, text, conversation_id}` and nothing else. There is no audio
  storage anywhere on this path, by design.
- **No auth.** The only caller is the wake service on the private `nora`
  docker network; the port is published to the host on loopback only, same
  as every other route on this app. What a forged POST could do is exactly
  what any message in an admitted conversation does — policy and the engage
  gate still apply downstream, unchanged.

Returns 202 Accepted: ingest runs inline (it is deterministic — the same
reasoning as `Dispatcher.on_message`), and downstream agent work is spawned
by the dispatcher, never awaited here.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable

from aiohttp import web

from nora.domain.models import NormalizedMessage

log = logging.getLogger("nora.web.voice")


def register_voice(
    app: web.Application,
    *,
    ingest: Callable[[NormalizedMessage], Awaitable[None]],
) -> None:
    """`ingest` is the SAME callable a platform adapter's `listen()` loop
    receives — production wires `deps.dispatcher.on_message` (see
    `nora/__main__.py`), so nothing about the voice path re-implements any
    part of the typed-message path."""

    async def voice_utterance(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "invalid json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "invalid body"}, status=400)
        user_id = body.get("user_id")
        text = body.get("text")
        conversation_id = body.get("conversation_id")
        if not (
            isinstance(user_id, str) and user_id
            and isinstance(text, str) and text.strip()
            and isinstance(conversation_id, str) and conversation_id
        ):
            return web.json_response(
                {"error": "user_id, text and conversation_id are required"},
                status=400,
            )
        message = NormalizedMessage(
            platform="discord",
            conversation_id=conversation_id,
            # A voice channel is a shared room — "group", never "dm" (the
            # wake service only ever joins a guild channel).
            conversation_type="group",
            sender_id=user_id,
            # The Discord snowflake says WHO spoke (protocol-native identity,
            # design/voice-channel/05); the display name is Discord's to give
            # and this path never saw it — None, not a guess.
            sender_name=None,
            timestamp=int(time.time() * 1000),
            body=text,
            raw={"voice": True, "user_id": user_id, "channel_id": conversation_id},
        )
        await ingest(message)
        return web.json_response({"status": "accepted"}, status=202)

    app.router.add_post("/app/v1/voice/utterance", voice_utterance)
