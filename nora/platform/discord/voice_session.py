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
    ) -> None:
        self._voice_client = voice_client
        self._loop = loop
        self._on_finished = on_finished
        self._audio_source_factory = audio_source_factory
        self._suppress_next_after = False
        self._pending_finish: Future[None] | None = None

    def is_playing(self) -> bool:
        return bool(self._voice_client.is_playing())

    def play(self, stream_url: str) -> None:
        source = self._audio_source_factory(stream_url)
        self._voice_client.play(source, after=self._after)

    def pause(self) -> None:
        self._voice_client.pause()

    def resume(self) -> None:
        self._voice_client.resume()

    def stop(self) -> None:
        # Cancel a completion this `stop()` is about to SUPERSEDE — see the
        # module docstring's second race. `Future.cancel()` on a not-yet-
        # started `run_coroutine_threadsafe` future prevents it from ever
        # running; a no-op if it already ran or was never scheduled.
        if self._pending_finish is not None:
            self._pending_finish.cancel()
            self._pending_finish = None
        self._suppress_next_after = True
        self._voice_client.stop()

    async def disconnect(self) -> None:
        await self._voice_client.disconnect()

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
