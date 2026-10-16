"""`VoiceEventConsumer` — the nora half of the controller → nora callback
contract.

The audio-controller POSTs lifecycle events to `/app/v1/voice/event`
(registered in `nora/web/voice.py`): music advancement (`track-finished`,
`stream-failed`), connection death (`voice-death`), the empty-channel
observation (`channel-emptied`), and the speech acknowledgement
(`speech-finished`). Every decision stays here — the controller never
advances nora's queue itself.

The guards that make a replayed, failed or stale event harmless:

- `event_id` dedupe via the hub (exactly-once, bounded window) — committed
  ONLY after the event's transition succeeded: a failed delivery
  answers 500 and stays retryable, so the controller's retry is PROCESSED,
  never acknowledged as a duplicate while the work is still undone;
- `playback_id` must match the guild's CURRENT expected playback before a
  music event may advance the queue — a `track-finished` for a track that
  was already skipped/superseded is answered `stale` and never advances.
  The match is evaluated by the manager UNDER its per-guild lock:
  checked anywhere earlier, an event arriving while a skip holds the lock
  would pass against the old id and advance twice;
- `voice-death` / `channel-emptied` carry the controller's `generation`
  (and `channel_id`), validated against the live session UNDER the same
  lock as the teardown: an outbox event delayed across an exit/rejoin
  must not tear down the NEW session. Both checks live in the manager.

Serialization: the `MusicPlayerManager` methods called here each take the
per-guild lock themselves, so consuming two events for one guild
concurrently cannot interleave two transitions on one player.
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


def _str_field(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) and value else None


class VoiceEventConsumer:
    def __init__(self, manager: MusicPlayerManager, hub: VoiceControllerHub) -> None:
        self._manager = manager
        self._hub = hub

    async def consume(self, body: object) -> tuple[int, dict[str, Any]]:
        """One callback → (http_status, payload). NEVER raises: the
        controller retries on non-2xx, so a well-formed event whose work
        FAILED answers 500 WITHOUT consuming the `event_id` (the retry
        will be processed), a well-formed-but-inapplicable one answers
        200 with a `status` naming why nothing happened, and only a
        malformed body earns a 400."""
        if not isinstance(body, dict):
            return 400, {"error": "body must be an object"}
        event_type = body.get("type")
        event_id = body.get("event_id")
        if (
            not isinstance(event_type, str) or not event_type
            or not isinstance(event_id, str) or not event_id
        ):
            return 400, {"error": "type and event_id are required"}
        if event_type not in _KNOWN_TYPES:
            return 400, {"error": f"unknown event type: {event_type}"}
        guild_id = body.get("guild_id")
        if not isinstance(guild_id, str) or not guild_id:
            return 400, {"error": "guild_id is required"}
        # A guild nora has no controller for cannot have emitted this. The
        # route has no auth by design (its only caller is a controller on the
        # private compose network), so without this check every distinct
        # `guild_id` a caller invents permanently adds an entry to the hub's
        # per-guild seq bookmark — an unbounded map keyed by request input. It
        # would also answer `200 ok` to an event for a guild it could do nothing
        # about, which is a fail-OPEN.
        if guild_id not in self._hub.guild_ids:
            log.warning(
                "voice event %s (%s) names guild %s, which has no configured "
                "audio-controller ([speech.tts] sidecars) — refused",
                event_id, event_type, guild_id,
            )
            return 400, {"error": "unknown guild"}
        if self._hub.seen_event(event_id):
            return 200, {"status": "duplicate"}
        event_seq = body.get("event_seq")
        if isinstance(event_seq, int) and not isinstance(event_seq, bool):
            last = self._hub.last_event_seq(guild_id)
            if last is not None and event_seq > last + 1:
                log.warning(
                    "voice event seq gap for guild %s: last consumed %d, arrived "
                    "%d — %d event(s) may have been lost while nora was down",
                    guild_id, last, event_seq, event_seq - last - 1,
                )

        try:
            status, payload = await self._process(event_type, guild_id, body)
        except Exception:
            # The transition RAISED (a dead-generation play climbing
            # out of the advance, a store failure) — nothing was consumed,
            # so the controller's outbox retry gets a genuine second
            # attempt instead of a `duplicate` acknowledgement of work
            # that never happened.
            log.exception(
                "voice event %s (%s) for guild %s failed — left unconsumed for retry",
                event_id, event_type, guild_id,
            )
            return 500, {"error": "event processing failed; left unconsumed for retry"}
        # Commit the dedupe ONLY on success — and only now bookmark the
        # seq, so a failed delivery never moves the gap baseline forward.
        self._hub.consume_event_id(event_id)
        if isinstance(event_seq, int) and not isinstance(event_seq, bool):
            self._hub.note_event_seq(guild_id, event_seq)
        return status, payload

    async def _process(
        self, event_type: str, guild_id: str, body: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        if event_type == "speech-finished":
            return 200, {"status": "ok"}
        if event_type == "voice-death":
            # The generation check AND the hub drop happen inside the
            # manager, under the guild lock, before any teardown — a
            # delayed death from an old generation must not touch the NEW
            # session.
            await self._manager.on_voice_death(
                guild_id, generation=_str_field(body, "generation"),
            )
            return 200, {"status": "ok"}
        if event_type == "channel-emptied":
            await self._manager.on_voice_channel_emptied(
                guild_id,
                generation=_str_field(body, "generation"),
                channel_id=_str_field(body, "channel_id"),
            )
            return 200, {"status": "ok"}

        # `track-finished` / `stream-failed`: advance ONLY for the playback
        # nora currently expects — anything else is the tail of a
        # skipped/superseded track and must not move the queue. The check
        # itself happens in the manager, UNDER the guild lock: checked
        # here instead, an event arriving mid-skip would pass against the
        # old expected id and advance twice.
        playback_id = body.get("playback_id")
        if not isinstance(playback_id, str):
            return 200, {"status": "stale"}
        if event_type == "stream-failed":
            # NOT the same transition as a natural end: a track that failed to
            # START has not finished, and the manager re-resolves it before it
            # will move the queue. Routing this to `on_track_finished` would
            # advance every failure into another track resolved through the same
            # broken source session.
            handled = await self._manager.on_stream_failed(
                guild_id, playback_id=playback_id, reason=_str_field(body, "reason"),
            )
            return 200, {"status": "ok" if handled else "stale"}
        advanced = await self._manager.on_track_finished(guild_id, playback_id=playback_id)
        return 200, {"status": "ok" if advanced else "stale"}
