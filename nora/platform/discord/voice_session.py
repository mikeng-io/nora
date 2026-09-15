"""`DiscordVoiceSession` — the `VoiceSession` port's real `discord.py`
implementation (design/discord-music/01-playback-and-source.md,
02-control-surface.md).

`discord.py`'s own source (2.7.1, `discord.player.AudioPlayer.run`'s
`finally: self._call_after()`) fires the `after` callback on ANY exit from
the audio thread's loop — natural completion, an error, OR an explicit
`VoiceClient.stop()`. `MusicPlayer.skip()`/`stop()` already drive that
transition explicitly and synchronously, so this class must SWALLOW the
`after` firing its own `stop()` call causes, or a skip advances the queue
twice (once from `MusicPlayer.skip()`'s own `_advance()`, again from the
callback it did not expect to see). **`_suppress_next_after` has two defects
of its own — a flag stranded when the track had already ended, and a real
`after` that fires asynchronously rather than during `stop()`. Issue #192;
deliberately untouched here.**

**A second, subtler race** (subagent review, 2026-08-23): a track ending
naturally schedules `on_finished()` via `run_coroutine_threadsafe` — QUEUED
on the loop, not run yet. If `MusicPlayer.skip()` reaches `voice.play()` (a
non-empty queue) or `voice.stop()` (an empty one) before that queued
coroutine gets a turn, the suppression flag above does NOT help: it only
guards the NEXT `after` invocation `stop()` itself causes, not an EARLIER one
already scheduled. Both the already-queued `on_finished()` and `skip()`'s own
explicit advance then run, advancing the queue twice for one skip.

**Cancelling the `Future` does not prevent that, and the fix that shipped in
2026-08 could not do what its comment claimed** (issue #179, measured
2026-09-11). `asyncio.run_coroutine_threadsafe` returns a
`concurrent.futures.Future` that is only CHAINED to the real `asyncio.Task`
on a later loop iteration; cancelling it before then makes
`asyncio.futures._chain_future` schedule `task.cancel()` with
`call_soon_threadsafe` — which lands in the ready queue AFTER the
`call_soon(task.__step)` the very same iteration queued. The task therefore
takes its first step regardless. Measured directly: `fut.cancel()` returns
`True`, `fut.cancelled()` is `True`, and the coroutine runs.

So supersession is decided ON THE LOOP instead, by an EPOCH: every call that
takes over the advance (`play()`, `stop()`) bumps `_finish_epoch`, each
track's completions are minted under the `_play_epoch` stamped when that
track started, and `_run_on_finished` compares the two before touching
`on_finished`. The comparison runs in the coroutine itself, before it awaits
anything, so no scheduling order can put a stale completion past it.

**What `Future.cancel()` is still for, and why it stays.** It is the only
thing that can stop a completion that has already STARTED and is suspended at
an `await` — which is exactly what happens when the completion reaches
`MusicPlayerManager.on_track_finished`'s per-guild lock while `skip()` is
holding it. The epoch covers the not-yet-started case; the cancel covers the
started-and-waiting case. Neither covers the other, and both are needed.

**HOW FAR THIS ACTUALLY REACHES — measured, because the honest answer is
narrower than the obvious one.** `MusicPlayerManager` serializes `skip()` and
`on_track_finished()` under a per-guild `asyncio.Lock` (`music_manager.py`),
and in production `MusicPlayer._advance` suspends almost immediately at
`pop_next` (a Postgres roundtrip) and `resolve` (a network call). Driven end
to end in THAT configuration, the pre-fix code and this one behave
identically: the stale completion starts, blocks on the lock, and the
deferred `task.cancel()` kills it before it can do anything. **No
user-visible double advance was demonstrated against production's
configuration, and this fix is not reported as one.** Remove the suspension —
hand `MusicPlayer` stores that return without yielding, the lock still in
place — and the pre-fix code advances two tracks for one skip while this one
advances one. So the defect is real at this class's own boundary and is
currently MASKED, by a lock in another module plus a cancellation that
happens to arrive in time. That is a guarantee by coincidence; this makes the
class provide the one its docstring claims, on its own.

`after` fires from the audio player's OWN THREAD, never the event loop's —
`asyncio.run_coroutine_threadsafe` is the only safe way back onto it from
there.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any

import discord
from discord.utils import MISSING

from nora.platform.discord.crossfade import CrossfadeSource

log = logging.getLogger("nora.music")

AudioSourceFactory = Callable[[str], discord.AudioSource]


def _default_audio_source(stream_url: str) -> discord.AudioSource:
    return discord.FFmpegPCMAudio(
        stream_url, before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    )


def _voice_connection_ws(voice_client: discord.VoiceClient) -> Any:
    """The underlying `DiscordVoiceWebSocket` of a `VoiceClient`'s
    `VoiceConnectionState` (`discord.voice_state.VoiceConnectionState.ws`),
    or `MISSING` if this discord.py shape ever changes. Probed read-only —
    the VOICE websocket is a different socket from the gateway websocket
    the listener's `connected` flag tracks, and Discord can drop it
    silently (found live 2026-08-26: a `discord gateway resumed` left the
    voice side dead while every is_connected() kept reporting True)."""
    state = getattr(voice_client, "_connection", None)
    if state is None:
        return MISSING
    return getattr(state, "ws", MISSING)


class DiscordVoiceSession:
    """Satisfies `nora.platform.discord.music_player.VoiceSession`."""

    def __init__(
        self,
        voice_client: discord.VoiceClient,
        *,
        loop: asyncio.AbstractEventLoop,
        on_finished: Callable[[], Coroutine[Any, Any, None]],
        on_voice_death: Callable[[], Coroutine[Any, Any, None]] | None = None,
        audio_source_factory: AudioSourceFactory = _default_audio_source,
        crossfade_seconds: float = 0.0,
        monitor_interval_seconds: float = 5.0,
        reconnect_channel: discord.VoiceChannel | None = None,
        reconnect_attempts: int = 2,
        reconnect_delay_seconds: float = 1.0,
    ) -> None:
        self._voice_client = voice_client
        self._loop = loop
        self._on_finished = on_finished
        self._on_voice_death = on_voice_death
        self._audio_source_factory = audio_source_factory
        self._crossfade_seconds = crossfade_seconds
        self._monitor_interval_seconds = monitor_interval_seconds
        self._monitor_task: asyncio.Task[None] | None = None
        self._suppress_next_after = False
        self._pending_finish: Future[None] | None = None
        # Monotonic supersession token for queued completions (issue #179).
        # Bumped by every call that TAKES OVER the advance; a completion
        # minted under an older epoch is stale by definition and returns
        # without calling `on_finished`.
        self._finish_epoch = 0
        # The epoch the CURRENT track was started under, stamped by `play()`
        # and read (never written) by `_after` on the audio thread. `_after`
        # must NOT read `_finish_epoch` directly: it would pick up a bump
        # that a `stop()` landed between its own suppression check and that
        # read, and then match. Stamping it here binds the token to the
        # track rather than to the instant the callback happens to fire.
        self._play_epoch = 0
        # Persisted across tracks, not reset on `play()` — setting the
        # volume once should stay in effect for whatever plays next, the
        # same "a setting, not an action on this one track" argument
        # `MusicPlayer.set_volume`'s docstring makes.
        self._volume: float = 1.0
        self._current_source: discord.PCMVolumeTransformer[discord.AudioSource] | None = None
        # The RAW (pre-volume-wrapped) source currently playing -- what a
        # later `play()` call mixes AGAINST if a crossfade starts before
        # this one's own natural end (`crossfade.py`).
        self._current_raw: discord.AudioSource | None = None
        self._crossfade_timer: asyncio.TimerHandle | None = None
        self._dead_consecutive = 0
        # True while the session is intentionally paused. `pause()` clears
        # discord.py's `AudioPlayer._resumed`, so `is_playing()` returns
        # False for the whole pause — the death monitor would otherwise
        # mistake that intentional silence for a dead voice connection and
        # tear the session down ~2 ticks later (found live 2026-09-15:
        # pause clicked at 13:30:21, monitor declared the connection dead
        # at 13:30:30, reconnect failed with "Already connected to a voice
        # channel.", and the session was torn down with zero notification).
        self._paused = False
        # Voice reconnect (2026-09-03): when the monitor observes the voice
        # connection die, try to rejoin the channel with a fresh connection
        # before tearing the session down. `None` disables reconnect (the
        # pre-existing behaviour: signal death immediately).
        self._reconnect_channel = reconnect_channel
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_delay_seconds = reconnect_delay_seconds
        # The stream URL/duration of the CURRENT track -- remembered so a
        # reconnect can restart the same track on the fresh connection.
        self._current_stream_url: str | None = None
        self._current_duration_seconds: float | None = None

    def is_playing(self) -> bool:
        return bool(self._voice_client.is_playing())

    def play(self, stream_url: str, *, duration_seconds: float | None = None) -> None:
        # Remember the CURRENT track so a voice reconnect can restart it on
        # the fresh connection (2026-09-03). Recorded BEFORE the factory
        # call below -- if the factory itself raises (ffmpeg missing, a
        # transient spawn failure), the previous track's URL is still the
        # right thing to reconnect with.
        self._current_stream_url = stream_url
        self._current_duration_seconds = duration_seconds
        raw = self._audio_source_factory(stream_url)
        mixed: discord.AudioSource = raw
        # `is_playing()` reflects the `AudioPlayer` THREAD's own state, not
        # the voice connection -- after a voice websocket drop whose
        # reconnect stalls or fails, that thread's last-known state can
        # stay "playing" indefinitely with no live connection behind it
        # (found live 2026-08-24: "the music is NOT playing again after it
        # reply start play" -- a live `.source` swap onto a dead connection
        # silently produced no audio while the tool call reported success).
        # Gating on `is_connected()` too means a stale "playing" flag with
        # no real connection falls through to `voice_client.play()` below,
        # which -- like real discord.py -- raises rather than doing nothing,
        # so the failure is visible to the caller instead of masquerading
        # as success.
        already_playing = self._voice_client.is_playing() and self._voice_client.is_connected()
        current_raw = self._current_raw
        # Captured BEFORE `self._current_source` is reassigned below, and
        # handed to `CrossfadeSource` as `keep_alive` -- see that class's
        # docstring for why this isn't optional: dropping the old
        # `PCMVolumeTransformer`'s last reference here, without this,
        # triggers CPython's refcounting `__del__` -> `cleanup()` ->
        # `current_raw.cleanup()` cascade IMMEDIATELY, poisoning the
        # crossfade's own `old` source before it ever reads a single
        # frame from it (verified live, 2026-08-24).
        old_wrapper = self._current_source
        log.info(
            "voice play() called: already_playing=%s crossfade_seconds=%s "
            "has_current_raw=%s",
            already_playing, self._crossfade_seconds, current_raw is not None,
        )
        if already_playing and self._crossfade_seconds > 0 and current_raw is not None:
            mixed = CrossfadeSource(
                current_raw, raw, duration_seconds=self._crossfade_seconds,
                keep_alive=old_wrapper,
            )
        self._current_raw = raw
        wrapped = discord.PCMVolumeTransformer(mixed, volume=self._volume)
        self._current_source = wrapped
        self._cancel_crossfade_timer()
        self._ensure_monitor()
        # A completion signal for the track THIS call is replacing may
        # already be in flight -- e.g. `/music skip` landing right as this
        # track's own early crossfade timer fired, or right after the track
        # ended on its own. Superseding here (not just in `stop()`) is what
        # keeps that race from double-advancing: this `play()` call IS the
        # advance now, an already-queued one must not also run (module
        # docstring's "second, subtler race"). `MusicPlayer.skip()` reaches
        # HERE, not `stop()`, whenever the queue is non-empty -- which is
        # the common skip.
        self._supersede_pending_finish()
        # Stamped AFTER the bump: this track's completions are minted under
        # the epoch this call establishes, so anything that supersedes it
        # later moves `_finish_epoch` past this value.
        self._play_epoch = self._finish_epoch
        try:
            if already_playing:
                # A live swap, not stop()+play() -- the AudioPlayer thread never
                # sees a gap, which is the whole point: the OLD track (now
                # inside `mixed`) keeps being heard, blending into the new one.
                self._voice_client.source = wrapped
            else:
                self._voice_client.play(wrapped, after=self._after)
        except Exception:
            # The voice connection is dead (a dropped voice websocket makes
            # `play()` raise `Not connected to voice.`). The ffmpeg we just
            # spawned via the factory would otherwise be orphaned -- writing
            # into a pipe nobody reads, forever (found live 2026-09-03: a
            # dead-voice teardown left an ffmpeg running for minutes).
            # Clean it up before re-raising so the caller's teardown does
            # not leak a process.
            try:
                wrapped.cleanup()
            except Exception:
                log.exception("voice source cleanup after failed play() failed")
            raise
        if self._crossfade_seconds > 0 and duration_seconds is not None:
            delay = duration_seconds - self._crossfade_seconds
            if delay > 0:
                self._crossfade_timer = self._loop.call_later(delay, self._on_crossfade_due)

    def pause(self) -> None:
        self._voice_client.pause()
        self._paused = True

    def resume(self) -> None:
        self._voice_client.resume()
        self._paused = False

    def set_volume(self, volume: float) -> None:
        self._volume = volume
        if self._current_source is not None:
            self._current_source.volume = volume

    def stop(self) -> None:
        # Invalidate a completion this `stop()` is about to SUPERSEDE — see
        # the module docstring's second race, and why cancelling the
        # `Future` is not what makes that hold.
        self._cancel_crossfade_timer()
        self._cancel_monitor()
        self._supersede_pending_finish()
        self._suppress_next_after = True
        self._current_raw = None
        self._current_stream_url = None
        self._current_duration_seconds = None
        self._voice_client.stop()

    async def disconnect(self) -> None:
        """`force=True`, not discord.py's default: `VoiceConnectionState.
        disconnect(force=False, ...)` returns EARLY, before ever calling
        `voice_client.cleanup()`, whenever the underlying voice websocket
        is already not connected (Discord's own voice server can drop an
        idle connection silently, independent of anything this class
        does). `cleanup()` is what unregisters `guild.voice_client` --
        skip it and the guild is left permanently "already connected" in
        discord.py's own bookkeeping, so the next real `channel.connect()`
        raises `ClientException` forever, with no live player or audio to
        show for it (found live 2026-08-24: an idle-timeout disconnect
        silently hit this path, and a later `/music play`-equivalent could
        not rejoin for over an hour until the process restarted). This
        method is only ever called because NORA decided the session is
        over (idle timeout, `/music stop`, an emptied channel) -- local
        state must end up clean regardless of whether the remote side
        already agrees, which is exactly what `force=True` guarantees."""
        await self._voice_client.disconnect(force=True)

    def _supersede_pending_finish(self) -> None:
        """Invalidate any completion already queued on the loop, because the
        caller is TAKING OVER the advance it would have driven.

        The two halves cover disjoint cases and BOTH are load-bearing:

        * the epoch stops a completion that has **not started** —
          `_run_on_finished` compares its minted epoch against this one
          before it awaits anything. `Future.cancel()` cannot do this:
          measured for issue #179, cancelling the `concurrent.futures.
          Future` that `run_coroutine_threadsafe` returns reports success
          and the coroutine runs anyway.
        * `Future.cancel()` stops one that **has started and is suspended**
          at an `await` — a completion sitting on `MusicPlayerManager`'s
          per-guild lock while `skip()` holds it. The epoch cannot do this;
          that completion already passed the check.

        Known hazard, NOT fixed here (issue #193): cancelling a completion
        suspended INSIDE `MusicPlayer._advance` can land after `pop_next` has
        already removed the row, losing a track. The durable fix is for
        `on_track_finished` to verify the track that finished is still the
        one playing, rather than for the cancel to arrive in time."""
        self._finish_epoch += 1
        pending, self._pending_finish = self._pending_finish, None
        if pending is not None:
            pending.cancel()

    def _cancel_crossfade_timer(self) -> None:
        if self._crossfade_timer is not None:
            self._crossfade_timer.cancel()
            self._crossfade_timer = None

    def _on_crossfade_due(self) -> None:
        """Fires `crossfade_seconds` before the current track's expected
        end (runs on the event loop, scheduled by `call_later` — NOT the
        audio thread). Signals `on_finished` EARLY so `MusicPlayer._advance`
        resolves and hands the next track to `play()` while this one is
        still audible, which is what makes that next `play()` call a
        crossfade instead of a plain start.

        Deliberately does NOT set `_suppress_next_after` — unlike `stop()`,
        this never calls `voice_client.stop()`, so it never makes the real
        `after` fire early in the first place. A live `.source` swap does
        not end the `AudioPlayer` thread's loop either, so the real `after`
        (registered once, at the very first `play()` of the session) only
        ever fires when whatever is CURRENTLY assigned to `.source` truly
        reaches EOF with no further swap incoming — i.e. exactly once, at
        the genuine end of the whole crossfade chain, and only that firing
        should ever advance anything. **Bug fixed 2026-08-24**: this used
        to set the suppression flag here too, which — because it is a
        single boolean, not a per-track token — permanently swallowed that
        one genuine final signal the first time ANY early crossfade fired,
        silently breaking auto-advance after the very next track."""
        self._crossfade_timer = None
        if not self._voice_client.is_playing():
            return  # skipped/stopped already; nothing to crossfade into
        self._pending_finish = asyncio.run_coroutine_threadsafe(
            self._run_on_finished(self._play_epoch), self._loop,
        )

    def _after(self, error: Exception | None) -> None:
        """Called on the audio player's own thread — never `await` or touch
        asyncio state directly here."""
        if self._suppress_next_after:
            self._suppress_next_after = False
            return
        if error is not None:
            log.warning("voice playback ended with an error: %s", error)
        # A NATURAL track end clears the loaded-track marker right here, on
        # the thread that observed it (plain attribute writes; no asyncio
        # state). Without this the voice-death monitor (R-66) would see
        # `is_playing() == False` while a source is still loaded and, after
        # two 5s ticks, tear the session down ~10s after the final track
        # ends instead of respecting `idle_timeout_seconds` (external audit
        # CRITICAL-1, 2026-08-26). `play()`/crossfade re-populate both
        # fields for the next track; `stop()` clears them itself.
        self._current_raw = None
        self._current_source = None
        # The epoch is read HERE, on the audio thread, and carried into the
        # coroutine: whatever supersedes this completion between now and the
        # loop running it will have bumped it past this value.
        self._pending_finish = asyncio.run_coroutine_threadsafe(
            self._run_on_finished(self._play_epoch), self._loop,
        )

    async def _run_on_finished(self, epoch: int) -> None:
        # Runs on the LOOP. If something took over the advance between this
        # completion being queued and it getting a turn — `MusicPlayer.
        # skip()`'s own `play()`/`stop()`, a voice reconnect — the epoch has
        # moved on and this signal is stale: delivering it would advance the
        # queue a second time for one skip (issue #179). This check, not the
        # `Future.cancel()` in `_supersede_pending_finish`, is what makes
        # that impossible.
        if epoch != self._finish_epoch:
            log.info(
                "voice completion superseded before it ran (epoch %d, now %d); "
                "not advancing", epoch, self._finish_epoch,
            )
            return
        # The `Future` `run_coroutine_threadsafe` returns is otherwise never
        # awaited by anything — an exception raised inside `on_finished()`
        # (e.g. a transient DB error in `MusicPlayerManager.on_track_
        # finished`) would sit unretrieved on that Future forever, logged
        # nowhere, and playback would just silently stop.
        try:
            await self._on_finished()
        except Exception:
            log.exception("on_finished handler raised")
        finally:
            self._pending_finish = None

    # -- voice-death monitor (R-66) ---------------------------------------

    def _ensure_monitor(self) -> None:
        """Starts the voice-death monitor on first `play()` of a session.
        The monitor only runs while a track is EXPECTED to be audible; once
        the session ends (`stop()`/`disconnect()`) it cancels itself."""
        if self._monitor_task is None:
            self._monitor_task = self._loop.create_task(self._monitor_loop())
            log.info(
                "voice-death monitor started for %s",
                getattr(self._voice_client, "guild", "<unknown>"),
            )

    def _cancel_monitor(self) -> None:
        task = self._monitor_task
        self._monitor_task = None
        # Never cancel the CURRENT task: the monitor calls `_signal_voice_death`
        # -> `on_voice_death` -> `MusicPlayerManager.stop` -> `voice.stop()` ->
        # `_cancel_monitor()` while still inside its own await stack. Cancelling
        # itself would throw `CancelledError` into the middle of the handler
        # coroutine and abort the teardown it just started.
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _monitor_loop(self) -> None:
        """Every `monitor_interval_seconds`, check whether the voice
        connection is still genuinely alive. Discord's gateway and voice
        websockets are independent; a gateway resume does not touch the
        voice side, and discord.py's `VoiceClient.is_connected()` only
        reflects a state flag that a silent voice-websocket drop never
        updates (found live 2026-08-26: \"now its NOT even playing\" after
        a `discord gateway resumed` — every is_connected() kept reporting
        True while the audio thread was already dead)."""
        try:
            while True:
                await asyncio.sleep(self._monitor_interval_seconds)
                if self._current_raw is None:
                    continue  # nothing expected to be audible; idle
                if self._paused:
                    # Intentionally paused — `is_playing()` is False for
                    # the whole pause by design, not a death signal.
                    self._dead_consecutive = 0
                    continue
                ws = _voice_connection_ws(self._voice_client)
                ws_dead = ws is not MISSING and getattr(ws, "closed", False)
                if not ws_dead and self._voice_client.is_playing():
                    self._dead_consecutive = 0
                    continue
                # Either the voice WS closed, or discord.py stopped
                # claiming it is playing while a track is still loaded —
                # the two death signals a dead voice connection leaves
                # behind. Require TWO consecutive observations so a brief
                # blip (a reconnect in flight, a momentary thread pause)
                # doesn't tear a live session down.
                self._dead_consecutive += 1
                if self._dead_consecutive < 2:
                    continue
                log.warning(
                    "voice connection dead for guild %s (ws_closed=%s playing=%s); "
                    "attempting reconnect",
                    getattr(self._voice_client, "guild", "<unknown>"),
                    ws_dead, self._voice_client.is_playing(),
                )
                self._dead_consecutive = 0
                if await self._try_reconnect():
                    continue  # monitor keeps running on the fresh connection
                await self._signal_voice_death()
                return
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("voice-death monitor crashed")

    async def _try_reconnect(self) -> bool:
        """Attempt to rejoin the voice channel with a fresh connection and
        restart the current track. Returns `True` if the session is alive
        again (monitor keeps running), `False` if reconnect failed and the
        caller should signal death.

        The old `VoiceClient` is discarded (its websocket is dead); a fresh
        `channel.connect()` gives us a new one. The current track restarts
        from position 0 -- the same limitation `resume_from_persisted` has
        (design/discord-music/06-session-persistence.md: mid-frame resume is
        never attempted, only \"what track\")."""
        if self._reconnect_channel is None or self._current_stream_url is None:
            return False
        for attempt in range(1, self._reconnect_attempts + 1):
            try:
                log.info(
                    "voice reconnect attempt %d/%d for guild %s",
                    attempt, self._reconnect_attempts,
                    getattr(self._voice_client, "guild", "<unknown>"),
                )
                try:
                    new_client: discord.VoiceClient = await self._reconnect_channel.connect()
                except discord.ClientException as exc:
                    if "Already connected to a voice channel" in str(exc):
                        # The connection is actually ALIVE — this is a
                        # false-positive death (found live 2026-09-15: a
                        # pause made the monitor declare the connection
                        # dead, and `channel.connect()` refused because the
                        # existing `VoiceClient` never left). Treat the
                        # monitor's own probe as the authority: a client
                        # that still holds the guild's voice state is not
                        # dead, so the session must NOT be torn down. The
                        # track is resumed by the caller's `pause`/`resume`
                        # flow, not by a reconnect.
                        log.info(
                            "voice reconnect for guild %s found an alive connection "
                            "(Already connected); treating the death probe as a false "
                            "positive and keeping the session",
                            getattr(self._voice_client, "guild", "<unknown>"),
                        )
                        return True
                    raise
                # Swap the live client. The old one is dead; disconnect it
                # forcefully so discord.py's bookkeeping is clean (the
                # `force=True` rationale in `disconnect()` applies here too).
                old_client = self._voice_client
                self._voice_client = new_client
                try:
                    await old_client.disconnect(force=True)
                except Exception:
                    log.warning(
                        "old voice client disconnect during reconnect failed", exc_info=True,
                    )
                # Restart the current track on the fresh connection.
                self._current_raw = None
                self._current_source = None
                self._suppress_next_after = False
                # Dropping the reference is enough HERE only because the
                # `self.play()` immediately below supersedes the epoch for
                # us — that is what actually neutralises a completion the
                # dead connection's last `after` already queued (#179).
                self._pending_finish = None
                self.play(
                    self._current_stream_url,
                    duration_seconds=self._current_duration_seconds,
                )
                log.info(
                    "voice reconnect succeeded for guild %s; restarted %s",
                    getattr(self._voice_client, "guild", "<unknown>"),
                    self._current_stream_url,
                )
                return True
            except Exception:
                log.exception(
                    "voice reconnect attempt %d/%d failed for guild %s",
                    attempt, self._reconnect_attempts,
                    getattr(self._voice_client, "guild", "<unknown>"),
                )
                if attempt < self._reconnect_attempts:
                    await asyncio.sleep(self._reconnect_delay_seconds)
        return False

    async def _signal_voice_death(self) -> None:
        if self._on_voice_death is None:
            return
        try:
            await self._on_voice_death()
        except Exception:
            log.exception("on_voice_death handler raised")
