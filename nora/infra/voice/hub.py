"""`VoiceControllerHub` — the per-guild voice-session registry nora's
command and application layers talk to (design/voice-channel/07 §3.1/§12).

One `HttpVoiceSession` per guild, cached on first join. The hub is also
where two pieces of cross-request state live, because both are scoped to
"what nora currently believes about the controller", not to any one call:

- **boot reconciliation (§12.1)** — a nora restart loses the generation it
  minted, but the controller's connection survives it. A `/voice/join` that
  comes back 409 means "already connected"; the hub then reads
  `/voice/status` and ADOPTS the live generation when it points at the
  channel nora asked for (and only then — connected elsewhere re-raises).
- **event bookkeeping (§3.3, B5)** — the expected `playback_id` per guild
  (read off the cached session) and the exactly-once `event_id` dedupe the
  `/app/v1/voice/event` consumer relies on.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any
from uuid import uuid4

from nora.infra.voice.http_controller import (
    GenerationConflict,
    HttpVoiceSession,
    VoiceControllerClient,
    VoiceControllerError,
    VoiceControllerUnavailable,
)

log = logging.getLogger("nora.infra.voice")

#: Bound on the event-id dedupe window (FIFO). Controller callbacks retry
#: from a ≤256-event outbox (§3.3), so 1024 covers several full replays.
_EVENT_ID_BOUND = 1024


class VoiceControllerHub:
    def __init__(self, client: VoiceControllerClient) -> None:
        self._client = client
        self._sessions: dict[str, HttpVoiceSession] = {}
        self._seen_event_ids: deque[str] = deque()
        self._seen_event_id_set: set[str] = set()

    async def join(self, guild_id: str, channel_id: str) -> HttpVoiceSession:
        """The cached session if this guild already has one; otherwise
        `/voice/join`, adopting the controller's existing connection on a
        409 that turns out to BE the channel asked for."""
        cached = self._sessions.get(guild_id)
        if cached is not None:
            return cached
        try:
            state = await self._client.join(
                guild_id, channel_id, request_id=str(uuid4()),
            )
        except GenerationConflict:
            state = await self._adopt_existing(guild_id, channel_id)
        session = self._build_session(guild_id, state)
        self._sessions[guild_id] = session
        return session

    async def _adopt_existing(self, guild_id: str, channel_id: str) -> dict[str, Any]:
        """§12.1 boot reconciliation: the join 409'd because the controller
        is already connected. Adopt its generation ONLY when the live
        connection is to the very channel requested — anything else is a
        real conflict and climbs back out as one."""
        status = await self._client.status()
        if status.get("voice") == "connected" and status.get("channel_id") == channel_id:
            log.info(
                "voice join for guild %s: adopting the controller's existing "
                "connection (channel %s)", guild_id, channel_id,
            )
            return status
        raise GenerationConflict(
            f"controller is connected elsewhere "
            f"(voice={status.get('voice')!r}, channel_id={status.get('channel_id')!r})"
        )

    def _build_session(self, guild_id: str, state: dict[str, Any]) -> HttpVoiceSession:
        generation = state.get("generation")
        if not isinstance(generation, str) or not generation:
            raise VoiceControllerUnavailable(
                "join succeeded but the state snapshot carries no generation"
            )
        session = HttpVoiceSession(self._client, guild_id=guild_id, generation=generation)
        playback_id = state.get("playback_id")
        session.playback_id = playback_id if isinstance(playback_id, str) else None
        return session

    async def exit(self, guild_id: str) -> bool:
        """Drop the cached session and exit the connection. `False` when
        there was no session, or the exit itself failed (the cache is
        dropped either way — a dead connection must not be reused)."""
        session = self._sessions.pop(guild_id, None)
        if session is None:
            return False
        try:
            await session.disconnect()
        except VoiceControllerError as exc:
            log.warning("voice exit for guild %s failed: %s", guild_id, exc)
            return False
        return True

    def session_for(self, guild_id: str) -> HttpVoiceSession | None:
        return self._sessions.get(guild_id)

    def drop(self, guild_id: str) -> None:
        """Forget the session WITHOUT an exit call — for the controller's
        own `voice-death` event, where the connection is already gone.
        (Named `drop`, not `clear`: `clear` is a store method name and the
        store-caller gate must be able to resolve it.)"""
        self._sessions.pop(guild_id, None)

    async def stream_speech(self, guild_id: str, audio: bytes, media_type: str) -> bool:
        """`POST /stream` for the guild's current generation. NEVER raises —
        `False` (with a log saying why) when there is no session, the
        generation is stale, or the controller is unavailable. A
        `GenerationConflict` also drops the cached session: the controller
        no longer recognises it, so nothing may stream through it again."""
        session = self._sessions.get(guild_id)
        if session is None:
            log.info("speech for guild %s dropped: no voice session", guild_id)
            return False
        try:
            await self._client.stream(
                session.generation, audio,
                media_type=media_type, guild_id=guild_id, request_id=str(uuid4()),
            )
        except GenerationConflict as exc:
            log.warning(
                "speech for guild %s dropped: %s — dropping the stale session",
                guild_id, exc,
            )
            self.drop(guild_id)
            return False
        except VoiceControllerError as exc:
            log.warning("speech for guild %s failed: %s", guild_id, exc)
            return False
        return True

    def expected_playback_id(self, guild_id: str) -> str | None:
        session = self._sessions.get(guild_id)
        return session.playback_id if session is not None else None

    def consume_event_id(self, event_id: str) -> bool:
        """Exactly-once dedupe for controller callbacks (§3.3, B5): `True`
        the first time an `event_id` is seen, `False` on any replay within
        the bounded FIFO window."""
        if event_id in self._seen_event_id_set:
            return False
        self._seen_event_ids.append(event_id)
        self._seen_event_id_set.add(event_id)
        while len(self._seen_event_ids) > _EVENT_ID_BOUND:
            evicted = self._seen_event_ids.popleft()
            self._seen_event_id_set.discard(evicted)
        return True
