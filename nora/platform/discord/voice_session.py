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
callback it did not expect to see).

**A second, subtler race** (subagent review, 2026-08-23): a track ending
naturally schedules `on_finished()` via `run_coroutine_threadsafe` — QUEUED
on the loop, not run yet. If `MusicPlayer.skip()` reaches `voice.stop()`
before that queued coroutine gets a turn (the common case: `skip()`'s call
chain has no `await` before `voice.stop()`, so it reliably wins the race
under Python's cooperative scheduling), the suppression flag above does
NOT help — it only guards the NEXT `after` invocation `stop()` itself
causes, not an EARLIER one already scheduled. Left unfixed, both the
already-queued `on_finished()` and `skip()`'s own explicit advance run,
advancing the queue twice for one skip. Fixed by keeping the `Future`
`run_coroutine_threadsafe` returns and cancelling it in `stop()` — before
the coroutine has started, cancellation prevents it from ever running.

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

from nora.platform.discord.crossfade import CrossfadeSource

log = logging.getLogger("nora.music")

AudioSourceFactory = Callable[[str], discord.AudioSource]


def _default_audio_source(stream_url: str) -> discord.AudioSource:
    return discord.FFmpegPCMAudio(
        stream_url, before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    )


class DiscordVoiceSession:
    """Satisfies `nora.platform.discord.music_player.VoiceSession`."""

    def __init__(
        self,
        voice_client: discord.VoiceClient,
        *,
        loop: asyncio.AbstractEventLoop,
        on_finished: Callable[[], Coroutine[Any, Any, None]],
        audio_source_factory: AudioSourceFactory = _default_audio_source,
        crossfade_seconds: float = 0.0,
    ) -> None:
        self._voice_client = voice_client
        self._loop = loop
        self._on_finished = on_finished
        self._audio_source_factory = audio_source_factory
        self._crossfade_seconds = crossfade_seconds
        self._suppress_next_after = False
        self._pending_finish: Future[None] | None = None
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

    def is_playing(self) -> bool:
        return bool(self._voice_client.is_playing())

    def play(self, stream_url: str, *, duration_seconds: float | None = None) -> None:
        raw = self._audio_source_factory(stream_url)
        mixed: discord.AudioSource = raw
        already_playing = self._voice_client.is_playing()
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
        # A completion signal for the track THIS call is replacing may
        # already be in flight -- e.g. `/music skip` landing right as this
        # track's own early crossfade timer fired. Cancelling here (not
        # just in `stop()`) is what keeps that race from double-advancing:
        # this `play()` call IS the advance now, an already-queued one
        # must not also run (module docstring's "second, subtler race").
        if self._pending_finish is not None:
            self._pending_finish.cancel()
            self._pending_finish = None
        if already_playing:
            # A live swap, not stop()+play() -- the AudioPlayer thread never
            # sees a gap, which is the whole point: the OLD track (now
            # inside `mixed`) keeps being heard, blending into the new one.
            self._voice_client.source = wrapped
        else:
            self._voice_client.play(wrapped, after=self._after)
        if self._crossfade_seconds > 0 and duration_seconds is not None:
            delay = duration_seconds - self._crossfade_seconds
            if delay > 0:
                self._crossfade_timer = self._loop.call_later(delay, self._on_crossfade_due)

    def pause(self) -> None:
        self._voice_client.pause()

    def resume(self) -> None:
        self._voice_client.resume()

    def set_volume(self, volume: float) -> None:
        self._volume = volume
        if self._current_source is not None:
            self._current_source.volume = volume

    def stop(self) -> None:
        # Cancel a completion this `stop()` is about to SUPERSEDE — see the
        # module docstring's second race. `Future.cancel()` on a not-yet-
        # started `run_coroutine_threadsafe` future prevents it from ever
        # running; a no-op if it already ran or was never scheduled.
        self._cancel_crossfade_timer()
        if self._pending_finish is not None:
            self._pending_finish.cancel()
            self._pending_finish = None
        self._suppress_next_after = True
        self._current_raw = None
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
            self._run_on_finished(), self._loop,
        )

    def _after(self, error: Exception | None) -> None:
        """Called on the audio player's own thread — never `await` or touch
        asyncio state directly here."""
        if self._suppress_next_after:
            self._suppress_next_after = False
            return
        if error is not None:
            log.warning("voice playback ended with an error: %s", error)
        self._pending_finish = asyncio.run_coroutine_threadsafe(
            self._run_on_finished(), self._loop,
        )

    async def _run_on_finished(self) -> None:
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
