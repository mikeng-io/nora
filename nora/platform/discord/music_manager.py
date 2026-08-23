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

from nora.domain.music import MusicQueueStore, MusicSessionStore, QueuedTrack
from nora.domain.ports import TrackSource
from nora.platform.discord.music_player import MusicPlayer, PlayerStatus, PlayOutcome, VoiceSession

log = logging.getLogger("nora.music")

Connect = Callable[[], Awaitable[VoiceSession]]
Spawn = Callable[[Coroutine[Any, Any, None]], "asyncio.Task[None]"]
ClockSleep = Callable[[float], Awaitable[None]]
# Posts a plain-text message to whatever channel a guild's session was
# STARTED from -- captured once, at the `/music play` that actually creates
# the `MusicPlayer` (never updated on a later reuse from a different
# channel, matching "the channel where it started").
Notify = Callable[[str], Awaitable[None]]


class MusicPlayerManager:
    def __init__(
        self,
        *,
        queue_store: MusicQueueStore,
        session_store: MusicSessionStore,
        track_source: TrackSource,
        idle_timeout_seconds: float,
        spawn: Spawn,
        clock_sleep: ClockSleep = asyncio.sleep,
    ) -> None:
        self._queue_store = queue_store
        self._session_store = session_store
        self._track_source = track_source
        self._idle_timeout_seconds = idle_timeout_seconds
        self._spawn = spawn
        self._clock_sleep = clock_sleep
        self._players: dict[str, MusicPlayer] = {}
        self._idle_tasks: dict[str, asyncio.Task[None]] = {}
        self._notify: dict[str, Notify] = {}
        # `play()`'s whole body runs under this — "no existing player, so
        # connect" is a check-then-act across an `await`, and enqueuing a
        # track is another; without a lock, two `/music play` calls for the
        # same guild can both pass the check and both connect (orphaning
        # the loser's voice connection), or both enqueue at once (subagent
        # review, 2026-08-23). One lock per guild, created on first use --
        # `setdefault` is a single synchronous dict operation, so creating
        # the LOCK itself cannot race the same way.
        self._guild_locks: dict[str, asyncio.Lock] = {}

    async def play(
        self,
        *,
        guild_id: str,
        query_or_url: str,
        requested_by: str,
        added_at: datetime,
        connect: Connect | None,
        notify: Notify | None = None,
        voice_channel_id: str | None = None,
        notify_channel_id: str | None = None,
    ) -> PlayOutcome | None:
        """`None` is the rejection: no existing player for `guild_id` AND
        `connect is None` (the requester is not in any voice channel).
        Reuses an existing player regardless of `connect` — only STARTING a
        session requires the requester to be present
        (03-state-and-persistence.md's own rule).

        `notify`/`voice_channel_id`/`notify_channel_id` are captured ONLY
        when this call actually creates the player (a fresh session) — a
        later `/music play` reusing an existing session does not move the
        now-playing notification channel, matching "the channel where it
        started" (design/discord-music/06-session-persistence.md).
        `voice_channel_id`/`notify_channel_id` are the raw Discord IDs, not
        derivable from `connect`/`notify` (opaque closures) -- persisted so
        a restart can reconnect to the right channels.

        The WHOLE method runs under this guild's lock, not just the
        get-or-create step — Nora is one process, so this is what keeps two
        `/music play` calls for the same guild from ever reaching
        `MusicQueueStore.enqueue` at the same time at all (that store's own
        UNIQUE-constraint-plus-retry is a second, independent line of
        defence for whatever this lock doesn't cover — a future multi-
        process deployment, say — not the mechanism this one relies on)."""
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            is_new_session = guild_id not in self._players
            player = await self._get_or_create(
                guild_id, connect, voice_channel_id, notify_channel_id,
            )
            if player is None:
                return None
            if is_new_session and notify is not None:
                self._notify[guild_id] = notify
            # Cancel BEFORE the slow resolve/enqueue await below, not only
            # in `_after_transition` at the end — an idle timer left
            # running across that await can fire and tear the player down
            # mid-`play()` (subagent review, 2026-08-23: reusing an idle
            # player raced its own idle-disconnect timer).
            self._cancel_idle_disconnect(guild_id)
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
        status = await self._after_transition(guild_id, player)
        # Skip's own reply already names what was skipped
        # (`music_commands.py`) -- this announces what's now playing next,
        # the same notification a natural track-end gets.
        await self._notify_now_playing(guild_id, status)
        return skipped

    async def on_track_finished(self, guild_id: str) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        await player.on_track_finished()
        status = await self._after_transition(guild_id, player)
        # This is the ONE transition with no slash-command reply of its own
        # — a track ending naturally and the next one auto-starting is
        # otherwise silent in Discord.
        await self._notify_now_playing(guild_id, status)

    async def pause(self, guild_id: str) -> bool:
        player = self._players.get(guild_id)
        return False if player is None else await player.pause()

    async def resume(self, guild_id: str) -> bool:
        player = self._players.get(guild_id)
        return False if player is None else await player.resume()

    async def set_volume(self, guild_id: str, volume: float) -> bool | None:
        """`None` — no player exists for this guild at all, distinct from
        `False` (a player exists but `volume` was out of range) — the same
        distinction `pause`/`resume`'s bare `False` doesn't need, because
        those have only one way to fail."""
        player = self._players.get(guild_id)
        return None if player is None else await player.set_volume(volume)

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

    async def _get_or_create(
        self, guild_id: str, connect: Connect | None,
        voice_channel_id: str | None, notify_channel_id: str | None,
    ) -> MusicPlayer | None:
        """Only ever called from `play()`, already holding this guild's
        lock (`asyncio.Lock` is not re-entrant, so this must NOT also
        acquire it) — that lock is what makes this method's own
        check-then-act ("no existing player, so connect") safe."""
        existing = self._players.get(guild_id)
        if existing is not None:
            return existing
        if connect is None or voice_channel_id is None or notify_channel_id is None:
            return None
        voice = await connect()
        player = MusicPlayer(
            guild_id=guild_id, queue=self._queue_store, source=self._track_source, voice=voice,
            session=self._session_store, voice_channel_id=voice_channel_id,
            notify_channel_id=notify_channel_id,
        )
        self._players[guild_id] = player
        return player

    async def _after_transition(self, guild_id: str, player: MusicPlayer) -> PlayerStatus:
        status = await player.status()
        if status.now_playing is None:
            self._schedule_idle_disconnect(guild_id)
        else:
            self._cancel_idle_disconnect(guild_id)
        return status

    async def _notify_now_playing(self, guild_id: str, status: PlayerStatus) -> None:
        now_playing = status.now_playing
        if now_playing is None:
            return
        notify = self._notify.get(guild_id)
        if notify is None:
            return
        await notify(f"Now playing: {now_playing.title} — {now_playing.url}")

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
        self._notify.pop(guild_id, None)
        player = self._players.pop(guild_id, None)
        if player is not None:
            await player.stop()
