"""`MusicPlayerManager` — per-guild lookup, join-or-reject, and idle/empty
-channel disconnect (design/discord-music/03-state-and-persistence.md).

`connect` is a zero-arg async factory the CALLER (the not-independently-
testable slash-command layer) is responsible for building from a real
`discord.VoiceChannel.connect()` + `DiscordVoiceSession(...)` — `None` means
"the requester is not in any voice channel", the rejection trigger.

The idle timer uses an injected `spawn`/`clock_sleep` pair rather than real
`asyncio.sleep`, so a test proves the SCHEDULING logic (cancel-and-replace,
never double-scheduled) without waiting on a real clock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

from nora.domain.ports import ResolvedTrack
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.testing.music import FakeMusicQueueStore

T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

_StubEntry = ResolvedTrack | Sequence[ResolvedTrack] | None


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
    def __init__(self, tracks: dict[str, _StubEntry]) -> None:
        self._tracks = _with_locators_registered(tracks)

    async def resolve(self, *, query_or_url: str) -> Sequence[ResolvedTrack]:
        return _as_sequence(self._tracks.get(query_or_url))


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
    def __init__(self) -> None:
        self.played: list[str] = []
        self.stopped = 0
        self.disconnected = 0
        self.volume_calls: list[float] = []

    def is_playing(self) -> bool:
        return bool(self.played) and self.stopped == 0

    def play(self, stream_url: str, *, duration_seconds: float | None = None) -> None:
        self.played.append(stream_url)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped += 1

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


def _manager(tracks: dict[str, _StubEntry]) -> MusicPlayerManager:
    scheduler = _FakeScheduler()
    return MusicPlayerManager(
        queue_store=FakeMusicQueueStore(), track_source=_StubSource(tracks),
        idle_timeout_seconds=300.0, spawn=asyncio.ensure_future,
        clock_sleep=scheduler.clock_sleep,
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
    )
    # Second call: no connect factory (requester not currently in voice),
    # but a player already exists for this guild -- must not be rejected.
    outcome = await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )

    assert outcome is not None
    assert outcome.status == "queued"


async def test_queue_emptying_schedules_an_idle_disconnect() -> None:
    manager = _manager({"track a": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
    )
    await manager.on_track_finished("guild-1")  # queue was empty -> now_playing is None

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert session.disconnected == 1
    assert await manager.status("guild-1") is None


async def test_a_new_track_before_the_idle_timeout_cancels_the_pending_disconnect() -> None:
    manager = _manager({"track a": _TRACK_A, "track b": _TRACK_A})
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
    )
    await manager.on_track_finished("guild-1")  # schedules the idle disconnect
    await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )

    await asyncio.sleep(0)
    await asyncio.sleep(0)

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
    )

    await manager.on_voice_channel_emptied("guild-1")

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
    ))
    task2 = asyncio.ensure_future(manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=slow_connect,
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
        queue_store=FakeMusicQueueStore(), track_source=source,
        idle_timeout_seconds=0.0, spawn=asyncio.ensure_future, clock_sleep=asyncio.sleep,
    )
    session = _FakeVoiceSession()

    async def connect() -> _FakeVoiceSession:
        return session

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect,
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

    assert session.disconnected == 0, "the idle timer fired despite reuse cancelling it"

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
    )

    assert await manager.set_volume("guild-1", 0.5) is True
    assert session.volume_calls == [0.5]

    assert await manager.set_volume("guild-1", 3.0) is False


async def test_the_first_play_does_not_notify_the_channel_that_started_it() -> None:
    """The slash command's own reply already says "Now playing" -- a
    separate notification for the very same transition would double-post."""
    manager = _manager({"track a": _TRACK_A})
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect, notify=notify,
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
        added_at=T0, connect=connect, notify=notify,
    )
    await manager.play(
        guild_id="guild-1", query_or_url="track b", requested_by="miles",
        added_at=T0, connect=None,
    )

    await manager.on_track_finished("guild-1")

    assert notify.messages == ["Now playing: Track B — locator-b"]


async def test_skip_notifies_what_is_now_playing() -> None:
    manager = _manager({"track a": _TRACK_A, "track b": _TRACK_B})
    notify = _RecordingNotify()

    async def connect() -> _FakeVoiceSession:
        return _FakeVoiceSession()

    await manager.play(
        guild_id="guild-1", query_or_url="track a", requested_by="miles",
        added_at=T0, connect=connect, notify=notify,
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
        added_at=T0, connect=connect, notify=notify,
    )

    await manager.on_track_finished("guild-1")

    assert notify.messages == []


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
        added_at=T0, connect=connect, notify=notify,
    )
    await manager.stop("guild-1")

    # A stale callback firing after the session ended (shouldn't happen in
    # practice, since the player is gone) must still not notify.
    await manager.on_track_finished("guild-1")

    assert notify.messages == []
