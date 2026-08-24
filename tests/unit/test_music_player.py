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

from collections.abc import Sequence
from datetime import UTC, datetime

from nora.domain.ports import ResolvedTrack
from nora.platform.discord.music_player import MusicPlayer
from nora.testing.music import FakeMusicQueueStore, FakeMusicSessionStore

T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

_StubEntry = ResolvedTrack | Sequence[ResolvedTrack] | None


class _StubSource:
    """A single `ResolvedTrack` per query is the common case every existing
    test here uses unchanged; a tuple of several stands in for a playlist
    link resolving to more than one (`YtDlpSource`'s own playlist-expansion
    fidelity is `test_ytdlp_source.py`'s job, not this stub's).

    Every track handed in is ALSO registered under its own `.locator` --
    `MusicPlayer._advance` resolves that locator again right before playing
    (the lazy-resolve contract, `nora/domain/ports.py`), so a stub that only
    answered the ORIGINAL query would make every test's second lookup miss
    and skip the track as unresolvable."""

    def __init__(self, tracks: dict[str, _StubEntry]) -> None:
        self._tracks: dict[str, _StubEntry] = dict(tracks)
        for entry in tracks.values():
            for track in ((entry,) if isinstance(entry, ResolvedTrack) else entry or ()):
                self._tracks.setdefault(track.locator, track)
        self.resolved: list[str] = []

    async def resolve(self, *, query_or_url: str) -> Sequence[ResolvedTrack]:
        self.resolved.append(query_or_url)
        found = self._tracks.get(query_or_url)
        if found is None:
            return ()
        if isinstance(found, ResolvedTrack):
            return (found,)
        return found


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

    def play(self, stream_url: str, *, duration_seconds: float | None = None) -> None:
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
    source="youtube", source_id="a", locator="locator-a",
)
_TRACK_B = ResolvedTrack(
    title="Track B", stream_url="https://cdn.example/b", duration_seconds=180,
    source="youtube", source_id="b", locator="locator-b",
)


def _player(
    tracks: dict[str, _StubEntry],
) -> tuple[MusicPlayer, _FakeVoiceSession, FakeMusicSessionStore]:
    voice = _FakeVoiceSession()
    session = FakeMusicSessionStore()
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(),
        source=_StubSource(tracks), voice=voice, session=session,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    return player, voice, session


async def test_play_with_an_empty_queue_starts_playback_immediately() -> None:
    player, voice, _session = _player({"track a": _TRACK_A})

    outcome = await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    assert outcome.status == "playing"
    assert voice.played == ["https://cdn.example/a"]
    status = await player.status()
    assert status.now_playing is not None
    assert status.now_playing.title == "Track A"
    assert status.queue == []


async def test_play_while_something_is_already_playing_only_enqueues() -> None:
    player, voice, _session = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    outcome = await player.play(query_or_url="track b", requested_by="miles", added_at=T0)

    assert outcome.status == "queued"
    assert voice.played == ["https://cdn.example/a"]
    status = await player.status()
    assert status.now_playing.title == "Track A"
    assert [t.title for t in status.queue] == ["Track B"]


async def test_play_reports_what_actually_started_when_the_queue_had_a_leftover_track() -> None:
    """Found live 2026-08-24: a guild with a leftover queue row from an
    earlier, since-ended session (`MusicPlayerManager.discard_persisted_
    session` not clearing the queue was the root cause; this covers the
    OTHER half — `play()`'s own reporting must never assume `queued[0]`
    is what's playing). `_advance()` pops the FRONT of the queue, which is
    the STALE track here, not the one this call just resolved and enqueued
    behind it — `PlayOutcome.track` must say so, not claim the new request
    is what's now audible."""
    queue = FakeMusicQueueStore()
    await queue.enqueue(
        guild_id="guild-1", source="youtube", url="stale-locator", title="Stale Leftover",
        added_by_person_id="miles", added_at=T0,
    )
    voice = _FakeVoiceSession()
    session = FakeMusicSessionStore()
    source = _StubSource({"track a": _TRACK_A})
    source._tracks["stale-locator"] = ResolvedTrack(
        title="Stale Leftover", stream_url="https://cdn.example/stale", duration_seconds=100,
        source="youtube", source_id="stale", locator="stale-locator",
    )
    player = MusicPlayer(
        guild_id="guild-1", queue=queue, source=source, voice=voice, session=session,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )

    outcome = await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    assert outcome.status == "playing"
    assert outcome.track is not None
    assert outcome.track.title == "Stale Leftover"
    assert voice.played == ["https://cdn.example/stale"]
    status = await player.status()
    assert status.now_playing.title == "Stale Leftover"
    assert [t.title for t in status.queue] == ["Track A"]


async def test_play_returns_nothing_found_when_the_source_cannot_resolve() -> None:
    player, voice, _session = _player({})

    outcome = await player.play(query_or_url="silence", requested_by="miles", added_at=T0)

    assert outcome.status == "nothing_found"
    assert voice.played == []
    status = await player.status()
    assert status.now_playing is None
    assert status.queue == []


async def test_skip_advances_to_the_next_queued_track_without_an_explicit_stop() -> None:
    """`voice.play()` for the next track (inside `_advance()`) is what
    replaces the skipped one -- a crossfade if configured, otherwise
    `DiscordVoiceSession`'s own instant live-swap. No separate `voice.
    stop()` call is needed (or made) when there IS a next track."""
    player, voice, _session = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track b", requested_by="miles", added_at=T0)

    skipped = await player.skip()

    assert skipped is not None
    assert skipped.title == "Track A"
    assert voice.stopped == 0
    assert voice.played == ["https://cdn.example/a", "https://cdn.example/b"]
    status = await player.status()
    assert status.now_playing.title == "Track B"
    assert status.queue == []


async def test_skip_with_nothing_queued_stops_and_leaves_nothing_playing() -> None:
    player, voice, _session = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    skipped = await player.skip()

    assert skipped is not None
    assert voice.stopped == 1
    status = await player.status()
    assert status.now_playing is None


async def test_on_track_finished_advances_without_calling_voice_stop() -> None:
    """The real voice client already stopped on its own before this fires —
    `MusicPlayer` must not call `voice.stop()` a second time."""
    player, voice, _session = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track b", requested_by="miles", added_at=T0)

    await player.on_track_finished()

    assert voice.stopped == 0
    status = await player.status()
    assert status.now_playing.title == "Track B"


async def test_pause_returns_false_when_nothing_is_playing() -> None:
    player, voice, _session = _player({})

    assert await player.pause() is False
    assert voice.paused == 0


async def test_pause_then_resume_round_trip_and_call_voice_once_each() -> None:
    player, voice, _session = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    assert await player.pause() is True
    assert voice.paused == 1
    assert (await player.status()).paused is True

    assert await player.resume() is True
    assert voice.resumed == 1
    assert (await player.status()).paused is False


async def test_stop_clears_the_queue_stops_playback_and_disconnects() -> None:
    player, voice, _session = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track b", requested_by="miles", added_at=T0)

    await player.stop()

    assert voice.stopped == 1
    assert voice.disconnected == 1
    status = await player.status()
    assert status.now_playing is None
    assert status.queue == []


async def test_set_volume_applies_immediately_and_reports_true() -> None:
    player, voice, _session = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    assert await player.set_volume(0.5) is True

    assert voice.volume_calls == [0.5]


async def test_set_volume_works_with_nothing_playing() -> None:
    """A setting, not an action on in-progress playback — unlike pause/
    resume, nothing needs to be playing for this to take effect (it
    applies to whatever plays next)."""
    player, voice, _session = _player({})

    assert await player.set_volume(0.5) is True

    assert voice.volume_calls == [0.5]


async def test_set_volume_refuses_a_value_outside_zero_to_two() -> None:
    player, voice, _session = _player({})

    assert await player.set_volume(-0.1) is False
    assert await player.set_volume(2.1) is False

    assert voice.volume_calls == []


async def test_set_volume_accepts_the_boundary_values() -> None:
    player, voice, _session = _player({})

    assert await player.set_volume(0.0) is True
    assert await player.set_volume(2.0) is True

    assert voice.volume_calls == [0.0, 2.0]


async def test_a_playlist_resolve_enqueues_every_track_and_starts_the_first() -> None:
    player, voice, _session = _player({"playlist url": (_TRACK_A, _TRACK_B)})

    outcome = await player.play(
        query_or_url="playlist url", requested_by="miles", added_at=T0,
    )

    assert outcome.status == "playing"
    assert outcome.queued_count == 2
    assert outcome.track is not None
    assert outcome.track.title == "Track A"
    assert voice.played == ["https://cdn.example/a"]
    status = await player.status()
    assert status.now_playing is not None
    assert status.now_playing.title == "Track A"
    assert [t.title for t in status.queue] == ["Track B"]


async def test_a_playlist_resolve_while_something_is_playing_only_enqueues_all() -> None:
    player, voice, _session = _player({
        "track a": _TRACK_A, "playlist url": (_TRACK_B, _TRACK_B),
    })
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    outcome = await player.play(
        query_or_url="playlist url", requested_by="miles", added_at=T0,
    )

    assert outcome.status == "queued"
    assert outcome.queued_count == 2
    assert voice.played == ["https://cdn.example/a"]
    status = await player.status()
    assert [t.title for t in status.queue] == ["Track B", "Track B"]


async def test_advance_re_resolves_the_locator_rather_than_reusing_a_cached_stream() -> None:
    """The lazy-resolve contract (`nora/domain/ports.py`): `QueuedTrack.url`
    is a locator, not a cached stream URL, so `_advance` must hand it back
    into `resolve()` to get something actually playable."""
    player, voice, _session = _player({"track a": _TRACK_A})
    source = player._source  # type: ignore[attr-defined]

    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    assert source.resolved == ["track a", "locator-a"]
    assert voice.played == ["https://cdn.example/a"]


async def test_a_track_that_no_longer_resolves_by_play_time_is_skipped() -> None:
    """A queued track can go stale between enqueue and play time (deleted,
    region-locked, a dead playlist entry) -- `_advance` must move on to the
    next track rather than handing `voice.play` nothing."""
    player, voice, _session = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track b", requested_by="miles", added_at=T0)
    # `track a` (now playing) is about to vanish by the time it's re-resolved
    # on skip; `track b` is still there.
    del player._source._tracks["locator-a"]  # type: ignore[attr-defined]

    skipped = await player.skip()

    assert skipped is not None and skipped.title == "Track A"
    assert voice.played == ["https://cdn.example/a", "https://cdn.example/b"]
    status = await player.status()
    assert status.now_playing is not None
    assert status.now_playing.title == "Track B"
    assert status.queue == []


async def test_play_persists_a_session_row_for_a_restart_to_read_back() -> None:
    player, _voice, session = _player({"track a": _TRACK_A})

    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    row = await session.get("guild-1")
    assert row is not None
    assert row.guild_id == "guild-1"
    assert row.voice_channel_id == "voice-channel-1"
    assert row.notify_channel_id == "notify-channel-1"
    assert row.now_playing_title == "Track A"
    assert row.now_playing_locator == "locator-a"
    assert row.now_playing_source == "youtube"
    assert row.paused is False
    assert row.volume == 1.0


async def test_pause_and_resume_update_the_persisted_session() -> None:
    player, _voice, session = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    await player.pause()
    assert (await session.get("guild-1")).paused is True  # type: ignore[union-attr]

    await player.resume()
    assert (await session.get("guild-1")).paused is False  # type: ignore[union-attr]


async def test_set_volume_updates_the_persisted_session() -> None:
    player, _voice, session = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    await player.set_volume(0.5)

    row = await session.get("guild-1")
    assert row is not None
    assert row.volume == 0.5


async def test_advancing_to_an_empty_queue_persists_no_now_playing_but_keeps_the_row() -> None:
    """The session isn't OVER just because nothing's playing right now --
    the voice connection is still live, waiting on the idle timer. Only
    `stop()` actually ends the session."""
    player, _voice, session = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    await player.skip()  # nothing queued -> now_playing goes back to None

    row = await session.get("guild-1")
    assert row is not None
    assert row.now_playing_title is None
    assert row.now_playing_locator is None
    assert row.now_playing_source is None


async def test_stop_deletes_the_persisted_session_row() -> None:
    player, _voice, session = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    await player.stop()

    assert await session.get("guild-1") is None


async def test_resume_from_persisted_restarts_the_track_from_the_top() -> None:
    """Boot-time resume (design/discord-music/06-session-persistence.md
    stage 2) -- never mid-frame, always position 0."""
    player, voice, session = _player({"track a": _TRACK_A})

    resumed = await player.resume_from_persisted(
        source="youtube", locator="locator-a", title="Track A", paused=False, volume=0.7,
    )

    assert resumed is True
    assert voice.played == ["https://cdn.example/a"]
    assert voice.volume_calls == [0.7]
    status = await player.status()
    assert status.now_playing is not None
    assert status.now_playing.title == "Track A"
    row = await session.get("guild-1")
    assert row is not None
    assert row.volume == 0.7
    assert row.paused is False


async def test_resume_from_persisted_reapplies_a_paused_state() -> None:
    player, voice, _session = _player({"track a": _TRACK_A})

    resumed = await player.resume_from_persisted(
        source="youtube", locator="locator-a", title="Track A", paused=True, volume=1.0,
    )

    assert resumed is True
    assert voice.paused == 1
    assert (await player.status()).paused is True


async def test_resume_from_persisted_returns_false_when_the_track_no_longer_resolves() -> None:
    """The persisted track vanished (deleted, region-locked) since it was
    last playing -- the manager falls back to whatever the ACTUAL queue
    has next, same as a stale queued track already does in `_advance`."""
    player, voice, _session = _player({})

    resumed = await player.resume_from_persisted(
        source="youtube", locator="locator-gone", title="Gone", paused=False, volume=1.0,
    )

    assert resumed is False
    assert voice.played == []
    assert (await player.status()).now_playing is None
