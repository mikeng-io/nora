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
from datetime import UTC, datetime

from nora.domain.ports import ResolvedTrack
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.testing.music import FakeMusicQueueStore

T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


class _StubSource:
    def __init__(self, tracks: dict[str, ResolvedTrack | None]) -> None:
        self._tracks = tracks

    async def resolve(self, *, query_or_url: str) -> ResolvedTrack | None:
        return self._tracks.get(query_or_url)


class _FakeVoiceSession:
    def __init__(self) -> None:
        self.played: list[str] = []
        self.stopped = 0
        self.disconnected = 0

    def is_playing(self) -> bool:
        return bool(self.played) and self.stopped == 0

    def play(self, stream_url: str) -> None:
        self.played.append(stream_url)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped += 1

    async def disconnect(self) -> None:
        self.disconnected += 1


_TRACK_A = ResolvedTrack(
    title="Track A", stream_url="https://cdn.example/a", duration_seconds=200,
    source="youtube", source_id="a",
)


class _FakeScheduler:
    """`clock_sleep` resolves instantly rather than waiting `idle_timeout_
    seconds` for real -- `spawn` is plain `asyncio.ensure_future`, so a
    scheduled task's cancellation is REAL `asyncio.Task` cancellation
    (which already closes its coroutine correctly) rather than a
    hand-rolled wrapper reimplementing that worse."""

    async def clock_sleep(self, _seconds: float) -> None:
        return None


def _manager(tracks: dict[str, ResolvedTrack | None]) -> MusicPlayerManager:
    scheduler = _FakeScheduler()
    return MusicPlayerManager(
        queue_store=FakeMusicQueueStore(), track_source=_StubSource(tracks),
        idle_timeout_seconds=300.0, spawn=asyncio.ensure_future,
        clock_sleep=scheduler.clock_sleep,
    )


async def test_play_is_rejected_when_the_requester_is_not_in_voice_and_no_player_exists() -> None:
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


async def test_on_voice_channel_emptied_leaves_immediately_no_idle_grace() -> None:
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
