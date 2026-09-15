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
import threading
from collections.abc import Callable
from unittest.mock import patch

import discord
import pytest

from nora.platform.discord.voice_session import DiscordVoiceSession


class _FakeAudioSource(discord.AudioSource):
    """A real `AudioSource` subclass carrying a label so a test can still
    identify which URL it came from — `read()` is never actually called
    (nothing here drives real audio)."""

    def __init__(self, label: str) -> None:
        self.label = label

    def read(self) -> bytes:
        return b""


class _FakeVoiceWS:
    def __init__(self, closed: bool = False) -> None:
        self.closed = closed


class _FakeVoiceConnection:
    """The shape `_voice_connection_ws()` probes: `VoiceClient._connection.ws`."""

    def __init__(self, ws_closed: bool = False) -> None:
        self.ws = _FakeVoiceWS(ws_closed)


class _FakeVoiceChannel:
    """Stands in for `discord.VoiceChannel.connect()` -- returns a fresh
    `_FakeVoiceClient` per call, the shape `_try_reconnect` needs."""

    def __init__(self, *, fail_connects: int = 0) -> None:
        self.connect_calls = 0
        self._fail_connects = fail_connects

    async def connect(self) -> _FakeVoiceClient:
        self.connect_calls += 1
        if self.connect_calls <= self._fail_connects:
            raise discord.ClientException("Not connected to voice.")
        return _FakeVoiceClient()


class _AlreadyConnectedChannel:
    """A channel whose `connect()` always raises
    `Already connected to a voice channel.` — the exact shape a LIVE
    connection produces when the death monitor mis-fires (2026-09-15: a
    pause made the monitor probe "dead", but `guild.voice_client` still
    held the channel, so a fresh `connect()` refused)."""

    def __init__(self) -> None:
        self.connect_calls = 0

    async def connect(self) -> _FakeVoiceClient:
        self.connect_calls += 1
        raise discord.ClientException("Already connected to a voice channel.")


class _FakeVoiceClient:
    def __init__(self, *, connected: bool = True, ws_closed: bool = False) -> None:
        self.played_sources: list[object] = []
        # Real `discord.VoiceClient.source` has a setter that swaps the
        # live `AudioPlayer`'s source without stopping it -- a distinct
        # path from `play()`, tracked separately here so a test can tell a
        # crossfade swap apart from a fresh `play()` call.
        self.live_swaps: list[object] = []
        self.paused = 0
        self.resumed = 0
        self.disconnected = 0
        self.disconnect_force_calls: list[bool] = []
        self._playing = False
        self._resumed = True  # discord.py AudioPlayer starts resumed
        self._after = None
        self._source: object = None
        # Real `discord.VoiceClient.is_connected()` -- toggled by a test to
        # reproduce a voice websocket that dropped and never rejoined, while
        # `is_playing()` keeps reporting whatever it last reported (the real
        # hazard: `AudioPlayer`'s thread state and the voice connection's
        # own state are two different things, found live 2026-08-24 as
        # "the music is NOT playing again after it reply start play").
        self.connected = connected
        # The voice-websocket shape the death monitor probes
        # (`VoiceConnectionState.ws`), defaulting to OPEN -- a test flips
        # `_connection.ws.closed` to reproduce the silent voice-side drop
        # of 2026-08-26.
        self._connection = _FakeVoiceConnection(ws_closed=ws_closed)
        self.channel = None

    def is_connected(self) -> bool:
        return self.connected

    def play(self, source: object, *, after) -> None:
        # Real `discord.VoiceClient.play()` checks connection state BEFORE
        # `is_playing()` and raises rather than silently doing nothing --
        # this fake must reproduce that shape, not just the happy path
        # (CLAUDE.md's fake-realism rule).
        if not self.connected:
            raise discord.ClientException("Not connected to voice.")
        if self._playing:
            raise discord.ClientException("Already playing audio.")
        self.played_sources.append(source)
        self._source = source
        self._after = after
        self._playing = True
        self._resumed = True

    @property
    def source(self) -> object:
        return self._source

    @source.setter
    def source(self, value: object) -> None:
        self._source = value
        self.live_swaps.append(value)

    def is_playing(self) -> bool:
        # Real `discord.py` behaviour: `AudioPlayer.pause()` clears
        # `_resumed` so `is_playing()` returns False for the whole pause,
        # even though the source is still loaded and the connection is
        # healthy. Reproduced here so the death monitor's pause handling is
        # tested against the real signal it gets in production (found live
        # 2026-09-15: a pause made the monitor declare the connection dead).
        return self._playing and self._resumed

    def pause(self) -> None:
        self.paused += 1
        # Real discord.py: `AudioPlayer.pause()` clears `_resumed`.
        self._resumed = False

    def resume(self) -> None:
        self.resumed += 1
        # Real discord.py: `AudioPlayer.resume()` sets `_resumed`.
        self._resumed = True

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
        self.disconnect_force_calls.append(force)


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


async def _run_the_stale_completion_race(
    supersede: Callable[[DiscordVoiceSession], None],
) -> list[str]:
    """Drives the race the two tests below assert on, and returns what
    `on_finished` recorded.

    The hand-off is the REAL one: `discord.py` fires `after` from the
    `AudioPlayer`'s own thread, never the loop's, so this drives it from a
    real thread too — that is what makes `run_coroutine_threadsafe` actually
    cross threads here instead of being a same-thread `call_soon`. The loop
    is parked in `Thread.join()` while that happens, so the completion is
    provably QUEUED AND NOT RUN when `supersede` is called: the loop has had
    no turn at all.

    **The wait is on a condition, not on a count of scheduler turns.** The
    marker coroutine is handed to the loop through the same
    `run_coroutine_threadsafe` path immediately after the completion, and
    `loop.call_soon_threadsafe` callbacks run in registration order, so the
    marker cannot have run until the completion has already had its turn.
    Waiting two `await asyncio.sleep(0)`s instead — which is what this test
    did until issue #179 — was a guess about scheduler behaviour rather than
    an observation of the code, and it is why a genuine, deterministic,
    every-platform failure was carried for weeks as "flaky on Apple
    Silicon"."""
    loop = asyncio.get_running_loop()
    voice_client = _FakeVoiceClient()
    calls: list[str] = []
    drained = asyncio.Event()

    async def on_finished() -> None:
        calls.append("finished")

    async def marker() -> None:
        drained.set()

    session = DiscordVoiceSession(
        voice_client, loop=loop,
        on_finished=on_finished, audio_source_factory=lambda url: _FakeAudioSource(url),
    )
    session.play("https://cdn.example/a")

    def audio_player_thread() -> None:
        # The track ends on its own: `AudioPlayer.run`'s `finally` calls
        # `after`, which queues `on_finished` on the loop.
        voice_client.stop()
        asyncio.run_coroutine_threadsafe(marker(), loop)

    thread = threading.Thread(target=audio_player_thread, name="fake-audio-player")
    thread.start()
    thread.join()

    # `MusicPlayer.skip()`'s own advance, landing before the queued
    # completion got a turn -- the exact ordering `skip()` produces (its
    # call chain has no `await` between the natural end and the advance).
    supersede(session)

    # Generous, and a DEADLOCK guard rather than a measurement: the marker is
    # behind the completion in the loop's own queue, so it arrives as soon as
    # the loop turns at all.
    await asyncio.wait_for(drained.wait(), timeout=5.0)
    return calls


async def test_a_pending_natural_finish_is_cancelled_by_a_stop_that_beats_it_to_running() -> None:
    """The race a subagent review found: `_after` schedules `on_finished`
    via `run_coroutine_threadsafe` (queued, not yet run) when a track ends
    naturally; if `MusicPlayer.skip()` reaches `voice.stop()` before that
    scheduled coroutine gets a turn, the stale notification must never
    fire — or `skip()`'s own advance and this one both run, skipping two
    tracks for one skip command.

    `stop()` is `skip()`'s path when the queue is EMPTY."""
    calls = await _run_the_stale_completion_race(lambda session: session.stop())

    assert calls == [], (
        f"a completion superseded by stop() still reached on_finished "
        f"({len(calls)} call(s), expected 0): MusicPlayer.skip()'s own advance "
        f"plus this one advances the queue twice for one skip command"
    )


async def test_a_pending_natural_finish_is_cancelled_by_a_play_that_beats_it_to_running() -> None:
    """The same race down `skip()`'s OTHER path, and the commoner one:
    `MusicPlayer.skip()` deliberately does NOT call `voice.stop()` when a
    next track exists — `_advance()`'s own `voice.play()` supersedes the
    audible track instead. A completion queued by the outgoing track's
    natural end must not survive that `play()` either.

    Measured through the real `MusicPlayer` for issue #179 with a queue of
    A/B/C: one `/music skip` after A's natural end left **C** playing."""
    calls = await _run_the_stale_completion_race(
        lambda session: session.play("https://cdn.example/b"),
    )

    assert calls == [], (
        f"a completion superseded by play() still reached on_finished "
        f"({len(calls)} call(s), expected 0): the track skip() just started "
        f"is immediately skipped again"
    )


async def test_a_completion_already_suspended_inside_on_finished_is_cancelled_by_a_stop() -> None:
    """The OTHER half of `_supersede_pending_finish`, and the half the epoch
    cannot do.

    Once a completion has started, it has already passed the epoch check —
    only `Future.cancel()` can still stop it. That is not hypothetical: the
    completion's first real `await` in production is
    `MusicPlayerManager.on_track_finished`'s per-guild lock, which
    `MusicPlayer.skip()` is holding at exactly that moment. Here the gate
    stands in for that lock.

    The wait is on the CONDITION that the completion has started and reached
    its await; the deadline is a deadlock guard."""
    loop = asyncio.get_running_loop()
    voice_client = _FakeVoiceClient()
    started = asyncio.Event()
    completed: list[str] = []
    gate = asyncio.Event()

    async def on_finished() -> None:
        started.set()
        await gate.wait()          # stands in for the per-guild lock
        completed.append("advanced")

    session = DiscordVoiceSession(
        voice_client, loop=loop,
        on_finished=on_finished, audio_source_factory=lambda url: _FakeAudioSource(url),
    )
    session.play("https://cdn.example/a")

    thread = threading.Thread(target=voice_client.stop, name="fake-audio-player")
    thread.start()
    thread.join()

    # Let the completion start and reach its await -- this is the case the
    # epoch is powerless against.
    await asyncio.wait_for(started.wait(), timeout=5.0)

    session.stop()               # skip() supersedes it while it waits
    gate.set()                   # the lock it was waiting on is released
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert completed == [], (
        f"a completion superseded while suspended mid-advance still finished "
        f"its work: {completed}"
    )


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


async def test_disconnect_delegates_to_the_voice_client_with_force_true() -> None:
    """`force=True`, not discord.py's default `False`: real
    `VoiceConnectionState.disconnect(force=False, ...)` returns EARLY,
    skipping `voice_client.cleanup()`, whenever the underlying voice
    websocket is already not connected -- Discord's own voice server can
    drop an idle connection silently, independent of anything this class
    does. Skipping cleanup leaves `guild.voice_client` registered forever,
    so the next real `channel.connect()` for that guild raises
    `ClientException('Already connected to a voice channel.')` with no
    live player or audio to show for it (found live 2026-08-24: an
    idle-timeout disconnect hit exactly this path, and a later play
    request could not rejoin the guild for over an hour until the process
    restarted)."""
    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=lambda: None, audio_source_factory=lambda url: _FakeAudioSource(url),
    )

    await session.disconnect()

    assert voice_client.disconnected == 1
    assert voice_client.disconnect_force_calls == [True]


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


async def test_play_does_not_silently_live_swap_onto_a_disconnected_voice_client() -> None:
    """**Regression test for a real bug** (found live 2026-08-24, reported
    as "the music is NOT playing again after it reply start play"): a voice
    websocket drop (Discord close code 1006) left `voice_client.is_playing()`
    stuck reporting `True` while the connection itself never came back
    (`is_connected()` False, and no further "Voice handshake complete" ever
    logged). The old code decided "already playing" from `is_playing()`
    alone and did a live `.source` swap -- which does nothing when there is
    no live `AudioPlayer` actually attached to a real connection. The tool
    call still reported `status=ok` and Nora's reply claimed playback
    started, while nothing was audible.

    The fix: `is_connected()` gates the live-swap/crossfade path too. When
    the voice client is not actually connected, this must fall through to a
    real `voice_client.play()` call -- which, same as real discord.py,
    raises rather than silently doing nothing, so the failure is visible to
    the caller instead of masquerading as success."""
    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        audio_source_factory=lambda url: _FakeAudioSource(url), crossfade_seconds=3.0,
    )
    session.play("https://cdn.example/a")
    assert voice_client.is_playing() is True

    # The websocket drops; discord.py's reconnect never completes, but the
    # stale `AudioPlayer` thread state still reports "playing".
    voice_client.connected = False

    with pytest.raises(discord.ClientException):
        session.play("https://cdn.example/b")

    # No live swap happened -- the broken connection never got a fake
    # "success" playing a queued track nobody can hear.
    assert voice_client.live_swaps == []


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


async def test_the_voice_death_monitor_signals_death_after_two_dead_ticks() -> None:
    """R-66: the voice websocket closed silently (the 2026-08-26 live
    failure) while a track is loaded -- the monitor observes two
    consecutive dead ticks and fires `on_voice_death` exactly once, so
    `MusicPlayerManager` can tear the session down like a stop."""
    voice_client = _FakeVoiceClient(ws_closed=True)
    deaths = 0

    async def on_voice_death() -> None:
        nonlocal deaths
        deaths += 1

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        on_voice_death=on_voice_death,
        audio_source_factory=lambda url: _FakeAudioSource(url),
        monitor_interval_seconds=0.02,
    )
    session.play("https://cdn.example/a")

    await asyncio.sleep(0.12)

    assert deaths == 1, "two consecutive dead ticks must signal death exactly once"


async def test_the_voice_death_monitor_stays_silent_while_the_connection_is_alive() -> None:
    """The monitor must NOT fire on a healthy connection -- a reconnect
    blip or momentary thread pause must never tear a live session down."""
    voice_client = _FakeVoiceClient()  # ws open, playing after play()
    deaths = 0

    async def on_voice_death() -> None:
        nonlocal deaths
        deaths += 1

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        on_voice_death=on_voice_death,
        audio_source_factory=lambda url: _FakeAudioSource(url),
        monitor_interval_seconds=0.02,
    )
    session.play("https://cdn.example/a")

    await asyncio.sleep(0.08)
    session.stop()  # cancels the still-running monitor so no task leaks

    assert deaths == 0


async def test_the_voice_death_monitor_stays_silent_after_a_natural_track_end() -> None:
    """External audit CRITICAL-1 (2026-08-26): after a track ends NATURALLY
    (the queue is empty, nothing else was queued), the loaded-track marker
    is cleared so the monitor must NOT treat the now-idle session as a dead
    voice connection. Before the fix the monitor saw `is_playing()==False`
    while a source was still loaded and tore the session down ~10s after
    the final track, instead of respecting `idle_timeout_seconds`."""
    voice_client = _FakeVoiceClient()
    deaths = 0

    async def on_voice_death() -> None:
        nonlocal deaths
        deaths += 1

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        on_voice_death=on_voice_death,
        audio_source_factory=lambda url: _FakeAudioSource(url),
        monitor_interval_seconds=0.02,
    )
    session.play("https://cdn.example/a")

    # Simulate the track reaching EOF on its own -- the same path a real
    # natural end takes (`_after` fires with no preceding `stop()`).
    voice_client.stop()
    await asyncio.sleep(0.05)
    # The session is now idle (nothing loaded, nothing playing) but the
    # voice connection itself is still healthy and the guild may keep it
    # open until the idle timeout -- the monitor must not tear it down.
    await asyncio.sleep(0.1)
    session.stop()

    assert deaths == 0


async def test_the_voice_death_monitor_stays_silent_while_paused() -> None:
    """2026-09-15 live: a pause clicked while a track was loaded made the
    death monitor see `is_playing()==False` for two consecutive 5s ticks
    and tear the session down — reconnect then failed with \"Already
    connected to a voice channel\" (the connection was ALIVE) and the room
    had no idea music had stopped. A paused track is intentional silence,
    not a death signal: the monitor must never fire while paused."""
    voice_client = _FakeVoiceClient()
    deaths = 0

    async def on_voice_death() -> None:
        nonlocal deaths
        deaths += 1

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        on_voice_death=on_voice_death,
        audio_source_factory=lambda url: _FakeAudioSource(url),
        monitor_interval_seconds=0.02,
    )
    session.play("https://cdn.example/a")

    session.pause()
    # More than two dead ticks' worth of paused time — pre-fix this fired
    # death after two ticks, exactly like the live incident.
    await asyncio.sleep(0.1)
    session.resume()

    assert deaths == 0, "a paused track must never be read as a dead voice connection"

    # After resume the monitor goes back to normal: a REAL death still fires.
    voice_client._resumed = False  # simulate a real dropped audio thread
    await asyncio.sleep(0.1)
    assert deaths == 1, "resume must re-arm the monitor against real deaths"


async def test_voice_reconnect_survives_a_false_positive_death_probe() -> None:
    """2026-09-15 live companion: when the death monitor mis-fires while
    the connection is actually alive, `channel.connect()` raises
    `Already connected to a voice channel.` — that is PROOF the connection
    is alive, not a failed reconnect. `_try_reconnect` must treat it as a
    false positive and keep the session, never signal death."""
    voice_client = _FakeVoiceClient(ws_closed=True)  # probe says "dead"
    # A channel whose connect() always raises "Already connected" — the
    # shape the REAL connection-alive state produces.
    channel = _AlreadyConnectedChannel()
    deaths = 0

    async def on_voice_death() -> None:
        nonlocal deaths
        deaths += 1

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        on_voice_death=on_voice_death,
        audio_source_factory=lambda url: _FakeAudioSource(url),
        monitor_interval_seconds=0.02,
        reconnect_channel=channel,
    )
    session.play("https://cdn.example/a")

    await asyncio.sleep(0.12)

    assert deaths == 0, "Already connected must be read as alive, not as death"
    assert session._voice_client is voice_client, "no client swap on a false positive"


async def test_voice_death_reconnects_and_restarts_the_track() -> None:
    """2026-09-03 live: a voice websocket drop made the monitor tear the
    session down, killing autoplay. With `reconnect_channel` set, the
    monitor must rejoin with a fresh connection and restart the current
    track instead of signalling death."""
    voice_client = _FakeVoiceClient(ws_closed=True)
    channel = _FakeVoiceChannel()
    deaths = 0

    async def on_voice_death() -> None:
        nonlocal deaths
        deaths += 1

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        on_voice_death=on_voice_death,
        audio_source_factory=lambda url: _FakeAudioSource(url),
        monitor_interval_seconds=0.02,
        reconnect_channel=channel,
    )
    session.play("https://cdn.example/a")

    await asyncio.sleep(0.12)

    assert deaths == 0, "reconnect must prevent the death signal"
    assert channel.connect_calls == 1, "must have rejoined the channel"
    assert session._voice_client is not voice_client, "must swap to a fresh client"
    assert session._voice_client.played_sources, "must restart the track on the new client"


async def test_voice_death_reconnect_failure_still_signals_death() -> None:
    """If every reconnect attempt fails, the session must still tear down
    like before -- a dead voice connection must not leave the player
    believing it is playing."""
    voice_client = _FakeVoiceClient(ws_closed=True)
    channel = _FakeVoiceChannel(fail_connects=3)
    deaths = 0

    async def on_voice_death() -> None:
        nonlocal deaths
        deaths += 1

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        on_voice_death=on_voice_death,
        audio_source_factory=lambda url: _FakeAudioSource(url),
        monitor_interval_seconds=0.02,
        reconnect_channel=channel,
        reconnect_attempts=2,
        reconnect_delay_seconds=0.01,
    )
    session.play("https://cdn.example/a")

    await asyncio.sleep(0.15)

    assert deaths == 1, "failed reconnect must still signal death"
    assert channel.connect_calls == 2, "must have tried both attempts"


async def test_voice_death_without_reconnect_channel_signals_death_immediately() -> None:
    """`reconnect_channel=None` (the default) keeps the pre-existing
    behaviour: signal death on the first observation, no reconnect."""
    voice_client = _FakeVoiceClient(ws_closed=True)
    deaths = 0

    async def on_voice_death() -> None:
        nonlocal deaths
        deaths += 1

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        on_voice_death=on_voice_death,
        audio_source_factory=lambda url: _FakeAudioSource(url),
        monitor_interval_seconds=0.02,
    )
    session.play("https://cdn.example/a")

    await asyncio.sleep(0.12)

    assert deaths == 1


async def test_a_failed_play_cleans_up_the_just_spawned_source() -> None:
    """2026-09-03 live: a dead-voice teardown left an orphan ffmpeg running
    for minutes. When `voice_client.play()` raises (dead connection), the
    source we just spawned via the factory must be cleaned up so its ffmpeg
    is reaped -- otherwise the process writes into a pipe nobody reads
    forever."""
    voice_client = _FakeVoiceClient(connected=False)
    cleaned: list[object] = []

    class _TrackingSource(_FakeAudioSource):
        def cleanup(self) -> None:
            cleaned.append(self)

    def factory(url: str) -> _TrackingSource:
        return _TrackingSource(url)

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(), on_finished=lambda: None,
        audio_source_factory=factory,
    )

    with pytest.raises(discord.ClientException):
        session.play("https://cdn.example/a")

    assert len(cleaned) == 1, "the failed play's source must be cleaned up"
    assert cleaned[0].label == "https://cdn.example/a"
