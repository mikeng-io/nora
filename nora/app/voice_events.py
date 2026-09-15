"""`VoiceEventConsumer` — the nora half of the controller → nora callback
contract (design/voice-channel/07 §5, B5/B9/B10).

The audio-controller POSTs lifecycle events to `/app/v1/voice/event`
(registered in `nora/web/voice.py`): music advancement (`track-finished`,
`stream-failed`), connection death (`voice-death`), the empty-channel
observation (`channel-emptied`), and the speech acknowledgement
(`speech-finished`). Every decision stays here — the controller never
advances nora's queue itself.

Two guards make a replayed or stale event harmless (§3.3):

- `event_id` dedupe via the hub (exactly-once, bounded window);
- `playback_id` must match the guild's CURRENT expected playback before a
  music event may advance the queue — a `track-finished` for a track that
  was already skipped/superseded is answered `stale` and never advances.
  The match is evaluated by the manager UNDER its per-guild lock (B5):
  checked anywhere earlier, an event arriving while a skip holds the lock
  would pass against the old id and advance twice.

Serialization: the `MusicPlayerManager` methods called here each take the
per-guild lock themselves, so consuming two events for one guild
concurrently cannot interleave two transitions on one player (B5).
"""

from __future__ import annotations

import logging
from typing import Any

from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.discord.music_manager import MusicPlayerManager

log = logging.getLogger("nora.app.voice_events")

_KNOWN_TYPES = frozenset({
    "track-finished", "stream-failed", "speech-finished",
    "voice-death", "channel-emptied",
})


class VoiceEventConsumer:
    def __init__(self, manager: MusicPlayerManager, hub: VoiceControllerHub) -> None:
        self._manager = manager
        self._hub = hub

    async def consume(self, body: object) -> tuple[int, dict[str, Any]]:
        """One callback → (http_status, payload). NEVER raises: the
        controller retries on non-2xx, so every well-formed-but-inapplicable
        event is answered 200 with a `status` naming why nothing happened,
        and only a malformed body earns a 400."""
        if not isinstance(body, dict):
            return 400, {"error": "body must be an object"}
        event_type = body.get("type")
        event_id = body.get("event_id")
        if (
            not isinstance(event_type, str) or not event_type
            or not isinstance(event_id, str) or not event_id
        ):
            return 400, {"error": "type and event_id are required"}
        if not self._hub.consume_event_id(event_id):
            return 200, {"status": "duplicate"}
        if event_type not in _KNOWN_TYPES:
            return 400, {"error": f"unknown event type: {event_type}"}
        guild_id = body.get("guild_id")
        if not isinstance(guild_id, str) or not guild_id:
            return 400, {"error": "guild_id is required"}

        if event_type == "speech-finished":
            return 200, {"status": "ok"}
        if event_type == "voice-death":
            # The connection is already gone controller-side — forget the
            # session WITHOUT an exit call, then run the leave-voice
            # teardown (which announces, as it always has).
            self._hub.drop(guild_id)
            await self._manager.on_voice_death(guild_id)
            return 200, {"status": "ok"}
        if event_type == "channel-emptied":
            await self._manager.on_voice_channel_emptied(guild_id)
            return 200, {"status": "ok"}

        # `track-finished` / `stream-failed`: advance ONLY for the playback
        # nora currently expects — anything else is the tail of a
        # skipped/superseded track and must not move the queue. The check
        # itself happens in the manager, UNDER the guild lock (B5): checked
        # here instead, an event arriving mid-skip would pass against the
        # old expected id and advance twice.
        playback_id = body.get("playback_id")
        if not isinstance(playback_id, str):
            return 200, {"status": "stale"}
        if event_type == "stream-failed":
            log.warning(
                "music stream failed for guild %s (playback %s): %s — advancing",
                guild_id, playback_id, body.get("reason"),
            )
        advanced = await self._manager.on_track_finished(guild_id, playback_id=playback_id)
        return 200, {"status": "ok" if advanced else "stale"}
