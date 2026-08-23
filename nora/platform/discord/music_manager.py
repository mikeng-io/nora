"""`MusicPlayerManager` — per-guild lookup, join-or-reject, and idle/empty
-channel disconnect (design/discord-music/03-state-and-persistence.md).

This is the ACTUAL entry point the slash-command layer calls — `MusicPlayer`
is an internal implementation detail this class constructs and owns one of
per guild. `connect` (a zero-arg async factory returning a `VoiceSession`)
is how the caller hands over "I already joined the requester's channel" —
`None` means the requester is not in any voice channel, the rejection this
class enforces BEFORE `MusicPlayer` or a `VoiceSession` ever exists for a
guild that doesn't have one yet.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
from typing import Any

from nora.domain.music import MusicQueueStore, QueuedTrack
from nora.domain.ports import TrackSource
from nora.platform.discord.music_player import MusicPlayer, PlayerStatus, PlayOutcome, VoiceSession

log = logging.getLogger("nora.music")

Connect = Callable[[], Awaitable[VoiceSession]]
Spawn = Callable[[Coroutine[Any, Any, None]], "asyncio.Task[None]"]
ClockSleep = Callable[[float], Awaitable[None]]


class MusicPlayerManager:
    def __init__(
        self,
        *,
        queue_store: MusicQueueStore,
        track_source: TrackSource,
        idle_timeout_seconds: float,
        spawn: Spawn,
        clock_sleep: ClockSleep = asyncio.sleep,
    ) -> None:
        self._queue_store = queue_store
        self._track_source = track_source
        self._idle_timeout_seconds = idle_timeout_seconds
        self._spawn = spawn
        self._clock_sleep = clock_sleep
        self._players: dict[str, MusicPlayer] = {}
        self._idle_tasks: dict[str, asyncio.Task[None]] = {}

    async def play(
        self,
        *,
        guild_id: str,
        query_or_url: str,
        requested_by: str,
        added_at: datetime,
        connect: Connect | None,
    ) -> PlayOutcome | None:
        """`None` is the rejection: no existing player for `guild_id` AND
        `connect is None` (the requester is not in any voice channel).
        Reuses an existing player regardless of `connect` — only STARTING a
        session requires the requester to be present
        (03-state-and-persistence.md's own rule)."""
        player = await self._get_or_create(guild_id, connect)
        if player is None:
            return None
        outcome = await player.play(
            query_or_url=query_or_url, requested_by=requested_by, added_at=added_at,
        )
        await self._after_transition(guild_id, player)
        return outcome

    async def skip(self, guild_id: str) -> QueuedTrack | None:
        player = self._players.get(guild_id)
        if player is None:
            return None
        skipped = await player.skip()
        await self._after_transition(guild_id, player)
        return skipped

    async def on_track_finished(self, guild_id: str) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        await player.on_track_finished()
        await self._after_transition(guild_id, player)

    async def pause(self, guild_id: str) -> bool:
        player = self._players.get(guild_id)
        return False if player is None else player.pause()

    async def resume(self, guild_id: str) -> bool:
        player = self._players.get(guild_id)
        return False if player is None else player.resume()

    async def status(self, guild_id: str) -> PlayerStatus | None:
        player = self._players.get(guild_id)
        return None if player is None else await player.status()

    async def stop(self, guild_id: str) -> None:
        """`/music stop` — same shape as leaving, an explicit command
        rather than a timer/event triggering it."""
        await self._leave(guild_id)

    async def on_voice_channel_emptied(self, guild_id: str) -> None:
        """The channel's human count dropped to zero — leaves immediately,
        no idle grace period (03's disconnect-timer rules, trigger 1)."""
        await self._leave(guild_id)

    async def _get_or_create(self, guild_id: str, connect: Connect | None) -> MusicPlayer | None:
        existing = self._players.get(guild_id)
        if existing is not None:
            return existing
        if connect is None:
            return None
        voice = await connect()
        player = MusicPlayer(
            guild_id=guild_id, queue=self._queue_store, source=self._track_source, voice=voice,
        )
        self._players[guild_id] = player
        return player

    async def _after_transition(self, guild_id: str, player: MusicPlayer) -> None:
        status = await player.status()
        if status.now_playing is None:
            self._schedule_idle_disconnect(guild_id)
        else:
            self._cancel_idle_disconnect(guild_id)

    def _schedule_idle_disconnect(self, guild_id: str) -> None:
        """03's disconnect-timer rule 2: queue empty and nothing playing ->
        leave after `idle_timeout_seconds`. Cancels-and-replaces any timer
        already pending for this guild, so repeated empty transitions never
        stack more than one."""
        self._cancel_idle_disconnect(guild_id)
        self._idle_tasks[guild_id] = self._spawn(self._idle_disconnect_after_timeout(guild_id))

    def _cancel_idle_disconnect(self, guild_id: str) -> None:
        task = self._idle_tasks.pop(guild_id, None)
        if task is not None:
            task.cancel()

    async def _idle_disconnect_after_timeout(self, guild_id: str) -> None:
        await self._clock_sleep(self._idle_timeout_seconds)
        await self._leave(guild_id)

    async def _leave(self, guild_id: str) -> None:
        self._cancel_idle_disconnect(guild_id)
        player = self._players.pop(guild_id, None)
        if player is not None:
            await player.stop()
