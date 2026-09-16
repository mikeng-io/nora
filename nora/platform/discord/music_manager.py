"""`MusicPlayerManager` — per-guild lookup, join-or-reject, and the
idle/empty-channel teardown policies (design/discord-music/
03-state-and-persistence.md).

This is the ACTUAL entry point the slash-command layer calls — `MusicPlayer`
is an internal implementation detail this class constructs and owns one of
per guild. `connect` (a zero-arg async factory returning a `VoiceSession`)
is how the caller hands over "the requester's channel is joined" —
`None` means the requester is not in any voice channel, the rejection this
class enforces BEFORE `MusicPlayer` or a `VoiceSession` ever exists for a
guild that doesn't have one yet.

Lifecycle split (design/voice-channel/07 §10, B3): **end-music** (`/music
stop`, the idle-expiry timer) tears down the session but STAYS in the voice
channel — the audio-controller keeps the connection, e.g. for the wake
path. **Leave-voice** (`/discord exit` via `leave_voice`, an emptied
channel, a voice-death event, the dead-session eviction) additionally
destroys the connection. The old convergence of both onto one teardown is
deliberately gone.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from datetime import datetime
from typing import Any

from nora.domain.music import (
    AUTOPLAY_PERSON_ID,
    MusicQueueStore,
    MusicSession,
    MusicSessionStore,
    QueuedTrack,
)
from nora.domain.ports import TrackSource
from nora.platform.discord import music_messages as m
from nora.platform.discord.music_player import (
    MusicPlayer,
    PlayerStatus,
    PlayOutcome,
    VoiceSession,
    _PendingPlay,
)

log = logging.getLogger("nora.music")

Connect = Callable[[], Awaitable[VoiceSession]]
Spawn = Callable[[Coroutine[Any, Any, None]], "asyncio.Task[None]"]
ClockSleep = Callable[[float], Awaitable[None]]
# Posts a plain-text message to whatever channel a guild's session was
# STARTED from -- captured once, at the `/music play` that actually creates
# the `MusicPlayer` (never updated on a later reuse from a different
# channel, matching "the channel where it started").
Notify = Callable[[str], Awaitable[None]]
# Reads the playback id nora currently expects the controller to be playing
# for a guild (`VoiceControllerHub.expected_playback_id` in production) —
# the B5 staleness probe `on_track_finished` applies UNDER the guild lock.
PlaybackProbe = Callable[[str], str | None]
# Reads the generation nora currently believes is live for a guild
# (`VoiceControllerHub.generation_for` in production) — the B8 staleness
# probe the lifecycle events (`voice-death`, `channel-emptied`) apply UNDER
# the guild lock before they may tear anything down.
GenerationProbe = Callable[[str], str | None]
# Forgets a guild's cached voice session WITHOUT an exit call
# (`VoiceControllerHub.drop` in production) — for events where the
# connection is already gone (voice-death) or is being exited by the
# player's own `leave` (channel-emptied). Runs INSIDE the guild lock (B8).
DropVoiceSession = Callable[[str], None]
# Exits a guild's voice connection through the hub
# (`VoiceControllerHub.exit` in production) — the channel-emptied policy
# for a session that has NO music player (voice-only: leaving IS exiting).
ExitVoiceSession = Callable[[str], Awaitable[bool]]


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
        autoplay_enabled: bool = False,
        autoplay_prewarm_seconds: float = 0.0,
        playback_probe: PlaybackProbe | None = None,
        generation_probe: GenerationProbe | None = None,
        drop_voice_session: DropVoiceSession | None = None,
        exit_voice_session: ExitVoiceSession | None = None,
    ) -> None:
        self._queue_store = queue_store
        self._session_store = session_store
        self._track_source = track_source
        self._idle_timeout_seconds = idle_timeout_seconds
        self._spawn = spawn
        self._clock_sleep = clock_sleep
        self._autoplay_enabled = autoplay_enabled
        self._autoplay_prewarm_seconds = autoplay_prewarm_seconds
        self._playback_probe = playback_probe
        self._generation_probe = generation_probe
        self._drop_voice_session = drop_voice_session
        self._exit_voice_session = exit_voice_session
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

        The WHOLE method's DECISION half runs under this guild's lock, not
        just the get-or-create step — Nora is one process, so this is what
        keeps two `/music play` calls for the same guild from ever reaching
        `MusicQueueStore.enqueue` at the same time at all (that store's own
        UNIQUE-constraint-plus-retry is a second, independent line of
        defence for whatever this lock doesn't cover — a future multi-
        process deployment, say — not the mechanism this one relies on).
        The lock is RELEASED for the outbound play POST (B14 — see
        `_commit_and_finish`): a hanging audio-controller must not block
        the guild's other commands."""
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
            queued, pending, was_idle = await player.play_prepare(
                query_or_url=query_or_url, requested_by=requested_by, added_at=added_at,
            )
        await self._commit_and_finish(guild_id, player, pending)
        return player.play_outcome(queued=queued, was_idle=was_idle)

    async def skip(self, guild_id: str) -> QueuedTrack | None:
        # Same per-guild lock as `play()`/`on_track_finished` — a skip can
        # race a natural track-end's `_advance` (or the monitor's
        # `on_voice_death`): without serialization a skip could advance a
        # player the finish path is mid-`pop_next` on, or hold a stale
        # reference to a player a teardown already removed (external audit
        # Codex MAJOR-2, 2026-08-26). Released around the outbound POST
        # (B14) like every transition.
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            player = self._players.get(guild_id)
            if player is None:
                return None
            skipped, pending, stop_audio = await player.skip_prepare()
        if stop_audio:
            # The queue ran out behind the skipped track — nothing
            # supersedes its still-live audio, so the controller is told
            # to go silent. Outside the lock, same B14 reason as the play
            # POST. (A play racing this stop can still land after it; the
            # controller serializes the two and whichever arrives last
            # wins — a momentary edge the next command resolves.)
            await player.stop_audio()
        status = await self._commit_and_finish(guild_id, player, pending)
        # Skip's own reply already names what was skipped
        # (`music_commands.py`) -- this announces what's now playing next,
        # the same notification a natural track-end gets.
        await self._notify_now_playing(guild_id, status)
        return skipped

    async def on_track_finished(self, guild_id: str, *, playback_id: str | None = None) -> bool:
        """Advance after a track end. Returns `True` when the queue actually
        advanced — `False` when the event was stale or there was no player.

        `playback_id` is the controller's (design/voice-channel/07 §3.3, B5):
        the voice-event consumer passes it from the callback, and the match
        against the currently-expected playback is checked UNDER the guild
        lock — never before taking it. That ordering is the whole point: a
        `track-finished` that arrives while a skip holds the lock mid-`play()`
        would pass an outside check against the OLD expected id and then
        advance a second time once the lock released; checked here, the
        skip's own advance has already moved the expected id and the event
        is recognised as the tail of the superseded track. A `None`
        `playback_probe` (tests, or a wiring that cannot answer) skips the
        check rather than guessing."""
        # Under the same per-guild lock as `play()` — a voice-death event
        # can fire while a natural track-end is mid-`_advance`; without
        # serialization the two transitions would interleave
        # `stop()`/`queue.clear()`/`session.delete()` with
        # `pop_next()`/`resolve()`/`voice.play()` on the same `MusicPlayer`
        # (external audit MAJOR-3, 2026-08-26). The lock is RELEASED for
        # the advance's outbound play POST (B14 — `_commit_and_finish`).
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            if (
                playback_id is not None
                and self._playback_probe is not None
                and playback_id != self._playback_probe(guild_id)
            ):
                log.info(
                    "track-finished for guild %s ignored: playback %s is not the "
                    "currently-expected one (a superseded track's tail)",
                    guild_id, playback_id,
                )
                return False
            player = self._players.get(guild_id)
            if player is None:
                return False
            pending = await player.prepare_advance()
        status = await self._commit_and_finish(guild_id, player, pending)
        # This is the ONE transition with no slash-command reply of its own
        # — a track ending naturally and the next one auto-starting is
        # otherwise silent in Discord.
        await self._notify_now_playing(guild_id, status)
        return True

    async def _commit_and_finish(
        self, guild_id: str, player: MusicPlayer, pending: _PendingPlay | None,
    ) -> PlayerStatus:
        """B14 (design/voice-channel/07, 2026-09-16 audit): the outbound
        play POST runs with the guild lock RELEASED — a hung audio-
        controller must not block `/music skip`, `/music stop` or
        `/discord exit` for the guild (the advance used to hold the lock
        across the whole 30s-timeout HTTP round-trip). The finish step
        then RE-ACQUIRES the lock and verifies the session is still on the
        track the POST started — a stop or skip may have landed meanwhile
        — before persisting; `MusicPlayer.finish_pending` owns that
        re-check, including killing the just-started audio when a stop
        left nothing to supersede it."""
        ok = True
        if pending is not None:
            ok = await player.commit_pending(pending)
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            if pending is not None:
                # Runs even when the player was popped mid-flight (a stop
                # landed while the POST was out): the zombie-audio guard
                # inside must still fire.
                await player.finish_pending(pending, ok=ok)
            if self._players.get(guild_id) is player:
                return await self._after_transition(guild_id, player)
            # Torn down while the POST was in flight — nothing left to
            # schedule or evict.
            return await player.status()

    async def pause(self, guild_id: str) -> bool:
        # Same per-guild lock as `play()`/`stop()` (B6, 2026-09-16 audit):
        # pause's HTTP response used to persist the session AFTER a
        # concurrent stop had deleted it, resurrecting the row. Under the
        # lock the two are serialized in arrival order — the pause's
        # persist lands BEFORE the stop's delete (or the stop runs first
        # and the pause finds no player), never the reverse. The player's
        # own post-await re-check covers any caller that bypasses the lock.
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            player = self._players.get(guild_id)
            return False if player is None else await player.pause()

    async def resume(self, guild_id: str) -> bool:
        # Same per-guild lock as `pause` (B6).
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            player = self._players.get(guild_id)
            return False if player is None else await player.resume()

    async def top(self, guild_id: str, position: int) -> QueuedTrack | None:
        """`None` covers both "no player for this guild" and "`position` is
        out of range" — same single-outcome shape `pause`/`resume` already
        use for their one way to fail; a caller wanting the distinction has
        `status()` to check first."""
        player = self._players.get(guild_id)
        return None if player is None else await player.top(position)

    async def set_volume(self, guild_id: str, volume: float) -> bool | None:
        """`None` — no player exists for this guild at all, distinct from
        `False` (a player exists but `volume` was out of range) — the same
        distinction `pause`/`resume`'s bare `False` doesn't need, because
        those have only one way to fail."""
        # Same per-guild lock as `pause` (B6): the volume response must not
        # persist over a concurrent stop's delete.
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            player = self._players.get(guild_id)
            return None if player is None else await player.set_volume(volume)

    async def status(self, guild_id: str) -> PlayerStatus | None:
        player = self._players.get(guild_id)
        return None if player is None else await player.status()

    async def stop(self, guild_id: str) -> None:
        """`/music stop` — END-MUSIC (design/voice-channel/07 §10, B3): the
        session ends but the voice connection STAYS (the controller keeps it,
        e.g. for the wake path). Same shape as leaving, an explicit command
        rather than a timer/event triggering it."""
        # Same per-guild lock as `play()` — a stop can race a natural
        # track-end's `_advance` or the monitor's `on_voice_death`; without
        # serialization a post-stop advance could resurrect persisted state
        # or double-teardown (external audit Codex MAJOR-2, 2026-08-26).
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            await self._end_music(guild_id)

    async def leave_voice(self, guild_id: str) -> None:
        """`/discord exit` — LEAVE-VOICE (end-music AND destroy the
        connection), with no announcement: the command replies itself."""
        # Same per-guild lock as `play()`/`stop()`.
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            await self._leave(guild_id)

    async def on_voice_channel_emptied(
        self, guild_id: str, *,
        generation: str | None = None, channel_id: str | None = None,
    ) -> None:
        """The channel's human count dropped to zero — LEAVE-VOICE
        immediately, no idle grace period (03's disconnect-timer rules,
        trigger 1). Fires from the controller's `channel-emptied` event
        (design/voice-channel/07 §10, B10).

        B8: the event's `generation`/`channel_id` are validated UNDER the
        guild lock before anything is torn down — an outbox delivery
        delayed across an exit/rejoin must not drop the NEW session. A
        session with NO music player (a voice-only join) still leaves: the
        hub exits the connection directly, since there is no player whose
        `leave` would do it."""
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            if not self._generation_matches(guild_id, generation):
                log.info(
                    "channel-emptied for guild %s ignored: generation %r is not "
                    "the live one (a delayed event from before an exit/rejoin)",
                    guild_id, generation,
                )
                return
            player = self._players.get(guild_id)
            if (
                channel_id is not None and player is not None
                and player.voice_channel_id != channel_id
            ):
                log.info(
                    "channel-emptied for guild %s ignored: channel %r is not the "
                    "session's channel %r",
                    guild_id, channel_id, player.voice_channel_id,
                )
                return
            if player is None and self._exit_voice_session is not None:
                # Voice-only session: leaving IS exiting the connection.
                await self._exit_voice_session(guild_id)
                return
            if self._drop_voice_session is not None:
                # The player's `leave` below exits the connection itself;
                # the hub just forgets the cached session so nothing reuses
                # a dead generation.
                self._drop_voice_session(guild_id)
            await self._leave(guild_id)

    async def on_voice_death(self, guild_id: str, *, generation: str | None = None) -> None:
        """The voice connection died while a track was supposedly audible —
        a silent voice-websocket drop that discord.py's `is_connected()`
        never reflects (R-66, found live 2026-08-26: "now its NOT even
        playing" after a `discord gateway resumed`, while every subsequent
        `voice.play()` raised `Not connected to voice.`). The player still
        believes it is playing; end the session like a stop so a fresh
        `/music play` joins a clean connection instead of streaming into a
        dead socket. Announces the teardown — this is the one transition
        with no command reply of its own, and the room used to have no idea
        (found live 2026-09-15).

        B8: the event's `generation` is compared against the live one UNDER
        the guild lock, and the hub's cached session is dropped inside the
        same lock — a delayed death from an OLD generation (the outbox
        retried across an exit/rejoin) must not tear down the NEW session.
        The teardown itself tolerates the dead connection (B7):
        `HttpVoiceSession.stop`/`disconnect` treat a 409
        `generation_conflict` as the end state already reached, so local
        cleanup completes without the controller answering."""
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            if not self._generation_matches(guild_id, generation):
                log.info(
                    "voice-death for guild %s ignored: generation %r is not the "
                    "live one (a delayed event from before an exit/rejoin)",
                    guild_id, generation,
                )
                return
            if self._drop_voice_session is not None:
                # The connection is already gone controller-side — forget
                # the session WITHOUT an exit call, INSIDE the same lock as
                # the teardown (B8: outside the lock, a delayed event could
                # drop the NEW session).
                self._drop_voice_session(guild_id)
            await self._leave(guild_id, announce=True)

    def _generation_matches(self, guild_id: str, generation: str | None) -> bool:
        """The B8 staleness probe, with the B5 probe's fail-open-if-
        unanswerable discipline: a wiring that cannot answer (tests, a
        partial composition) or an event carrying no generation is never
        GUESSED stale. No live session on record is likewise not provable
        stale — the teardown it guards is idempotent anyway."""
        if generation is None or self._generation_probe is None:
            return True
        current = self._generation_probe(guild_id)
        if current is None:
            return True
        return generation == current

    async def persisted_sessions(self) -> Sequence[MusicSession]:
        """Every session a PRIOR process left behind — the boot-time
        resume scan's input (design/discord-music/06-session-persistence
        .md stage 2). The Discord platform layer owns deciding whether
        each one is actually resumable (channel still exists, still has
        humans in it); this just hands back what's on record."""
        return await self._session_store.all()

    async def discard_persisted_session(self, guild_id: str) -> None:
        """The boot-time scan's "not resumable" outcome (channel gone, or
        emptied while Nora was down) — deletes the stale row without ever
        constructing a `MusicPlayer` or opening a voice connection for it.

        Clears the guild's queue too, not just the session row: those rows
        belonged to the session being discarded, and leaving them behind
        orphans them in `music_queue` forever — silently jumping the line
        ahead of whatever a future, unrelated `play()` call actually asks
        for once someone starts a fresh session in this guild. This is the
        same cleanup `MusicPlayer.stop()` already does for an explicit
        `/music stop`; the boot-time "this session is dead" path needs the
        same symmetry (found live 2026-08-24 — a K-pop batch orphaned by an
        earlier un-resumed session silently played instead of a fresh
        "play apt" request, while the reply claimed APT was playing)."""
        await self._queue_store.clear(guild_id)
        await self._session_store.delete(guild_id)

    async def resume_persisted_session(
        self, session: MusicSession, *, connect: Connect, notify: Notify,
    ) -> None:
        """Boot-time resume ONLY — never called from a slash command.
        Builds a fresh `MusicPlayer` directly from a persisted row rather
        than through `play()`'s query-resolving flow: there is no new
        query here, just an already-known track to restart from position
        0 (`MusicPlayer.resume_from_persisted`'s own limitation).

        The WHOLE reconciliation runs under the guild lock (B5, 2026-09-16
        audit): a `track-finished` for the pre-restart playback must wait
        for the resume to settle, after which the staleness probe
        recognises it as the old track's tail. Without the lock the event
        advanced the queue MID-resume and the resume then re-played the
        persisted track OVER the advanced one — the advanced track was
        already popped, so it was lost outright (Codex probe).

        §12.1 live reconcile comes first: when the adopted connection is
        ACTUALLY playing (the controller outlived the restart), the
        persisted track is recorded as now-playing WITHOUT a play call —
        replaying it would cut live audio, and the controller's own
        `track-finished` for it drives the advance from the real queue."""
        lock = self._guild_locks.setdefault(session.guild_id, asyncio.Lock())
        async with lock:
            if session.guild_id in self._players:
                return  # already has a live player; do not clobber it
            voice = await connect()
            player = MusicPlayer(
                guild_id=session.guild_id, queue=self._queue_store, source=self._track_source,
                voice=voice, session=self._session_store,
                voice_channel_id=session.voice_channel_id,
                notify_channel_id=session.notify_channel_id,
                autoplay_enabled=self._autoplay_enabled,
                autoplay_prewarm_seconds=self._autoplay_prewarm_seconds,
                loop=asyncio.get_running_loop(),
            )
            self._players[session.guild_id] = player
            self._notify[session.guild_id] = notify
            live_playback = getattr(voice, "playback_id", None)
            if isinstance(live_playback, str) and live_playback:
                log.info(
                    "boot resume for guild %s: the controller is already playing "
                    "(playback %s) — adopting the persisted track without replaying",
                    session.guild_id, live_playback,
                )
                if (
                    session.now_playing_source is not None
                    and session.now_playing_locator is not None
                ):
                    player.adopt_persisted_now_playing(
                        source=session.now_playing_source,
                        locator=session.now_playing_locator,
                        title=session.now_playing_title or "",
                    )
            else:
                resumed = False
                if (
                    session.now_playing_source is not None
                    and session.now_playing_locator is not None
                ):
                    resumed = await player.resume_from_persisted(
                        source=session.now_playing_source, locator=session.now_playing_locator,
                        title=session.now_playing_title or "", paused=session.paused,
                        volume=session.volume,
                    )
                if not resumed:
                    # Either nothing was playing when this was persisted, or the
                    # persisted track no longer resolves -- either way, fall
                    # through to whatever the ACTUAL queue has next (or idle).
                    #
                    # R-88: if nothing is playing but a last-played track is on
                    # record (autoplay's chain had already ended before the
                    # restart), restore it as the re-seed seed FIRST -- the
                    # `_advance` below then seeds the autoplay chain from it
                    # instead of sitting silent in a live voice channel.
                    if (
                        session.last_played_source is not None
                        and session.last_played_locator is not None
                    ):
                        player.seed_last_played(
                            source=session.last_played_source,
                            locator=session.last_played_locator,
                            title=session.last_played_title or "",
                        )
                    await player.on_track_finished()
            status = await self._after_transition(session.guild_id, player)
        await self._notify_now_playing(session.guild_id, status)

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
            notify_channel_id=notify_channel_id, autoplay_enabled=self._autoplay_enabled,
            autoplay_prewarm_seconds=self._autoplay_prewarm_seconds,
            loop=asyncio.get_running_loop(),
        )
        self._players[guild_id] = player
        return player

    async def _after_transition(self, guild_id: str, player: MusicPlayer) -> PlayerStatus:
        status = await player.status()
        if getattr(player, "_session_dead", False):
            # R-65: `_advance` already tore the session down (queue cleared,
            # session row deleted, voice disconnected) but cannot remove
            # itself from `_players` — drop the dead player entirely so the
            # next `/music play` joins a clean connection and the idle timer
            # is not armed for a zombie (external audit MAJOR-2, 2026-08-26).
            # `_leave` re-runs the leave-voice path, which is safe on the
            # torn state (`HttpVoiceSession.disconnect` tolerates an
            # already-invalid generation; clear/stop on empty are no-ops).
            await self._leave(guild_id)
            return status
        if status.now_playing is None:
            self._schedule_idle_disconnect(guild_id)
        else:
            self._cancel_idle_disconnect(guild_id)
        return status

    async def _notify_now_playing(self, guild_id: str, status: PlayerStatus) -> None:
        now_playing = status.now_playing
        if now_playing is None:
            log.info(
                "notify_now_playing: nothing playing for guild %s — no notification",
                guild_id,
            )
            return
        notify = self._notify.get(guild_id)
        if notify is None:
            log.warning(
                "notify_now_playing: no notify closure registered for guild %s — "
                "auto-advance notification will NOT be sent (track: %s)",
                guild_id, now_playing.title,
            )
            return
        log.info(
            "notify_now_playing: sending notification for guild %s (track: %s)",
            guild_id, now_playing.title,
        )
        # Tells apart a track someone actually queued from one autoplay
        # picked (`AUTOPLAY_PERSON_ID`, design/discord-music/08-autoplay.md)
        # -- otherwise a listener has no way to tell why a track nobody
        # requested just started. Wording comes from the canonical message
        # table (`music_messages.py`) — this module never formats its own.
        is_autoplay = now_playing.added_by_person_id == AUTOPLAY_PERSON_ID
        line = (
            m.autoplay_now_playing_line(now_playing)
            if is_autoplay
            else m.now_playing_line(now_playing)
        )
        await notify(line)

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
        # Same per-guild lock (Codex MAJOR-2, 2026-08-26): the idle timer can
        # fire while a track-end `_advance` or a `/music play` is mid-flight.
        lock = self._guild_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            # Idle expiry is END-MUSIC (design/voice-channel/07 §10, B3) —
            # the session ends but the connection STAYS; the controller
            # keeps it, e.g. for the wake path.
            await self._end_music(guild_id)

    async def _end_music(self, guild_id: str) -> None:
        """END-MUSIC: tear the session down (queue, persistence, playback)
        and STAY connected — `/music stop` and the idle-expiry timer."""
        self._cancel_idle_disconnect(guild_id)
        self._notify.pop(guild_id, None)
        player = self._players.pop(guild_id, None)
        if player is not None:
            await player.stop()

    async def _leave(self, guild_id: str, *, announce: bool = False) -> None:
        """LEAVE-VOICE: end-music AND destroy the voice connection — an
        emptied channel, a voice-death event, `/discord exit`, the
        dead-session eviction. `MusicPlayer.leave` tolerates a connection
        that is already gone controller-side."""
        self._cancel_idle_disconnect(guild_id)
        notify = self._notify.pop(guild_id, None)
        player = self._players.pop(guild_id, None)
        if player is not None:
            await player.leave()
        if announce and notify is not None and player is not None:
            # Announce the teardown ONLY on the one transition with no
            # command reply of its own — `on_voice_death`. `/music stop`
            # already replies `Stopped.`, an idle timeout is expected, and
            # an emptied channel has nobody left to read it (found live
            # 2026-09-15: voice death tore the session down with zero
            # notification — listeners only noticed the silence).
            # `_leave` is idempotent and can be re-run on an already-torn
            # guild; a second call has no player and must not double-post.
            # `silent=True` matches every other music notify.
            try:
                await notify(m.connection_lost_line())
            except Exception:
                log.warning(
                    "could not announce music teardown for guild %s", guild_id,
                    exc_info=True,
                )
