"""`MusicPlayer` — the per-guild state machine. Exercised against a
fake `VoiceSession` that reproduces the one behaviour that matters for
`skip()`'s correctness: `stop()` does NOT itself invoke `on_track_finished`
(that is the real `discord.py` voice client's job, via its own `after`
callback and the Discord integration layer's scheduling — out of scope
here).

`_StubSource` is a throwaway, test-file-local `TrackSource` double — not a
`nora.testing` fake — because `MusicPlayer`'s own logic is what is under
test here, not `TrackSource`'s fidelity to the real yt-dlp boundary
(that's `test_ytdlp_source.py`'s job, and `YtDlpSource` is the
`TrackSource` port's only real, contract-suite-eligible implementation).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from nora.domain.music import AUTOPLAY_PERSON_ID, QueuedTrack
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

    def __init__(
        self, tracks: dict[str, _StubEntry], *,
        related: dict[str, Sequence[ResolvedTrack]] | None = None,
    ) -> None:
        self._tracks: dict[str, _StubEntry] = dict(tracks)
        for entry in tracks.values():
            for track in ((entry,) if isinstance(entry, ResolvedTrack) else entry or ()):
                self._tracks.setdefault(track.locator, track)
        self._related = dict(related or {})
        self.resolved: list[str] = []
        self.related_calls: list[str] = []
        self.refreshed = 0

    async def resolve(self, *, query_or_url: str) -> Sequence[ResolvedTrack]:
        self.resolved.append(query_or_url)
        found = self._tracks.get(query_or_url)
        if found is None:
            return ()
        if isinstance(found, ResolvedTrack):
            return (found,)
        return found

    async def related(self, *, locator: str) -> Sequence[ResolvedTrack]:
        self.related_calls.append(locator)
        return self._related.get(locator, ())

    async def refresh_session(self) -> None:
        # Counted, not ignored: whether the stale-stream retry discards the
        # source session BEFORE re-resolving is the whole difference between
        # a retry and a second helping of the same broken answer.
        self.refreshed += 1


class _FakeVoiceSession:
    """The awaitable `VoiceSession` port —
    every verb is a coroutine, matching the real `HttpVoiceSession` over
    the audio-controller's HTTP control plane."""

    def __init__(self, *, fail_on_play: bool = False) -> None:
        self.played: list[str] = []
        self.stopped = 0
        self.paused = 0
        self.resumed = 0
        self.disconnected = 0
        self.volume_calls: list[float] = []
        self._playing = False
        self._fail_on_play = fail_on_play

    async def play(
        self, stream_url: str, *,
        duration_seconds: float | None = None, resumable: bool | None = None,
    ) -> None:
        if self._fail_on_play:
            # Stands in for the controller reporting the connection dead
            # (HTTP 409/unavailable): a dropped voice connection makes every
            # subsequent advance raise.
            raise RuntimeError("Not connected to voice.")
        self.played.append(stream_url)
        self._playing = True

    async def pause(self) -> None:
        self.paused += 1

    async def resume(self) -> None:
        self.resumed += 1

    async def stop(self) -> None:
        self.stopped += 1
        self._playing = False

    async def set_volume(self, volume: float) -> None:
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
_TRACK_C = ResolvedTrack(
    title="Track C", stream_url="https://cdn.example/c", duration_seconds=210,
    source="youtube", source_id="c", locator="locator-c",
)


def _player(
    tracks: dict[str, _StubEntry], *, autoplay_enabled: bool = False,
    autoplay_prewarm_seconds: float = 0.0,
    loop: asyncio.AbstractEventLoop | None = None,
) -> tuple[MusicPlayer, _FakeVoiceSession, FakeMusicSessionStore]:
    voice = _FakeVoiceSession()
    session = FakeMusicSessionStore()
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(),
        source=_StubSource(tracks), voice=voice, session=session,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
        autoplay_enabled=autoplay_enabled,
        autoplay_prewarm_seconds=autoplay_prewarm_seconds,
        loop=loop,
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
    """A guild with a leftover queue row from an earlier, ended session:
    `play()`'s own reporting must never assume `queued[0]` is what's playing.
    `_advance()` pops the FRONT of the queue, which is
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
    replaces the skipped one — the controller's `/music/play` is
    replace-not-enqueue. No separate `voice.
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


async def test_top_moves_a_later_queued_track_to_play_next() -> None:
    player, voice, _session = _player(
        {"track a": _TRACK_A, "track b": _TRACK_B, "track c": _TRACK_C},
    )
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track b", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track c", requested_by="miles", added_at=T0)

    moved = await player.top(2)

    assert moved is not None
    assert moved.title == "Track C"
    status = await player.status()
    assert [t.title for t in status.queue] == ["Track C", "Track B"]
    # Reordering the upcoming queue must not touch what's already playing.
    assert status.now_playing.title == "Track A"
    assert voice.stopped == 0


async def test_top_out_of_range_returns_none_and_leaves_the_queue_untouched() -> None:
    player, _voice, _session = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track b", requested_by="miles", added_at=T0)

    assert await player.top(0) is None
    assert await player.top(5) is None
    status = await player.status()
    assert [t.title for t in status.queue] == ["Track B"]


async def test_autoplay_enqueues_and_plays_a_related_track_when_the_queue_empties() -> None:
    voice = _FakeVoiceSession()
    session = FakeMusicSessionStore()
    source = _StubSource(
        # `_TRACK_B` here (not just in `related=`) is what makes it
        # LAZY-RESOLVABLE by its own locator, once autoplay enqueues it and
        # `_advance` re-resolves it right before playing -- same contract
        # every ordinary queued track already has.
        {"track a": _TRACK_A, "locator-b": _TRACK_B}, related={"locator-a": (_TRACK_B,)},
    )
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(), source=source, voice=voice,
        session=session, voice_channel_id="voice-channel-1",
        notify_channel_id="notify-channel-1", autoplay_enabled=True,
    )
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    await player.on_track_finished()

    status = await player.status()
    assert status.now_playing is not None
    assert status.now_playing.title == "Track B"
    assert status.now_playing.added_by_person_id == AUTOPLAY_PERSON_ID
    assert source.related_calls == ["locator-a"]
    assert voice.played == ["https://cdn.example/a", "https://cdn.example/b"]


async def test_autoplay_disabled_goes_idle_exactly_like_before() -> None:
    voice = _FakeVoiceSession()
    session = FakeMusicSessionStore()
    source = _StubSource(
        {"track a": _TRACK_A}, related={"locator-a": (_TRACK_B,)},
    )
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(), source=source, voice=voice,
        session=session, voice_channel_id="voice-channel-1",
        notify_channel_id="notify-channel-1", autoplay_enabled=False,
    )
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    await player.on_track_finished()

    status = await player.status()
    assert status.now_playing is None
    assert source.related_calls == []


async def test_autoplay_with_nothing_related_falls_back_to_idle() -> None:
    """`related()` answering empty is the ordinary "nothing to offer"
    outcome, not an error -- must not loop or crash, just fall through to
    the same idle behaviour a disabled autoplay already has."""
    player, voice, _session = _player({"track a": _TRACK_A}, autoplay_enabled=True)
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    await player.on_track_finished()

    status = await player.status()
    assert status.now_playing is None
    assert voice.stopped == 0  # on_track_finished never calls voice.stop() itself


async def test_autoplay_never_seeds_from_nothing_on_the_very_first_track() -> None:
    """The first `/music play` in a session has no PREVIOUS track to seed
    from -- autoplay must not fire off whatever `related()` happens to
    return for an empty/undefined locator."""
    source = _StubSource({"track a": _TRACK_A})
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(), source=source, voice=_FakeVoiceSession(),
        session=FakeMusicSessionStore(), voice_channel_id="voice-channel-1",
        notify_channel_id="notify-channel-1", autoplay_enabled=True,
    )

    outcome = await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    assert outcome.status == "playing"
    assert source.related_calls == []


async def test_autoplay_skips_tracks_already_played_this_session() -> None:
    """Played-history dedup: a Mix
    only excludes its own seed, so a chain that keeps reseeding from the
    latest pick drifts into the same few tracks. Once a track has played
    this session, autoplay must skip it and take the next fresh one from
    the Mix instead."""
    voice = _FakeVoiceSession()
    session = FakeMusicSessionStore()
    source = _StubSource(
        {
            "track a": _TRACK_A,
            "locator-b": _TRACK_B,
            "locator-c": _TRACK_C,
        },
        related={
            # Both B and C offered after A. B is skipped (already played)
            # so C plays next.
            "locator-a": (_TRACK_B, _TRACK_C),
            # After B, its Mix offers C: fresh, picked.
            "locator-b": (_TRACK_C,),
            # After C, the Mix offers C's own seed plus B and A -- all
            # already played -- so autoplay falls to idle.
            "locator-c": (_TRACK_C, _TRACK_B, _TRACK_A),
        },
    )
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(), source=source, voice=voice,
        session=session, voice_channel_id="voice-channel-1",
        notify_channel_id="notify-channel-1", autoplay_enabled=True,
    )
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    # A finishes -> B and C offered; B (locator-b) already played? No --
    # A played, B is fresh. B is picked.
    await player.on_track_finished()
    status = await player.status()
    assert status.now_playing is not None
    assert status.now_playing.title == "Track B"
    # B finishes -> related(B) yields (C,): C fresh, picked.
    await player.on_track_finished()
    status = await player.status()
    assert status.now_playing is not None
    assert status.now_playing.title == "Track C"
    # C finishes -> its Mix is all repeats -> idle, no fourth track.
    await player.on_track_finished()

    status = await player.status()
    assert status.now_playing is None
    assert voice.played == [
        "https://cdn.example/a", "https://cdn.example/b", "https://cdn.example/c",
    ]


async def test_autoplay_with_an_all_repeats_mix_falls_back_to_idle() -> None:
    """A Mix whose every entry has already played this session offers
    nothing new -- must fall through to the same idle behaviour as an
    empty one, never retried and never substituted with a guess."""
    source = _StubSource(
        {"track a": _TRACK_A, "locator-b": _TRACK_B},
        related={"locator-a": (_TRACK_B,), "locator-b": (_TRACK_A,)},
    )
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(), source=source, voice=_FakeVoiceSession(),
        session=FakeMusicSessionStore(), voice_channel_id="voice-channel-1",
        notify_channel_id="notify-channel-1", autoplay_enabled=True,
    )
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    # A -> autoplay B (fresh) -> B's Mix has only A (already played) -> idle.
    await player.on_track_finished()
    await player.on_track_finished()

    status = await player.status()
    assert status.now_playing is None


async def test_user_play_of_the_same_track_twice_is_not_deduped() -> None:
    """Dedup is autoplay's pick selection only: the played-history set
    never filters what a PERSON explicitly queues. `related()` (and the
    autoplay path) is the only reader of `_played_locators`; an ordinary
    `/music play` resolving to a track that already played still plays."""
    player, voice, _session = _player({"track a": _TRACK_A})

    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.on_track_finished()  # A finished; no autoplay configured
    second = await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    assert second.status == "playing"
    assert voice.played == ["https://cdn.example/a", "https://cdn.example/a"]


async def test_resumed_persisted_track_occupies_a_dedup_slot() -> None:
    """`resume_from_persisted` restarts the interrupted track from
    position 0 -- it HAS played this session, so the first autoplay pick
    after a restart must not be able to repeat it."""
    session = FakeMusicSessionStore()
    source = _StubSource(
        {"locator-a": _TRACK_A, "locator-b": _TRACK_B},
        related={"locator-a": (_TRACK_A, _TRACK_B)},
    )
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(), source=source, voice=_FakeVoiceSession(),
        session=session, voice_channel_id="voice-channel-1",
        notify_channel_id="notify-channel-1", autoplay_enabled=True,
    )

    resumed = await player.resume_from_persisted(
        source="youtube", locator="locator-a", title="Track A", paused=False, volume=1.0,
    )
    assert resumed is True
    # A -> autoplay: Mix offers A (already resumed) and B (fresh) -> B.
    await player.on_track_finished()

    status = await player.status()
    assert status.now_playing is not None
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


async def test_stop_clears_the_queue_stops_playback_and_stays_connected() -> None:
    """END-MUSIC: `/music stop` ends the
    session but must NOT disconnect — the audio-controller keeps the voice
    connection (e.g. for the wake path)."""
    player, voice, _session = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track b", requested_by="miles", added_at=T0)

    await player.stop()

    assert voice.stopped == 1
    assert voice.disconnected == 0
    status = await player.status()
    assert status.now_playing is None
    assert status.queue == []


async def test_leave_ends_the_music_and_disconnects() -> None:
    """LEAVE-VOICE: stop AND destroy the
    connection — what `/discord exit`, an emptied channel, and voice-death
    get."""
    player, voice, _session = _player({"track a": _TRACK_A, "track b": _TRACK_B})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    await player.play(query_or_url="track b", requested_by="miles", added_at=T0)

    await player.leave()

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
    """Boot-time resume -- never mid-frame, always position 0."""
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


async def test_prewarm_resolves_the_next_autoplay_pick_before_the_track_ends() -> None:
    """R-64: with `autoplay_prewarm_seconds` set, the next autoplay pick
    (its Mix lookup AND its playable stream) is resolved while the current
    track is still audible, so `on_track_finished` costs zero network
    calls: the prewarmed stream is played directly."""
    voice = _FakeVoiceSession()
    session = FakeMusicSessionStore()
    source = _StubSource(
        {"track a": _TRACK_A, "locator-b": _TRACK_B}, related={"locator-a": (_TRACK_B,)},
    )
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(), source=source, voice=voice,
        session=session, voice_channel_id="voice-channel-1",
        notify_channel_id="notify-channel-1", autoplay_enabled=True,
        autoplay_prewarm_seconds=20.0, loop=asyncio.get_running_loop(),
    )
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    # The timer would fire 180s in; in a test, drive the prewarm directly.
    # (`_schedule_prewarm` scheduled the timer; `_start_prewarm` is what a
    # real loop calls.)
    assert player._prewarm_timer is not None  # type: ignore[attr-defined]
    player._start_prewarm()  # type: ignore[attr-defined]
    await asyncio.sleep(0)  # let the background task complete
    assert player._prewarmed is not None  # type: ignore[attr-defined]
    assert player._prewarmed.locator == "locator-b"  # type: ignore[attr-defined]
    assert "locator-b" in player._prewarm_streams  # type: ignore[attr-defined]

    await player.on_track_finished()

    status = await player.status()
    assert status.now_playing is not None
    assert status.now_playing.title == "Track B"
    assert voice.played == ["https://cdn.example/a", "https://cdn.example/b"]
    # The track-end advance resolved from the prewarm cache: the second
    # `resolve()` for locator-b was never made.
    assert source.resolved.count("locator-b") == 1


async def test_autoplay_pick_refuses_a_prewarmed_pick_already_played() -> None:
    """R-63 + R-64 together: the prewarmed pick is refused when its locator
    has since played (a skip/user re-queue made it start), and the fresh
    `related()` fallback supplies a deduped pick instead."""
    player, _voice, _session = _player(
        {"locator-a": _TRACK_A, "locator-c": _TRACK_C}, autoplay_enabled=True,
    )
    source = player._source  # type: ignore[attr-defined]
    source._related["locator-a"] = (_TRACK_C,)  # type: ignore[attr-defined]
    player._prewarmed = _TRACK_B  # type: ignore[attr-defined]
    player._played_locators.add("locator-b")  # type: ignore[attr-defined]
    previous = QueuedTrack(
        id=uuid4(), guild_id="guild-1", position=0, source="youtube",
        url="locator-a", title="Track A", added_by_person_id="miles", added_at=T0,
    )

    pick = await player._autoplay_pick(previous)  # type: ignore[attr-defined]

    assert pick is not None
    assert pick.locator == "locator-c"


async def test_a_dead_voice_connection_tears_down_the_session() -> None:
    """A dropped voice websocket makes every `voice.play()` raise
    `Not connected to voice.` mid-`_advance`; that must not escape into the
    `on_finished` handler and strand `_now_playing` claiming a track is
    audible when nothing is. The player must tear the session down (queue + session row
    cleared, voice disconnected) so a fresh `/music play` can join cleanly."""
    voice = _FakeVoiceSession()
    session = FakeMusicSessionStore()
    queue = FakeMusicQueueStore()
    source = _StubSource({"track a": _TRACK_A, "locator-b": _TRACK_B})
    await queue.enqueue(
        guild_id="guild-1", source="youtube", url="locator-b", title="Track B",
        added_by_person_id="miles", added_at=T0,
    )
    player = MusicPlayer(
        guild_id="guild-1", queue=queue, source=source, voice=voice, session=session,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    assert (await session.get("guild-1")) is not None
    # The voice connection dies now: the
    # NEXT advance's `voice.play()` raises.
    voice._fail_on_play = True  # type: ignore[attr-defined]

    await player.on_track_finished()

    status = await player.status()
    assert status.now_playing is None
    assert voice.disconnected == 1
    assert await session.get("guild-1") is None
    assert await queue.queue_for("guild-1") == []
    # The player marks itself dead so
    # `MusicPlayerManager._after_transition` drops it from `_players` --
    # without this the manager would keep the zombie and a `/music play`
    # before the idle timeout would reuse the dead player.
    assert player._session_dead is True  # type: ignore[attr-defined]


async def test_scheduling_a_new_prewarm_cancels_the_previous_timer_and_task() -> None:
    """`_schedule_prewarm` must cancel the previous timer/task before arming the
    new one. Otherwise an old timer fires after a skip/advance, sees `_prewarm_for_locator` already
    updated to the NEW track, spawns a duplicate `_prewarm_pick` for it and
    races to fill `_prewarm_streams` -- a wasted `related()` network call
    per advance."""
    player, _voice, _session = _player(
        {"locator-a": _TRACK_A}, autoplay_enabled=True,
        autoplay_prewarm_seconds=20.0, loop=asyncio.get_running_loop(),
    )
    player._now_playing = QueuedTrack(  # type: ignore[attr-defined]
        id=uuid4(), guild_id="guild-1", position=0, source="youtube",
        url="locator-a", title="Track A", added_by_person_id="miles", added_at=T0,
    )
    player._schedule_prewarm(200.0)  # type: ignore[attr-defined]
    old_timer = player._prewarm_timer  # type: ignore[attr-defined]
    assert old_timer is not None
    assert not old_timer.cancelled()

    # An advance for a second track re-arms prewarm -- the old timer must
    # be cancelled, not left to fire.
    player._now_playing = QueuedTrack(  # type: ignore[attr-defined]
        id=uuid4(), guild_id="guild-1", position=0, source="youtube",
        url="locator-b", title="Track B", added_by_person_id="miles", added_at=T0,
    )
    player._schedule_prewarm(180.0)  # type: ignore[attr-defined]

    assert old_timer.cancelled(), "the earlier prewarm timer must be cancelled"
    assert player._prewarm_timer is not None  # type: ignore[attr-defined]
    player._cancel_prewarm()  # type: ignore[attr-defined]


async def test_an_empty_prewarm_is_retried_once_at_track_end() -> None:
    """R-67: a completed prewarm that answered empty is NOT the
    final answer — the prewarm ran up to `autoplay_prewarm_seconds` BEFORE
    the track ended,
    and a transient YouTube failure at that earlier moment must not
    permanently kill the chain. The track-end `_advance` retries
    `related()` ONCE live; only if THAT also answers empty does the
    chain fall through to idle (R-62's never-retried discipline still
    holds for the live answer — the retry is bounded to one)."""
    source = _StubSource(
        {"track a": _TRACK_A, "locator-b": _TRACK_B},
        related={"locator-a": ()},  # empty Mix: nothing to offer
    )
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(), source=source,
        voice=_FakeVoiceSession(), session=FakeMusicSessionStore(),
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
        autoplay_enabled=True, autoplay_prewarm_seconds=20.0,
        loop=asyncio.get_running_loop(),
    )
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    player._start_prewarm()  # type: ignore[attr-defined]
    await asyncio.sleep(0)  # let the background task complete
    assert player._prewarm_done is True  # type: ignore[attr-defined]
    assert player._prewarmed is None  # type: ignore[attr-defined]

    # Track ends naturally: the empty prewarm IS retried once live.
    related_before = len(source.related_calls)
    await player.on_track_finished()

    assert len(source.related_calls) == related_before + 1
    status = await player.status()
    assert status.now_playing is None  # live retry also empty -> idle


async def test_resume_from_persisted_into_a_dead_voice_marks_the_session_dead() -> None:
    """A boot-time resume whose `voice.play()`
    raises (dead voice connection that reconnect claimed was up) must mark
    the player dead so the manager's `_after_transition` evicts it -- a
    zombie would sit in `_players` and a later `/music play` would reuse
    it instead of joining fresh."""
    voice = _FakeVoiceSession(fail_on_play=True)
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(),
        source=_StubSource({"locator-a": _TRACK_A}), voice=voice,
        session=FakeMusicSessionStore(), voice_channel_id="voice-channel-1",
        notify_channel_id="notify-channel-1",
    )

    resumed = await player.resume_from_persisted(
        source="youtube", locator="locator-a", title="Track A", paused=False, volume=1.0,
    )

    assert resumed is False
    assert player._session_dead is True  # type: ignore[attr-defined]
    assert (await player.status()).now_playing is None


class _FailingDeleteSessionStore(FakeMusicSessionStore):
    async def delete(self, guild_id: str) -> None:
        raise RuntimeError("db down")


async def test_a_failed_session_delete_does_not_block_dead_voice_teardown() -> None:
    """A transient DB failure deleting the
    session row during dead-voice teardown must not prevent `_session_dead`
    from being set -- the manager's `_after_transition` evicts on that
    flag; a stranded row is cleaned up by the next boot-time resume scan."""
    voice = _FakeVoiceSession()
    queue = FakeMusicQueueStore()
    source = _StubSource({"track a": _TRACK_A, "locator-b": _TRACK_B})
    await queue.enqueue(
        guild_id="guild-1", source="youtube", url="locator-b", title="Track B",
        added_by_person_id="miles", added_at=T0,
    )
    player = MusicPlayer(
        guild_id="guild-1", queue=queue, source=source, voice=voice,
        session=_FailingDeleteSessionStore(), voice_channel_id="voice-channel-1",
        notify_channel_id="notify-channel-1",
    )
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    voice._fail_on_play = True  # type: ignore[attr-defined]

    await player.on_track_finished()

    assert player._session_dead is True  # type: ignore[attr-defined]
    assert voice.disconnected == 1


# ── R-88: autoplay re-seed seed (last_played) ──────────────────────────────


async def test_advance_records_last_played_when_a_track_starts() -> None:
    """R-88: a track that actually starts becomes the re-seed seed, and it
    survives the queue emptying (unlike `_now_playing`)."""
    player, _voice, session = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)

    assert player._last_played is not None  # type: ignore[attr-defined]
    assert player._last_played.url == "locator-a"  # type: ignore[attr-defined]
    # Persisted too.
    row = await session.get("guild-1")
    assert row is not None
    assert row.last_played_locator == "locator-a"
    assert row.last_played_title == "Track A"


async def test_last_played_survives_the_queue_emptying() -> None:
    """R-88: when autoplay's chain ends (related() empty), `_now_playing`
    is cleared but `_last_played` remains -- the seed a boot-time resume
    needs."""
    player, _voice, session = _player(
        {"track a": _TRACK_A}, autoplay_enabled=True,
    )
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    # related() answers empty -> chain ends, session goes idle.
    await player.on_track_finished()

    assert (await player.status()).now_playing is None
    assert player._last_played is not None  # type: ignore[attr-defined]
    assert player._last_played.url == "locator-a"  # type: ignore[attr-defined]
    row = await session.get("guild-1")
    assert row is not None
    assert row.now_playing_locator is None
    assert row.last_played_locator == "locator-a"


async def test_advance_seeds_autoplay_from_last_played_when_nothing_playing() -> None:
    """R-88: a boot-time resume that found nothing playing (autoplay's
    chain had already ended) still seeds the chain from `_last_played`."""
    player, voice, _session = _player(
        {"track a": _TRACK_A, "locator-b": _TRACK_B}, autoplay_enabled=True,
    )
    source = player._source  # type: ignore[attr-defined]
    source._related["locator-a"] = (_TRACK_B,)  # type: ignore[attr-defined]
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    # Simulate the boot-time resume path: nothing playing, seed restored.
    player._now_playing = None  # type: ignore[attr-defined]
    player.seed_last_played(source="youtube", locator="locator-a", title="Track A")

    await player.on_track_finished()

    status = await player.status()
    assert status.now_playing is not None
    assert status.now_playing.title == "Track B"
    assert voice.played == ["https://cdn.example/a", "https://cdn.example/b"]


async def test_seed_last_played_does_not_play_or_dedup() -> None:
    """R-88: `seed_last_played` only restores the seed -- it never starts
    playback and never occupies a dedup slot (the seed track has not played
    in THIS session)."""
    player, voice, _session = _player({"track a": _TRACK_A})
    player.seed_last_played(source="youtube", locator="locator-a", title="Track A")

    assert voice.played == []
    assert player._last_played is not None  # type: ignore[attr-defined]
    assert "locator-a" not in player._played_locators  # type: ignore[attr-defined]


async def test_stop_clears_the_last_played_seed() -> None:
    """R-88: a stop ENDS the session -- the re-seed seed dies with it."""
    player, _voice, _session = _player({"track a": _TRACK_A})
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    assert player._last_played is not None  # type: ignore[attr-defined]

    await player.stop()

    assert player._last_played is None  # type: ignore[attr-defined]


async def test_an_empty_prewarm_recovers_when_the_live_retry_has_a_track() -> None:
    """R-67: the prewarm answered empty (transient YouTube failure at
    prewarm time) but the live track-end retry finds a track — the chain
    continues instead of dying silently mid-session."""
    source = _StubSource(
        {"track a": _TRACK_A, "locator-b": _TRACK_B},
        related={"locator-a": ()},  # prewarm sees empty
    )
    player = MusicPlayer(
        guild_id="guild-1", queue=FakeMusicQueueStore(), source=source,
        voice=_FakeVoiceSession(), session=FakeMusicSessionStore(),
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
        autoplay_enabled=True, autoplay_prewarm_seconds=20.0,
        loop=asyncio.get_running_loop(),
    )
    await player.play(query_or_url="track a", requested_by="miles", added_at=T0)
    player._start_prewarm()  # type: ignore[attr-defined]
    await asyncio.sleep(0)  # let the background task complete
    assert player._prewarm_done is True  # type: ignore[attr-defined]
    assert player._prewarmed is None  # type: ignore[attr-defined]

    # The Mix recovers by track end: the live retry finds Track B.
    source._related["locator-a"] = (_TRACK_B,)  # type: ignore[attr-defined]

    await player.on_track_finished()

    status = await player.status()
    assert status.now_playing is not None
    assert status.now_playing.title == "Track B"
