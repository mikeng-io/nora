"""`MusicPlayerManager` — per-guild lookup, join-or-reject, and the
idle/empty-channel teardown policies (design/discord-music/
03-state-and-persistence.md), including the design/voice-channel/07 §10
lifecycle split: end-music (`/music stop`, idle expiry — the connection
STAYS) vs leave-voice (emptied channel, voice-death, `/discord exit` — the
connection is destroyed).

`connect` is a zero-arg async factory the CALLER (the not-independently-
testable slash-command layer) is responsible for building over the
`VoiceControllerHub` — `None` means "the requester is not in any voice
channel", the rejection trigger.

The idle timer uses an injected `spawn`/`clock_sleep` pair rather than real
`asyncio.sleep`, so a test proves the SCHEDULING logic (cancel-and-replace,
never double-scheduled) without waiting on a real clock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

from nora.domain.music import MusicSession
from nora.domain.ports import ResolvedTrack
from nora.platform.discord.music_manager import MusicPlayerManager, PlaybackProbe
from nora.testing.music import FakeMusicQueueStore, FakeMusicSessionStore

T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

_StubEntry = ResolvedTrack | Sequence[ResolvedTrack] | None


def _persisted_session(**kw) -> MusicSession:
    base = {
        "guild_id": "guild-1", "voice_channel_id": "voice-channel-1",
        "notify_channel_id": "notify-channel-1", "now_playing_source": None,
        "now_playing_locator": None, "now_playing_title": None,
        "paused": False, "volume": 1.0, "updated_at": T0,
    }
    base.update(kw)
    return MusicSession(**base)


def _as_sequence(found: _StubEntry) -> Sequence[ResolvedTrack]:
    if found is None:
        return ()
    if isinstance(found, ResolvedTrack):
        return (found,)
    return found


def _with_locators_registered(tracks: dict[str, _StubEntry]) -> dict[str, _StubEntry]:
    """`MusicPlayer._advance` resolves a queued track's `.locator` again
    right before playing it (the lazy-resolve contract, `nora/domain/
    ports.py`) -- every track handed to a stub source must also answer to
    its own locator, or that second lookup misses and the track is treated
    as gone stale."""
    registered = dict(tracks)
    for entry in tracks.values():
        for track in _as_sequence(entry):
            registered.setdefault(track.locator, track)
    return registered


class _StubSource:
    def __init__(
        self, tracks: dict[str, _StubEntry], *,
        related: dict[str, Sequence[ResolvedTrack]] | None = None,
    ) -> None:
        self._tracks = _with_locators_registered(tracks)
        self._related = dict(related or {})

    async def resolve(self, *, query_or_url: str) -> Sequence[ResolvedTrack]:
        return _as_sequence(self._tracks.get(query_or_url))

    async def related(self, *, locator: str) -> Sequence[ResolvedTrack]:
        return self._related.get(locator, ())


class _GatedSource:
    """Resolves normally for every query except `gated_query`, which blocks
    on `gate` first -- lets a test hold a `play()` call open mid-resolve."""

    def __init__(
        self, tracks: dict[str, _StubEntry], *, gated_query: str, gate: asyncio.Event,
    ) -> None:
        self._tracks = _with_locators_registered(tracks)
        self._gated_query = gated_query
        self._gate = gate

    async def resolve(self, *, query_or_url: str) -> Sequence[ResolvedTrack]:
        if query_or_url == self._gated_query:
            await self._gate.wait()
        return _as_sequence(self._tracks.get(query_or_url))


class _FakeVoiceSession:
    """The awaitable `VoiceSession` port (design/voice-channel/07 B4)."""

    def __init__(self) -> None:
        self.played: list[str] = []
        self.stopped = 0
        self.disconnected = 0
        self.volume_calls: list[float] = []

    async def play(self, stream_url: str, *, duration_seconds: float | None = None) -> None:
        self.played.append(stream_url)

    async def pause(self) -> None:
        pass

    async def resume(self) -> None:
        pass

    async def stop(self) -> None:
        self.stopped += 1

    async def set_volume(self, volume: float) -> None:
        self.volume_calls.append(volume)

    async def disconnect(self) -> None:
        self.disconnected += 1


class _FailingVoiceSession(_FakeVoiceSession):
    """Plays `fail_after` tracks, then every later `play()` raises -- the
    live 2026-08-26 shape (a voice connection that dies mid-session while
    nothing surfaces the drop)."""

    def __init__(self, *, fail_after: int = 1) -> None:
        super().__init__()
        self._fail_after = fail_after
        self._play_count = 0

    async def play(self, stream_url: str, *, duration_seconds: float | None = None) -> None:
        self._play_count += 1
        if self._play_count > self._fail_after:
            raise RuntimeError("Not connected to voice.")
        await super().play(stream_url, duration_seconds=duration_seconds)


_TRACK_A = ResolvedTrack(
    title="Track A", stream_url="https://cdn.example/a", duration_seconds=200,
    source="youtube", source_id="a", locator="locator-a",
)
_TRACK_B = ResolvedTrack(
    title="Track B", stream_url="https://cdn.example/b", duration_seconds=180,
    source="youtube", source_id="b", locator="locator-b",
)


class _RecordingNotify:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, text: str) -> None:
        self.messages.append(text)


class _FakeScheduler:
    """`clock_sleep` resolves instantly rather than waiting `idle_timeout_
    seconds` for real -- `spawn` is plain `asyncio.ensure_future`, so a
    scheduled task's cancellation is REAL `asyncio.Task` cancellation
    (which already closes its coroutine correctly) rather than a
    hand-rolled wrapper reimplementing that worse."""

    async def clock_sleep(self, _seconds: float) -> None:
        return None


def _manager(
    tracks: dict[str, _StubEntry], *,
    related: dict[str, Sequence[ResolvedTrack]] | None = None,
    autoplay_enabled: bool = False,
    playback_probe: PlaybackProbe | None = None,
    generation_probe=None,
    drop_voice_session=None,
    exit_voice_session=None,
) -> MusicPlayerManager:
    scheduler = _FakeScheduler()
    return MusicPlayerManager(
        queue_store=FakeMusicQueueStore(), session_store=FakeMusicSessionStore(),
        track_source=_StubSource(tracks, related=related), idle_timeout_seconds=300.0,
        spawn=asyncio.ensure_future, clock_sleep=scheduler.clock_sleep,
        autoplay_enabled=autoplay_enabled, playback_probe=playback_probe,
        generation_probe=generation_probe, drop_voice_session=drop_voice_session,
        exit_voice_session=exit_voice_session,
    )


async def test_r59_play_refused_when_requester_not_in_voice_and_no_player_exists() -> None:
    manager = _manager({"track a": _TRACK_A})

    outcome = await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=None,
    )

    assert outcome is None


async def test_play_joins_via_the_connect_factory_when_no_player_exists() -> None:
    manager = _manager({"track a": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    outcome = await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )

    assert outcome is not None
    assert outcome.status == "playing"
    assert session.played == ["https://cdn.example/a"]


async def test_an_existing_player_is_reused_even_when_connect_is_none() -> None:
    manager = _manager({"track a": _TRACK_A, "track b": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    # Second call: no connect factory (requester not currently in voice),
    # but a player already exists for this guild -- must not be rejected.
    outcome = await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )

    assert outcome is not None
    assert outcome.status == "queued"


async def test_queue_emptying_schedules_an_idle_end_of_music_that_stays_connected() -> None:
    """Idle expiry is END-MUSIC (design/voice-channel/07 §10, B3): the
    session is torn down — but the voice connection STAYS (the
    audio-controller keeps it, e.g. for the wake path)."""
    manager = _manager({"track a": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    await manager.on_track_finished("guild-1")  # queue was empty -> now_playing is None

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert session.stopped == 1
    assert session.disconnected == 0, "idle expiry must NOT leave the voice channel"
    assert await manager.status("guild-1") is None


async def test_a_new_track_before_the_idle_timeout_cancels_the_pending_disconnect() -> None:
    manager = _manager({"track a": _TRACK_A, "track b": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    await manager.on_track_finished("guild-1")  # schedules the idle disconnect
    await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert session.stopped == 0
    assert session.disconnected == 0
    assert (await manager.status("guild-1")) is not None


async def test_r60_a_voice_channel_emptied_of_humans_disconnects_immediately() -> None:
    manager = _manager({"track a": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )

    await manager.on_voice_channel_emptied("guild-1")

    assert session.disconnected == 1
    assert await manager.status("guild-1") is None


async def test_music_stop_is_end_music_and_stays_connected() -> None:
    """`/music stop` ≠ leaving (design/voice-channel/07 §10, B3): the
    session ends, the voice connection STAYS."""
    manager = _manager({"track a": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )

    await manager.stop("guild-1")

    assert session.stopped == 1
    assert session.disconnected == 0, "/music stop must NOT leave the voice channel"
    assert await manager.status("guild-1") is None


async def test_leave_voice_ends_the_music_and_disconnects() -> None:
    """`/discord exit` (design/voice-channel/07 §10, B3): end-music AND
    destroy the connection."""
    manager = _manager({"track a": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )

    await manager.leave_voice("guild-1")

    assert session.stopped == 1
    assert session.disconnected == 1
    assert await manager.status("guild-1") is None


async def test_on_voice_death_ends_the_music_and_disconnects() -> None:
    """The controller's voice-death event is leave-voice (and keeps its
    announce=True behaviour, covered by the `_leave` tests below)."""
    manager = _manager({"track a": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )

    await manager.on_voice_death("guild-1")

    assert session.stopped == 1
    assert session.disconnected == 1
    assert await manager.status("guild-1") is None


async def test_two_concurrent_first_plays_for_a_guild_connect_only_once() -> None:
    """The race a subagent review found: `_get_or_create`'s check-then-act
    ('no existing player, so connect') is not atomic across `await`. Two
    `/music play` calls landing for the same guild before either has
    finished connecting must not both open a voice connection — the loser
    would be silently orphaned (never stopped or disconnected)."""
    manager = _manager({"track a": _TRACK_A})
    gate = asyncio.Event()
    connect_calls = 0

    async def slow_connect() -> _FakeVoiceSession:
        nonlocal connect_calls
        connect_calls += 1
        await gate.wait()
        return _FakeVoiceSession()

    task1 = asyncio.ensure_future(manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=slow_connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    ))
    task2 = asyncio.ensure_future(manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=slow_connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    ))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    gate.set()
    outcome1, outcome2 = await asyncio.gather(task1, task2)

    assert connect_calls == 1
    assert outcome1 is not None
    assert outcome2 is not None


async def test_reusing_an_idle_player_cancels_its_timer_before_the_slow_resolve() -> None:
    """The other race a subagent review found: cancelling the idle timer
    only in `_after_transition`, at the END of `play()`, leaves it running
    across the resolve/enqueue await in between -- long enough for it to
    fire and tear the player down mid-call."""
    gate = asyncio.Event()
    source = _GatedSource(
        {"track a": _TRACK_A, "track b": _TRACK_A}, gated_query="track b", gate=gate,
    )
    manager = MusicPlayerManager(
        # 0.0, not the usual test double: a REAL `asyncio.sleep(0.0)`
        # resolves on close to the very next loop tick, which is what makes
        # this a genuine race against the gated resolve below rather than a
        # timer that could never win regardless of the fix.
        queue_store=FakeMusicQueueStore(), session_store=FakeMusicSessionStore(),
        track_source=source, idle_timeout_seconds=0.0,
        spawn=asyncio.ensure_future, clock_sleep=asyncio.sleep,
    )
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    await manager.on_track_finished("guild-1")  # queue empty -> idle timer scheduled

    play_task = asyncio.ensure_future(manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    ))
    # Let play_task start (reuse the existing player, cancel its idle timer,
    # then block on the gated resolve) and give the idle task every
    # remaining chance to also run.
    for _ in range(5):
        await asyncio.sleep(0)

    assert session.stopped == 0, "the idle timer fired end-music despite reuse cancelling it"
    assert session.disconnected == 0

    gate.set()
    outcome = await play_task

    assert outcome is not None
    assert outcome.status == "playing"


async def test_set_volume_is_none_when_no_player_exists_for_the_guild() -> None:
    manager = _manager({})

    assert await manager.set_volume("guild-1", 0.5) is None


async def test_set_volume_delegates_to_the_player_once_one_exists() -> None:
    manager = _manager({"track a": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )

    assert await manager.set_volume("guild-1", 0.5) is True
    assert session.volume_calls == [0.5]

    assert await manager.set_volume("guild-1", 3.0) is False


async def test_top_is_none_when_no_player_exists_for_the_guild() -> None:
    manager = _manager({})

    assert await manager.top("guild-1", 1) is None


async def test_top_delegates_to_the_player_once_one_exists() -> None:
    manager = _manager({"track a": _TRACK_A, "track b": _TRACK_B})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )

    moved = await manager.top("guild-1", 1)

    assert moved is not None
    assert moved.title == "Track B"
    # Out of range delegates through to the player's own None, not a crash.
    assert await manager.top("guild-1", 99) is None


async def test_the_first_play_does_not_notify_the_channel_that_started_it() -> None:
    """The slash command's own reply already says "Now playing" -- a
    separate notification for the very same transition would double-post."""
    manager = _manager({"track a": _TRACK_A})
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )

    assert notify.messages == []


async def test_a_track_finishing_naturally_notifies_the_channel_that_started_the_session() -> None:
    """The one transition with no slash-command reply of its own -- a track
    ending and the next auto-starting is otherwise silent in Discord."""
    manager = _manager({"track a": _TRACK_A, "track b": _TRACK_B})
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )
    await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )

    await manager.on_track_finished("guild-1")

    assert notify.messages == ["Now playing: Track B — locator-b"]


async def test_autoplay_enqueues_a_related_track_and_notifies_it_as_autoplaying() -> None:
    manager = _manager(
        # `locator-b` here (not just in `related=`) is what makes it
        # LAZY-RESOLVABLE by its own locator, once autoplay enqueues it and
        # `_advance` re-resolves it right before playing.
        {"track a": _TRACK_A, "locator-b": _TRACK_B},
        related={"locator-a": (_TRACK_B,)}, autoplay_enabled=True,
    )
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )

    await manager.on_track_finished("guild-1")

    # Distinct wording from an ordinary auto-advance -- nobody asked for
    # this track, autoplay picked it.
    assert notify.messages == ["Autoplaying: Track B — locator-b"]
    status = await manager.status("guild-1")
    assert status is not None
    assert status.now_playing is not None
    assert status.now_playing.title == "Track B"


async def test_autoplay_disabled_by_default_goes_idle_and_never_calls_related() -> None:
    manager = _manager({"track a": _TRACK_A}, related={"locator-a": (_TRACK_B,)})
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )

    await manager.on_track_finished("guild-1")

    assert notify.messages == []
    status = await manager.status("guild-1")
    assert status is not None
    assert status.now_playing is None


async def test_skip_notifies_what_is_now_playing() -> None:
    manager = _manager({"track a": _TRACK_A, "track b": _TRACK_B})
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )
    await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )

    await manager.skip("guild-1")

    assert notify.messages == ["Now playing: Track B — locator-b"]


async def test_a_track_finishing_with_an_empty_queue_does_not_notify() -> None:
    """Nothing plays next -- there is nothing to announce."""
    manager = _manager({"track a": _TRACK_A})
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )

    await manager.on_track_finished("guild-1")

    assert notify.messages == []


async def test_leave_notifies_the_channel_when_it_tears_a_session_down() -> None:
    """2026-09-15 live: voice death tore the session down with ZERO
    notification — the room only noticed the silence. `_leave` must
    announce the teardown the same way it announces a now-playing line,
    using the session's registered notify closure."""
    manager = _manager({"track a": _TRACK_A})
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )

    await manager._leave("guild-1", announce=True)

    assert notify.messages == [
        "Music stopped — voice connection lost. Use /music play to restart."
    ]


async def test_leave_without_announce_does_not_notify() -> None:
    """`/music stop` replies `Stopped.` itself; an idle timeout is
    expected silence. Only the voice-death path announces — a plain
    `_leave` must stay silent (2026-09-15 live: the old code would have
    double-posted after `/music stop`)."""
    manager = _manager({"track a": _TRACK_A})
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )

    await manager._leave("guild-1")

    assert notify.messages == []


async def test_leave_on_an_already_torn_down_guild_does_not_double_notify() -> None:
    """`_leave` runs idempotently (an emptied channel + an idle timer can
    both fire) — a second call must not double-post the teardown line."""
    manager = _manager({"track a": _TRACK_A})
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )

    await manager._leave("guild-1", announce=True)
    await manager._leave("guild-1", announce=True)

    assert len(notify.messages) == 1


async def test_reusing_a_session_from_another_channel_keeps_the_original_notify() -> None:
    """"The channel where it started" means the FIRST `/music play` for this
    session, not the most recent one -- a later reuse must not move where
    now-playing notifications land."""
    manager = _manager({"track a": _TRACK_A, "track b": _TRACK_B})
    original_channel = _RecordingNotify()
    other_channel = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect, notify=original_channel,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None, notify=other_channel,
    )

    await manager.on_track_finished("guild-1")

    assert other_channel.messages == []
    assert original_channel.messages == ["Now playing: Track B — locator-b"]


async def test_leaving_stops_further_notifications_for_that_guild() -> None:
    manager = _manager({"track a": _TRACK_A})
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )
    await manager.stop("guild-1")

    # A stale callback firing after the session ended (shouldn't happen in
    # practice, since the player is gone) must still not notify.
    await manager.on_track_finished("guild-1")

    assert notify.messages == []


async def test_persisted_sessions_reflects_the_session_store() -> None:
    manager = _manager({"track a": _TRACK_A})
    session_store = manager._session_store  # type: ignore[attr-defined]
    await session_store.upsert(_persisted_session(guild_id="guild-9"))

    sessions = await manager.persisted_sessions()

    assert [s.guild_id for s in sessions] == ["guild-9"]


async def test_discard_persisted_session_removes_it_without_ever_connecting() -> None:
    manager = _manager({"track a": _TRACK_A})
    session_store = manager._session_store  # type: ignore[attr-defined]
    await session_store.upsert(_persisted_session(guild_id="guild-9"))

    await manager.discard_persisted_session("guild-9")

    assert await session_store.get("guild-9") is None


async def test_discard_persisted_session_also_clears_its_orphaned_queue() -> None:
    """Found live 2026-08-24: discarding an unresumable session used to
    leave its `music_queue` rows behind. A guild's next, unrelated `play()`
    call then had `_advance()` pop that stale leftover instead of the
    freshly requested track — while still reporting the new request as
    playing. `MusicPlayer.stop()` already clears the queue on an explicit
    `/music stop`; the boot-time "this session is dead" path needs the
    same symmetry."""
    manager = _manager({"track a": _TRACK_A})
    session_store = manager._session_store  # type: ignore[attr-defined]
    queue_store = manager._queue_store  # type: ignore[attr-defined]
    await session_store.upsert(_persisted_session(guild_id="guild-9"))
    await queue_store.enqueue(
        guild_id="guild-9", source="youtube", url="stale-locator", title="Stale Leftover",
        added_by_person_id="miles", added_at=T0,
    )

    await manager.discard_persisted_session("guild-9")

    assert await queue_store.pop_next("guild-9") is None


async def test_resume_persisted_session_reconnects_and_restarts_the_track() -> None:
    manager = _manager({"track a": _TRACK_A})
    voice = _FakeVoiceSession()
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return voice

    persisted = _persisted_session(
        guild_id="guild-1", now_playing_source="youtube", now_playing_locator="locator-a",
        now_playing_title="Track A", paused=False, volume=1.0,
    )

    await manager.resume_persisted_session(persisted, connect=connect, notify=notify)

    assert voice.played == ["https://cdn.example/a"]
    status = await manager.status("guild-1")
    assert status is not None
    assert status.now_playing is not None
    assert status.now_playing.title == "Track A"
    assert notify.messages == ["Now playing: Track A — locator-a"]


async def test_resume_persisted_session_with_nothing_playing_advances_the_real_queue() -> None:
    """The session was persisted with nothing playing (queue empty at the
    time) -- resume just picks up whatever's queued now, or stays idle."""
    manager = _manager({"track b": _TRACK_B})
    voice = _FakeVoiceSession()
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return voice

    # Simulate a track having been queued for this guild in the meantime.
    await manager._queue_store.enqueue(  # type: ignore[attr-defined]
        guild_id="guild-1", source="youtube", url="locator-b", title="Track B",
        added_by_person_id="miles", added_at=T0,
    )
    persisted = _persisted_session(
        guild_id="guild-1", now_playing_source=None, now_playing_locator=None,
        now_playing_title=None,
    )

    await manager.resume_persisted_session(persisted, connect=connect, notify=notify)

    assert voice.played == ["https://cdn.example/b"]
    assert notify.messages == ["Now playing: Track B — locator-b"]


async def test_resume_persisted_session_never_clobbers_an_already_live_player() -> None:
    manager = _manager({"track a": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    connect_calls = 0

    async def resume_connect() -> _FakeVoiceSession:
        nonlocal connect_calls
        connect_calls += 1
        return _FakeVoiceSession()

    persisted = _persisted_session(guild_id="guild-1")
    await manager.resume_persisted_session(
        persisted, connect=resume_connect, notify=_RecordingNotify(),
    )

    assert connect_calls == 0


async def test_dead_voice_teardown_removes_the_player_from_the_manager() -> None:
    """External audit MAJOR-2 (2026-08-26): when `_advance` finds the voice
    connection dead and tears the session down, the manager must drop the
    player from `_players` -- otherwise the idle timer is armed for a
    zombie and the next `/music play` reuses the dead player and fails
    again."""
    manager = _manager({"track a": _TRACK_A, "track b": _TRACK_B})
    voice = _FailingVoiceSession(fail_after=1)

    async def connect() -> _FakeVoiceSession:
        return voice

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    assert "guild-1" in manager._players  # type: ignore[attr-defined]
    # Queue a second track -- the next advance must PLAY it, and will
    # discover the voice connection is dead.
    queued = await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )
    assert queued is not None and queued.status == "queued"

    await manager.on_track_finished("guild-1")

    assert "guild-1" not in manager._players  # type: ignore[attr-defined]
    assert "guild-1" not in manager._idle_tasks  # type: ignore[attr-defined]

    # A fresh `/music play` joins a NEW connection and works.
    voice2 = _FakeVoiceSession()

    async def connect2() -> _FakeVoiceSession:
        return voice2

    outcome = await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=connect2,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    assert outcome is not None
    assert outcome.status == "playing"
    assert voice2.played == ["https://cdn.example/b"]


async def test_resume_persisted_session_reseeds_autoplay_from_last_played() -> None:
    """R-67: a persisted session with nothing playing (autoplay's chain
    had already ended before the restart) but a last-played track on record
    re-seeds the autoplay chain from it -- the boot-time resume's
    fall-through `_advance` picks a fresh track instead of sitting silent
    in a live voice channel."""
    manager = _manager(
        {"track a": _TRACK_A, "locator-b": _TRACK_B},
        related={"locator-a": (_TRACK_B,)}, autoplay_enabled=True,
    )
    voice = _FakeVoiceSession()
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return voice

    persisted = _persisted_session(
        guild_id="guild-1", now_playing_source=None, now_playing_locator=None,
        now_playing_title=None, last_played_source="youtube",
        last_played_locator="locator-a", last_played_title="Track A",
    )

    await manager.resume_persisted_session(persisted, connect=connect, notify=notify)

    # The chain re-seeded from last_played: Track B autoplayed.
    assert voice.played == ["https://cdn.example/b"]
    status = await manager.status("guild-1")
    assert status is not None
    assert status.now_playing is not None
    assert status.now_playing.title == "Track B"
    # Autoplay picks announce with the autoplay line, not the plain one.
    assert notify.messages == ["Autoplaying: Track B — locator-b"]


async def test_resume_persisted_session_without_last_played_stays_idle() -> None:
    """R-67: no last-played on record (never played a track) means no seed
    to re-seed from -- the resume stays idle, exactly as before."""
    manager = _manager({"track a": _TRACK_A}, autoplay_enabled=True)
    voice = _FakeVoiceSession()
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return voice

    persisted = _persisted_session(
        guild_id="guild-1", now_playing_source=None, now_playing_locator=None,
        now_playing_title=None, last_played_source=None, last_played_locator=None,
        last_played_title=None,
    )

    await manager.resume_persisted_session(persisted, connect=connect, notify=notify)

    assert voice.played == []
    status = await manager.status("guild-1")
    assert status is not None
    assert status.now_playing is None
    assert notify.messages == []


async def test_a_track_end_for_a_superseded_playback_does_not_advance() -> None:
    """B5 (design/voice-channel/07 §3.3): a `track-finished` naming a
    playback the controller has since superseded is the OLD track's tail
    arriving late — the queue must not advance a second time, and the
    check happens UNDER the guild lock against the probe, never outside
    it."""
    manager = _manager(
        {"track a": _TRACK_A, "track b": _TRACK_B},
        playback_probe=lambda _guild_id: "pb-new",
    )
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )
    await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )

    advanced = await manager.on_track_finished("guild-1", playback_id="pb-old")

    assert advanced is False
    status = await manager.status("guild-1")
    assert status is not None
    assert status.now_playing is not None
    assert status.now_playing.title == "Track A"  # the current track survived
    assert [t.title for t in status.queue] == ["Track B"]  # nothing popped
    assert notify.messages == []


async def test_a_track_end_for_the_current_playback_advances() -> None:
    """The same event naming the playback the hub currently expects IS the
    real track end — the queue advances exactly once."""
    manager = _manager(
        {"track a": _TRACK_A, "track b": _TRACK_B},
        playback_probe=lambda _guild_id: "pb-new",
    )
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1", notify=notify,
    )
    await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )

    advanced = await manager.on_track_finished("guild-1", playback_id="pb-new")

    assert advanced is True
    assert notify.messages == ["Now playing: Track B — locator-b"]


async def test_a_track_end_without_a_probe_skips_the_staleness_check() -> None:
    """A wiring that cannot answer the probe (tests, a partial composition)
    must never GUESS at staleness — no probe means the check is skipped and
    the event advances as it always did."""
    manager = _manager({"track a": _TRACK_A, "track b": _TRACK_B})

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )

    advanced = await manager.on_track_finished("guild-1", playback_id="pb-anything")

    assert advanced is True
    status = await manager.status("guild-1")
    assert status is not None
    assert status.now_playing is not None
    assert status.now_playing.title == "Track B"


class _AdoptedVoiceSession(_FakeVoiceSession):
    """A voice session as the hub hands it over after adopting the
    controller's live connection (§12.1): it carries the `playback_id` the
    controller is ACTUALLY playing, and each `play()` mints a fresh one —
    the same shape `HttpVoiceSession` has."""

    def __init__(self, *, playback_id: str | None = None) -> None:
        super().__init__()
        self.playback_id = playback_id
        self._play_count = 0

    async def play(self, stream_url: str, *, duration_seconds: float | None = None) -> None:
        await super().play(stream_url, duration_seconds=duration_seconds)
        self._play_count += 1
        self.playback_id = f"pb-new-{self._play_count}"


class _GatedPauseVoiceSession(_FakeVoiceSession):
    """`pause()`/`set_volume()` block on a gate — the slow HTTP response of
    the B6 probe (pause's answer landing after a concurrent stop)."""

    def __init__(self, gate: asyncio.Event) -> None:
        super().__init__()
        self._gate = gate

    async def pause(self) -> None:
        await self._gate.wait()

    async def set_volume(self, volume: float) -> None:
        await self._gate.wait()
        await super().set_volume(volume)


class _GatedPlayVoiceSession(_FakeVoiceSession):
    """`play()` blocks on a gate — the hung audio-controller of the B14
    probe (a track-finish advance whose POST never answers)."""

    def __init__(self, gate: asyncio.Event) -> None:
        super().__init__()
        self._gate = gate

    async def play(self, stream_url: str, *, duration_seconds: float | None = None) -> None:
        await self._gate.wait()
        await super().play(stream_url, duration_seconds=duration_seconds)


async def test_a_track_finish_landing_during_boot_resume_does_not_lose_the_advanced_track() -> None:
    """B5 (2026-09-16 audit, Codex probe): boot is resuming persisted track
    A when the controller's `track-finished` for the PRE-RESTART playback
    lands. The resume must (a) run entirely under the guild lock, so the
    event waits for it, and (b) reconcile the LIVE playback first — the
    controller is already playing A on the adopted connection, so A is
    adopted WITHOUT a replay and the event advances the real queue to B.
    The pre-fix interleave replayed A OVER the already-advanced B, and B
    was popped from the queue — lost outright."""
    manager = _manager(
        {"track a": _TRACK_A, "track b": _TRACK_B},
    )
    voice = _AdoptedVoiceSession(playback_id="pb-live")
    manager._playback_probe = lambda _guild_id: voice.playback_id  # type: ignore[attr-defined]
    notify = _RecordingNotify()

    async def connect() -> _AdoptedVoiceSession:
        return voice

    await manager._queue_store.enqueue(  # type: ignore[attr-defined]
        guild_id="guild-1", source="youtube", url="locator-b", title="Track B",
        added_by_person_id="miles", added_at=T0,
    )
    persisted = _persisted_session(
        guild_id="guild-1", now_playing_source="youtube",
        now_playing_locator="locator-a", now_playing_title="Track A",
    )

    resume_task = asyncio.ensure_future(
        manager.resume_persisted_session(persisted, connect=connect, notify=notify)
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # The controller's track-finished for the pre-restart playback of A.
    finish_task = asyncio.ensure_future(
        manager.on_track_finished("guild-1", playback_id="pb-live")
    )
    await asyncio.gather(resume_task, finish_task)

    assert voice.played == ["https://cdn.example/b"], (
        "A must be ADOPTED, not replayed over the live connection — and the "
        "finish event must advance the real queue to B, exactly once"
    )
    status = await manager.status("guild-1")
    assert status is not None
    assert status.now_playing is not None
    assert status.now_playing.title == "Track B"
    assert [t.title for t in status.queue] == []


async def test_a_pause_response_landing_after_stop_does_not_recreate_the_session() -> None:
    """B6 (2026-09-16 audit): pause waited for its HTTP response, stop
    deleted the session, and pause's `_persist_session()` then RECREATED
    it. Pause now runs under the same guild lock as stop, so the persist
    lands before the delete (arrival order), never after."""
    gate = asyncio.Event()
    manager = _manager({"track a": _TRACK_A})
    voice = _GatedPauseVoiceSession(gate)

    async def connect() -> _FakeVoiceSession:
        return voice

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    session_store = manager._session_store  # type: ignore[attr-defined]
    assert await session_store.get("guild-1") is not None

    pause_task = asyncio.ensure_future(manager.pause("guild-1"))
    for _ in range(3):
        await asyncio.sleep(0)  # let pause reach its gated HTTP response
    stop_task = asyncio.ensure_future(manager.stop("guild-1"))
    for _ in range(3):
        await asyncio.sleep(0)  # pre-fix: stop completes underneath the pause
    gate.set()
    paused, _ = await asyncio.gather(pause_task, stop_task)

    assert paused is True
    assert await session_store.get("guild-1") is None, (
        "the stop's delete must be the LAST write — a pause persist landing "
        "after it resurrects the session row"
    )
    assert await manager.status("guild-1") is None


async def test_a_volume_response_landing_after_stop_does_not_recreate_the_session() -> None:
    """B6, the volume verb: same resurrection shape as pause."""
    gate = asyncio.Event()
    manager = _manager({"track a": _TRACK_A})
    voice = _GatedPauseVoiceSession(gate)

    async def connect() -> _FakeVoiceSession:
        return voice

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    session_store = manager._session_store  # type: ignore[attr-defined]

    volume_task = asyncio.ensure_future(manager.set_volume("guild-1", 0.5))
    for _ in range(3):
        await asyncio.sleep(0)
    stop_task = asyncio.ensure_future(manager.stop("guild-1"))
    for _ in range(3):
        await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(volume_task, stop_task)

    assert await session_store.get("guild-1") is None


async def test_a_delayed_voice_death_from_an_old_generation_touches_nothing() -> None:
    """B8 (2026-09-16 audit): an outbox `voice-death` delayed across an
    exit/rejoin names the OLD generation. Compared against the live one
    UNDER the guild lock, it is ignored — the NEW session keeps playing,
    and the hub's cached session is not dropped."""
    dropped: list[str] = []
    manager = _manager(
        {"track a": _TRACK_A},
        generation_probe=lambda _guild_id: "gen-2",
        drop_voice_session=dropped.append,
    )
    voice = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return voice

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )

    await manager.on_voice_death("guild-1", generation="gen-1")

    assert dropped == []
    assert voice.disconnected == 0
    status = await manager.status("guild-1")
    assert status is not None and status.now_playing is not None

    # Positive control: the CURRENT generation's death DOES tear down
    # (and drops the hub session inside the lock).
    await manager.on_voice_death("guild-1", generation="gen-2")

    assert dropped == ["guild-1"]
    assert voice.disconnected == 1
    assert await manager.status("guild-1") is None


async def test_a_delayed_channel_emptied_for_an_old_generation_or_channel_is_ignored() -> None:
    """B8: `channel-emptied` validates BOTH the generation and the channel
    under the lock before the leave-voice teardown."""
    dropped: list[str] = []
    manager = _manager(
        {"track a": _TRACK_A},
        generation_probe=lambda _guild_id: "gen-2",
        drop_voice_session=dropped.append,
    )
    voice = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return voice

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )

    await manager.on_voice_channel_emptied(
        "guild-1", generation="gen-1", channel_id="voice-channel-1",
    )
    await manager.on_voice_channel_emptied(
        "guild-1", generation="gen-2", channel_id="cortez-elsewhere",
    )

    assert dropped == []
    assert voice.disconnected == 0
    assert (await manager.status("guild-1")) is not None

    # Positive control: the live generation AND channel DO tear down.
    await manager.on_voice_channel_emptied(
        "guild-1", generation="gen-2", channel_id="voice-channel-1",
    )

    assert dropped == ["guild-1"]
    assert voice.disconnected == 1
    assert await manager.status("guild-1") is None


async def test_a_hung_play_does_not_block_stop_for_the_guild() -> None:
    """B14 (2026-09-16 audit): the track-finish advance used to hold the
    guild lock across the whole play POST — a hanging audio-controller
    blocked `/music stop`, `/music skip` and `/discord exit` behind its
    30s timeout. The POST now runs with the lock RELEASED: stop completes
    promptly, and when the hung play finally lands the finish step's
    zombie guard kills the audio nora already stopped tracking."""
    gate = asyncio.Event()
    gate.set()  # the FIRST play (track A) goes through
    manager = _manager({"track a": _TRACK_A, "track b": _TRACK_B})
    voice = _GatedPlayVoiceSession(gate)

    async def connect() -> _FakeVoiceSession:
        return voice

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
        voice_channel_id="voice-channel-1", notify_channel_id="notify-channel-1",
    )
    await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )
    gate.clear()  # the controller now hangs on play

    finish_task = asyncio.ensure_future(manager.on_track_finished("guild-1"))
    for _ in range(5):
        await asyncio.sleep(0)  # prepare (pop B) done, the play POST in flight

    stop_task = asyncio.ensure_future(manager.stop("guild-1"))
    # Times out (and fails the test) if stop waits on the hung play POST.
    await asyncio.wait_for(stop_task, timeout=1.0)
    assert await manager.status("guild-1") is None
    assert voice.stopped == 1

    gate.set()
    await asyncio.wait_for(finish_task, timeout=1.0)

    assert voice.stopped == 2, (
        "the play landed AFTER the stop — the finish step must kill the "
        "just-started audio (nothing supersedes it: replace semantics only "
        "cover a NEWER play)"
    )
