"""`MusicPlayer` — the per-guild state machine (design/discord-music/
03-state-and-persistence.md, 02-control-surface.md). Exercised against a
fake `VoiceSession` that reproduces the one behaviour that matters for
`skip()`'s correctness: `stop()` does NOT itself invoke `on_track_finished`
(that is the real `discord.py` voice client's job, via its own `after`
callback and the Discord integration layer's scheduling — out of scope
here, per 02-control-surface.md's own boundary).

`_StubSource` is a throwaway, test-file-local `TrackSource` double — not a
`nora.testing` fake — because `MusicPlayer`'s own logic is what is under
test here, not `TrackSource`'s fidelity to the real yt-dlp boundary
(that's `test_ytdlp_source.py`'s job, and `YtDlpSource` is still the
`TrackSource` port's only real, contract-suite-eligible implementation).
"""

from __future__ import annotations

from datetime import UTC, datetime

from nora.domain.ports import ResolvedTrack
from nora.platform.discord.music_player import MusicPlayer
from nora.testing.music import FakeMusicQueueStore

T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


class _StubSource:
    def __init__(self, tracks: dict[str, ResolvedTrack | None]) -> None:
        self._tracks = tracks
        self.resolved: list[str] = []

    async def resolve(self, *, query_or_url: str) -> ResolvedTrack | None:
        self.resolved.append(query_or_url)
        return self._tracks.get(query_or_url)


class _FakeVoiceSession:
    def __init__(self) -> None:
        self.played: list[str] = []
        self.stopped = 0
        self.paused = 0
        self.resumed = 0
        self.disconnected = 0
        self.volume_calls: list[float] = []
        self._playing = False

    def is_playing(self) -> bool:
        return self._playing

    def play(self, stream_url: str) -> None:
        self.played.append(stream_url)
        self._playing = True

    def pause(self) -> None:
        self.paused += 1

    def resume(self) -> None:
        self.resumed += 1

    def stop(self) -> None:
        self.stopped += 1
        self._playing = False

    def set_volume(self, volume: float) -> None:
        self.volume_calls.append(volume)

    async def disconnect(self) -> None:
        self.disconnected += 1


_TRACK_A = ResolvedTrack(
    title="Track A", stream_url="https://cdn.example/a", duration_seconds=200,
    source="youtube", source_id="a",
)
_TRACK_B = ResolvedTrack(
    title="Track B", stream_url="https://cdn.example/b", duration_seconds=180,
    source="youtube", source_id="b",
)


def _player(tracks: dict[str, ResolvedTrack | None]) -> tuple[MusicPlayer, _FakeVoiceSession]:
    voice = _FakeVoiceSession()
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(),
        source=_StubSource(tracks), voice=voice,
    )
    return player, voice


async def test_play_with_an_empty_queue_starts_playback_immediately() -> None:
    player, voice = _player({"track a": _TRACK_A})

    outcome = await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    assert outcome.status == "playing"
    assert voice.played == ["https://cdn.example/a"]
    status = await player.status()
    assert status.now_playing is not None
    assert status.now_playing.title == "Track A"
    assert status.queue == []


async def test_play_while_something_is_already_playing_only_enqueues() -> None:
    player, voice = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    outcome = await player.play(query_or_url="track b", requested_by="miles", added_at=T0)

    assert outcome.status == "queued"
    assert voice.played == ["https://cdn.example/a"]
    status = await player.status()
    assert status.now_playing.title == "Track A"
    assert [t.title for t in status.queue] == ["Track B"]


async def test_play_returns_nothing_found_when_the_source_cannot_resolve() -> None:
    player, voice = _player({})

    outcome = await player.play(query_or_url="silence", requested_by="miles", added_at=T0)

    assert outcome.status == "nothing_found"
    assert voice.played == []
    status = await player.status()
    assert status.now_playing is None
    assert status.queue == []


async def test_skip_stops_current_and_advances_to_the_next_queued_track() -> None:
    player, voice = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track b", requested_by="miles", added_at=T0)

    skipped = await player.skip()

    assert skipped is not None
    assert skipped.title == "Track A"
    assert voice.stopped == 1
    assert voice.played == ["https://cdn.example/a", "https://cdn.example/b"]
    status = await player.status()
    assert status.now_playing.title == "Track B"
    assert status.queue == []


async def test_skip_with_nothing_queued_stops_and_leaves_nothing_playing() -> None:
    player, voice = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    skipped = await player.skip()

    assert skipped is not None
    assert voice.stopped == 1
    status = await player.status()
    assert status.now_playing is None


async def test_on_track_finished_advances_without_calling_voice_stop() -> None:
    """The real voice client already stopped on its own before this fires —
    `MusicPlayer` must not call `voice.stop()` a second time."""
    player, voice = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track b", requested_by="miles", added_at=T0)

    await player.on_track_finished()

    assert voice.stopped == 0
    status = await player.status()
    assert status.now_playing.title == "Track B"


async def test_pause_returns_false_when_nothing_is_playing() -> None:
    player, voice = _player({})

    assert player.pause() is False
    assert voice.paused == 0


async def test_pause_then_resume_round_trip_and_call_voice_once_each() -> None:
    player, voice = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    assert player.pause() is True
    assert voice.paused == 1
    assert (await player.status()).paused is True

    assert player.resume() is True
    assert voice.resumed == 1
    assert (await player.status()).paused is False


async def test_stop_clears_the_queue_stops_playback_and_disconnects() -> None:
    player, voice = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track b", requested_by="miles", added_at=T0)

    await player.stop()

    assert voice.stopped == 1
    assert voice.disconnected == 1
    status = await player.status()
    assert status.now_playing is None
    assert status.queue == []


async def test_set_volume_applies_immediately_and_reports_true() -> None:
    player, voice = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    assert player.set_volume(0.5) is True

    assert voice.volume_calls == [0.5]


async def test_set_volume_works_with_nothing_playing() -> None:
    """A setting, not an action on in-progress playback — unlike pause/
    resume, nothing needs to be playing for this to take effect (it
    applies to whatever plays next)."""
    player, voice = _player({})

    assert player.set_volume(0.5) is True

    assert voice.volume_calls == [0.5]


async def test_set_volume_refuses_a_value_outside_zero_to_two() -> None:
    player, voice = _player({})

    assert player.set_volume(-0.1) is False
    assert player.set_volume(2.1) is False

    assert voice.volume_calls == []


async def test_set_volume_accepts_the_boundary_values() -> None:
    player, voice = _player({})

    assert player.set_volume(0.0) is True
    assert player.set_volume(2.0) is True

    assert voice.volume_calls == [0.0, 2.0]
