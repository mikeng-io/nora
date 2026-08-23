"""`DiscordVoiceSession` — the `VoiceSession` port's real `discord.py`
implementation (design/discord-music/02-control-surface.md).

Verified against `discord.py` 2.7.1's own source (`discord.player.
AudioPlayer.run`'s `finally: self._call_after()`): `VoiceClient.stop()`
triggers its `after` callback exactly like natural completion does. That is
the one piece of non-obvious behaviour this class exists to handle —
`MusicPlayer.skip()`/`stop()` already drive the transition explicitly and
synchronously, so a `stop()` here must SWALLOW the next `after` firing, or a
skip advances the queue twice. `_FakeVoiceClient.stop()` reproduces that
real firing rather than omitting it, per CLAUDE.md's fake-realism rule.

`audio_source_factory` is stubbed out in every test here — the real default
(`discord.FFmpegPCMAudio(...)`) spawns an actual `ffmpeg` subprocess in its
constructor, which a unit test must never do. It returns a real
`discord.AudioSource` subclass (`_FakeAudioSource`), not a bare string:
`play()` wraps whatever the factory returns in `discord.
PCMVolumeTransformer`, which raises `TypeError` on anything that isn't
actually an `AudioSource` — the same fidelity requirement as `_FakeVoiceClient`
reproducing `stop()`'s real `after`-firing behaviour.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import discord

from nora.platform.discord.voice_session import DiscordVoiceSession


class _FakeAudioSource(discord.AudioSource):
    """A real `AudioSource` subclass carrying a label so a test can still
    identify which URL it came from — `read()` is never actually called
    (nothing here drives real audio)."""

    def __init__(self, label: str) -> None:
        self.label = label

    def read(self) -> bytes:
        return b""


class _FakeVoiceClient:
    def __init__(self) -> None:
        self.played_sources: list[object] = []
        # Real `discord.VoiceClient.source` has a setter that swaps the
        # live `AudioPlayer`'s source without stopping it -- a distinct
        # path from `play()`, tracked separately here so a test can tell a
        # crossfade swap apart from a fresh `play()` call.
        self.live_swaps: list[object] = []
        self.paused = 0
        self.resumed = 0
        self.disconnected = 0
        self._playing = False
        self._after = None
        self._source: object = None

    def play(self, source: object, *, after) -> None:
        self.played_sources.append(source)
        self._source = source
        self._after = after
        self._playing = True

    @property
    def source(self) -> object:
        return self._source

    @source.setter
    def source(self, value: object) -> None:
        self._source = value
        self.live_swaps.append(value)

    def is_playing(self) -> bool:
        return self._playing

    def pause(self) -> None:
        self.paused += 1

    def resume(self) -> None:
        self.resumed += 1

    def stop(self) -> None:
        # The real `discord.py` behaviour this fake exists to reproduce:
        # `AudioPlayer.run`'s `finally` block calls `after` on ANY exit,
        # including one `stop()` triggered.
        self._playing = False
        if self._after is not None:
            after, self._after = self._after, None
            after(None)

    async def disconnect(self, *, force: bool = False) -> None:
        self.disconnected += 1


async def test_play_builds_a_source_from_the_factory_and_hands_it_to_the_voice_client() -> None:
    voice_client = _FakeVoiceClient()
    built: list[str] = []

    def factory(url: str) -> _FakeAudioSource:
        built.append(url)
        return _FakeAudioSource(f"source-for-{url}")

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=lambda: None, audio_source_factory=factory,
    )

    session.play("https://cdn.example/a")

    assert built == ["https://cdn.example/a"]
    # `play()` wraps the factory's output in `PCMVolumeTransformer` (for
    # volume control) rather than handing it to the voice client bare — the
    # ORIGINAL factory output survives underneath as `.original`.
    [played] = voice_client.played_sources
    assert isinstance(played, discord.PCMVolumeTransformer)
    assert played.original.label == "source-for-https://cdn.example/a"


async def test_is_playing_pause_and_resume_delegate_to_the_voice_client() -> None:
    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=lambda: None, audio_source_factory=lambda url: _FakeAudioSource(url),
    )
    session.play("https://cdn.example/a")

    assert session.is_playing() is True
    session.pause()
    session.resume()

    assert voice_client.paused == 1
    assert voice_client.resumed == 1


async def test_stop_does_not_schedule_on_finished_it_swallows_that_after_call() -> None:
    voice_client = _FakeVoiceClient()
    finished = asyncio.Event()

    async def on_finished() -> None:
        finished.set()

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=on_finished, audio_source_factory=lambda url: _FakeAudioSource(url),
    )
    session.play("https://cdn.example/a")

    session.stop()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert finished.is_set() is False


async def test_a_natural_after_firing_without_a_preceding_stop_schedules_on_finished() -> None:
    voice_client = _FakeVoiceClient()
    finished = asyncio.Event()

    async def on_finished() -> None:
        finished.set()

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=on_finished, audio_source_factory=lambda url: _FakeAudioSource(url),
    )
    session.play("https://cdn.example/a")

    # Simulate the track finishing on its own (never called `.stop()`).
    voice_client.stop()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert finished.is_set() is True


async def test_a_pending_natural_finish_is_cancelled_by_a_stop_that_beats_it_to_running() -> None:
    """The race a subagent review found: `_after` schedules `on_finished`
    via `run_coroutine_threadsafe` (queued, not yet run) when a track ends
    naturally; if `MusicPlayer.skip()` reaches `voice.stop()` before that
    scheduled coroutine gets a turn, the stale notification must never
    fire — or `skip()`'s own advance and this one both run, skipping two
    tracks for one skip command."""
    voice_client = _FakeVoiceClient()
    calls: list[str] = []

    async def on_finished() -> None:
        calls.append("finished")

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=on_finished, audio_source_factory=lambda url: _FakeAudioSource(url),
    )
    session.play("https://cdn.example/a")

    # The track ends naturally -- `_after` schedules `on_finished` but the
    # loop has not run it yet.
    voice_client.stop()
    # `MusicPlayer.skip()`'s own `voice.stop()` call, reaching here with no
    # intervening `await` -- the exact ordering `skip()` produces in
    # practice (no yield point before it in the real call chain).
    session.stop()

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert calls == []


async def test_a_natural_finish_that_already_ran_is_unaffected_by_a_later_stop() -> None:
    """The other half of the same fix: a completion that already fired
    (and was not superseded by anything) must still be delivered."""
    voice_client = _FakeVoiceClient()
    calls: list[str] = []

    async def on_finished() -> None:
        calls.append("finished")

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=on_finished, audio_source_factory=lambda url: _FakeAudioSource(url),
    )
    session.play("https://cdn.example/a")

    voice_client.stop()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert calls == ["finished"]

    # A later, unrelated stop() must not un-deliver or duplicate it.
    session.stop()
    await asyncio.sleep(0)

    assert calls == ["finished"]


async def test_an_exception_from_on_finished_is_logged_not_swallowed() -> None:
    voice_client = _FakeVoiceClient()

    async def on_finished() -> None:
        raise RuntimeError("boom")

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=on_finished, audio_source_factory=lambda url: _FakeAudioSource(url),
    )
    session.play("https://cdn.example/a")

    with patch("nora.platform.discord.voice_session.log") as mock_log:
        voice_client.stop()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert mock_log.exception.called


async def test_disconnect_delegates_to_the_voice_client() -> None:
    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=lambda: None, audio_source_factory=lambda url: _FakeAudioSource(url),
    )

    await session.disconnect()

    assert voice_client.disconnected == 1


async def test_set_volume_before_anything_plays_is_applied_to_the_first_track() -> None:
    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=lambda: None, audio_source_factory=lambda url: _FakeAudioSource(url),
    )

    session.set_volume(0.5)
    session.play("https://cdn.example/a")

    [played] = voice_client.played_sources
    assert isinstance(played, discord.PCMVolumeTransformer)
    assert played.volume == 0.5


async def test_set_volume_while_playing_adjusts_the_live_source() -> None:
    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=lambda: None, audio_source_factory=lambda url: _FakeAudioSource(url),
    )
    session.play("https://cdn.example/a")

    session.set_volume(1.5)

    [played] = voice_client.played_sources
    assert played.volume == 1.5


async def test_volume_persists_across_tracks() -> None:
    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=lambda: None, audio_source_factory=lambda url: _FakeAudioSource(url),
    )
    session.play("https://cdn.example/a")
    session.set_volume(0.3)

    voice_client.stop()  # natural end of track A
    session.play("https://cdn.example/b")  # skip()'s own advance, in effect

    assert [s.volume for s in voice_client.played_sources] == [0.3, 0.3]


async def test_crossfade_disabled_by_default_never_live_swaps() -> None:
    """`crossfade_seconds=0.0` (the config default) must reproduce today's
    behaviour exactly: no timer, no live swap, even when `duration_seconds`
    is supplied."""
    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=lambda: None, audio_source_factory=lambda url: _FakeAudioSource(url),
    )

    session.play("https://cdn.example/a", duration_seconds=0.05)
    await asyncio.sleep(0.08)

    assert voice_client.live_swaps == []
    assert len(voice_client.played_sources) == 1


async def test_crossfade_fires_early_and_live_swaps_into_the_next_track() -> None:
    """The core spike mechanic: with crossfading on, the completion signal
    fires `crossfade_seconds` BEFORE the track's real end, and the next
    `play()` call -- landing while the old one is still technically
    "playing" -- live-swaps in a mixer instead of a fresh `play()`."""
    voice_client = _FakeVoiceClient()
    finished = asyncio.Event()

    async def on_finished() -> None:
        finished.set()

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=on_finished,
        audio_source_factory=lambda url: _FakeAudioSource(url), crossfade_seconds=0.02,
    )

    session.play("https://cdn.example/a", duration_seconds=0.06)
    await asyncio.wait_for(finished.wait(), timeout=1.0)
    # The early fire must land BEFORE the track's own true end -- the voice
    # client was never told to stop.
    assert voice_client.is_playing() is True

    session.play("https://cdn.example/b", duration_seconds=0.06)

    assert voice_client.live_swaps, "expected a live source swap, not a fresh play() call"
    mixer = voice_client.live_swaps[-1]
    assert isinstance(mixer, discord.PCMVolumeTransformer)
    from nora.platform.discord.crossfade import CrossfadeSource
    assert isinstance(mixer.original, CrossfadeSource)
    # Only the FIRST play() ever called `voice_client.play()` -- everything
    # after is a live swap, never a second `.play()` (which real discord.py
    # raises `ClientException` for while already playing).
    assert len(voice_client.played_sources) == 1


class _RealisticFFmpegLikeSource(discord.AudioSource):
    """Reproduces the exact hazard real `discord.player.FFmpegAudio.
    cleanup()` creates: it replaces the underlying pipe with discord.py's
    own `MISSING` sentinel, so a `.read()` call after cleanup raises
    `AttributeError` -- `_FakeAudioSource` above doesn't reproduce this
    (its `cleanup()` is a no-op), which is exactly why the ORIGINAL test
    suite for crossfading passed while a real, live bug shipped."""

    def __init__(self, label: str) -> None:
        self.label = label
        self._cleaned_up = False

    def read(self) -> bytes:
        if self._cleaned_up:
            raise AttributeError("'_MissingSentinel' object has no attribute 'read'")
        return b"\x00" * 3840

    def cleanup(self) -> None:
        self._cleaned_up = True


async def test_reassigning_current_source_does_not_kill_the_crossfading_old_track() -> None:
    """**Regression test for a real bug** (2026-08-24, caught live): a
    `PCMVolumeTransformer` wrapping the outgoing track's raw source
    (`DiscordVoiceSession._current_source`) loses its LAST reference the
    moment `play()` reassigns it to the new wrapper. CPython's refcounting
    runs its `__del__` synchronously right then -- `AudioSource.__del__` ->
    `PCMVolumeTransformer.cleanup()` -> the old raw source's `cleanup()` --
    poisoning it before `CrossfadeSource` ever gets to read a single frame
    from it. This is why crossfading always sounded exactly like a hard
    cut: the mixer crashed on its first or second read, discord.py's
    `AudioPlayer.run()` silently caught the exception and just stopped."""
    import gc

    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        audio_source_factory=lambda url: _RealisticFFmpegLikeSource(url),
        crossfade_seconds=0.02,
    )
    session.play("https://cdn.example/a", duration_seconds=10.0)  # long: no early timer firing
    session.play("https://cdn.example/b", duration_seconds=10.0)  # skip()'s own advance, in effect

    gc.collect()  # force any premature __del__ to fire NOW rather than whenever CPython feels like

    mixer = voice_client.live_swaps[-1]
    assert isinstance(mixer, discord.PCMVolumeTransformer)
    # Must not raise -- if it does, the old source was cleaned up out from
    # under the still-in-progress crossfade.
    mixer.read()


async def test_a_second_track_also_crossfades_and_the_chain_keeps_advancing() -> None:
    """**Regression test for a real bug** (2026-08-24, caught live): an
    earlier version of `_on_crossfade_due` set the same suppression flag
    `stop()` uses, which -- because it's a single boolean, not a per-track
    token -- silently swallowed the genuine final completion signal the
    FIRST time any early crossfade fired, breaking auto-advance after the
    very next track. A live `.source` swap never ends the `AudioPlayer`
    thread's loop, so the real `after` (registered once, at the session's
    first `play()`) only fires once, at the true end of the WHOLE chain --
    it must never be suppressed by an early crossfade fire, only by an
    actual `stop()`."""
    voice_client = _FakeVoiceClient()
    calls: list[str] = []

    async def on_finished() -> None:
        calls.append("finished")

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=on_finished,
        audio_source_factory=lambda url: _FakeAudioSource(url), crossfade_seconds=0.02,
    )
    session.play("https://cdn.example/a", duration_seconds=0.03)
    await asyncio.sleep(0.05)  # let track A's early timer fire
    assert calls == ["finished"]

    # `MusicPlayer._advance()`'s response to that signal, in effect: the
    # next track starts (and crossfades, since the old one is still live).
    session.play("https://cdn.example/b", duration_seconds=0.03)
    await asyncio.sleep(0.05)  # let track B's OWN early timer fire too
    assert calls == ["finished", "finished"], (
        "the second track's own crossfade boundary must still signal "
        "completion -- this is exactly what the bug silently broke"
    )

    # Nothing further was queued for track B -- its real, true end (no
    # further swap) must still fire normally, exactly once.
    voice_client.stop()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert calls == ["finished", "finished", "finished"]


async def test_no_crossfade_timer_when_duration_is_unknown() -> None:
    """A track `yt-dlp` couldn't determine a duration for (a live stream)
    must not crash the timer math -- it just plays with a hard cut, same
    as crossfading being off."""
    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        audio_source_factory=lambda url: _FakeAudioSource(url), crossfade_seconds=0.02,
    )

    session.play("https://cdn.example/a", duration_seconds=None)
    await asyncio.sleep(0.05)

    assert voice_client.live_swaps == []


async def test_stop_cancels_a_pending_crossfade_timer() -> None:
    """`/music skip`/`/music stop` stay an instant cut regardless of
    crossfade config -- a timer left running past `stop()` could fire an
    early "finished" signal for a track that isn't playing anymore."""
    voice_client = _FakeVoiceClient()
    calls: list[str] = []

    async def on_finished() -> None:
        calls.append("finished")

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=on_finished,
        audio_source_factory=lambda url: _FakeAudioSource(url), crossfade_seconds=0.02,
    )
    session.play("https://cdn.example/a", duration_seconds=0.05)

    session.stop()
    await asyncio.sleep(0.08)

    # `stop()` itself suppresses its own `after` firing (already covered
    # above) -- the thing THIS test guards is the early timer specifically
    # not also firing on top of that.
    assert calls == []
