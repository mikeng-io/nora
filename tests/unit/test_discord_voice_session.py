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
constructor, which a unit test must never do.
"""

from __future__ import annotations

import asyncio

from nora.platform.discord.voice_session import DiscordVoiceSession


class _FakeVoiceClient:
    def __init__(self) -> None:
        self.played_sources: list[object] = []
        self.paused = 0
        self.resumed = 0
        self.disconnected = 0
        self._playing = False
        self._after = None

    def play(self, source: object, *, after) -> None:
        self.played_sources.append(source)
        self._after = after
        self._playing = True

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

    def factory(url: str) -> str:
        built.append(url)
        return f"source-for-{url}"

    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=lambda: None, audio_source_factory=factory,
    )

    session.play("https://cdn.example/a")

    assert built == ["https://cdn.example/a"]
    assert voice_client.played_sources == ["source-for-https://cdn.example/a"]


async def test_is_playing_pause_and_resume_delegate_to_the_voice_client() -> None:
    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=lambda: None, audio_source_factory=lambda url: url,
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
        on_finished=on_finished, audio_source_factory=lambda url: url,
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
        on_finished=on_finished, audio_source_factory=lambda url: url,
    )
    session.play("https://cdn.example/a")

    # Simulate the track finishing on its own (never called `.stop()`).
    voice_client.stop()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert finished.is_set() is True


async def test_disconnect_delegates_to_the_voice_client() -> None:
    voice_client = _FakeVoiceClient()
    session = DiscordVoiceSession(
        voice_client, loop=asyncio.get_running_loop(),
        on_finished=lambda: None, audio_source_factory=lambda url: url,
    )

    await session.disconnect()

    assert voice_client.disconnected == 1
