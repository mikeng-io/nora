"""The voice pipeline WIRED: the real `MusicPlayerManager`, `VoiceControllerHub`,
`HttpVoiceSession` and `VoiceEventConsumer` over real HTTP against
`nora/testing/voice.py`'s `FakeVoiceController` — the same objects
`nora.app.composition` wires, exercising the following ACROSS the seams instead of per module:

- a stop after a controller restart tolerates the dead generation and the
  session row still goes;
- a voice-death / channel-emptied delayed across an exit/rejoin is validated
  against the live generation and touches nothing;
- a voice-only session exits when its channel empties;
- the first command after a restart reconciles the generation instead of
  409-looping, and the hub and manager agree afterwards.

`_StubSource` is imported from the manager's own suite rather than copied —
the port-fakes gate pins ONE inline `TrackSource` stand-in per file and the
list only burns down.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from nora.app.voice_events import VoiceEventConsumer
from nora.domain.ports import ResolvedTrack
from nora.infra.voice.http_controller import HttpVoiceSession, VoiceControllerClient
from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.testing.music import FakeMusicQueueStore, FakeMusicSessionStore
from nora.testing.voice import FakeVoiceController
from tests.unit.test_music_player_manager import _StubSource

GUILD = "guild-1"
CHANNEL = "cortez-1"
T0 = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

_TRACK_A = ResolvedTrack(
    title="Track A", stream_url="https://cdn.example/a", duration_seconds=200,
    source="youtube", source_id="a", locator="locator-a",
)
_TRACK_B = ResolvedTrack(
    title="Track B", stream_url="https://cdn.example/b", duration_seconds=180,
    source="youtube", source_id="b", locator="locator-b",
)


async def _instant_sleep(_seconds: float) -> None:
    return None


class _Pipeline:
    def __init__(self, base_url: str, fake: FakeVoiceController) -> None:
        self.fake = fake
        self.client = VoiceControllerClient(base_url)
        self.hub = VoiceControllerHub({GUILD: self.client})
        self.queue_store = FakeMusicQueueStore()
        self.session_store = FakeMusicSessionStore()
        self.manager = MusicPlayerManager(
            queue_store=self.queue_store, session_store=self.session_store,
            track_source=_StubSource({"track a": _TRACK_A, "track b": _TRACK_B}),
            idle_timeout_seconds=300.0, spawn=asyncio.ensure_future,
            clock_sleep=_instant_sleep,
            playback_probe=self.hub.expected_playback_id,
            generation_probe=self.hub.generation_for,
            drop_voice_session=self.hub.drop,
            exit_voice_session=self.hub.exit,
        )
        self.consumer = VoiceEventConsumer(self.manager, self.hub)

    async def connect(self) -> HttpVoiceSession:
        """The `connect` factory a slash command hands the manager — joining
        IS going through the hub, so the session the player holds is the one
        the hub caches (and the one the probes read)."""
        return await self.hub.join(GUILD, CHANNEL)

    async def play(self, query: str = "track a") -> Any:
        return await self.manager.play(
            guild_id=GUILD, query_or_url=query, requested_by="miles", added_at=T0,
            connect=self.connect, voice_channel_id=CHANNEL, notify_channel_id="notify-1",
        )


@pytest.fixture
async def pipeline(aiohttp_client: Any) -> _Pipeline:
    fake = FakeVoiceController(GUILD, CHANNEL)
    test_client = await aiohttp_client(fake.app())
    return _Pipeline(str(test_client.make_url("")).rstrip("/"), fake)


async def test_play_then_controller_restart_then_stop_cleans_the_session_row(
    pipeline: _Pipeline,
) -> None:
    """Dead-connection cleanup across the seams: after a restart the
    manager's stop tolerates the stale-generation 409 (the music slot is
    definitionally stopped) and the local teardown still completes."""
    await pipeline.play()
    assert pipeline.fake.voice == "connected"
    assert await pipeline.session_store.get(GUILD) is not None
    pipeline.fake.restart()

    await pipeline.manager.stop(GUILD)

    assert await pipeline.session_store.get(GUILD) is None
    assert pipeline.manager._players.get(GUILD) is None


async def test_a_delayed_voice_death_from_an_old_generation_leaves_the_new_session(
    pipeline: _Pipeline,
) -> None:
    """Across the seams: an outbox death event delivered after an
    exit/rejoin names the OLD generation and must not tear down the NEW
    session — and the CURRENT generation's death still does (positive
    control)."""
    await pipeline.play()
    first_generation = pipeline.hub.generation_for(GUILD)
    assert first_generation is not None

    # The connection drops and re-joins; the hub mints a fresh generation.
    await pipeline.hub.exit(GUILD)
    await pipeline.hub.join(GUILD, CHANNEL)
    new_generation = pipeline.hub.generation_for(GUILD)
    assert new_generation is not None and new_generation != first_generation

    status, _ = await pipeline.consumer.consume({
        "type": "voice-death", "guild_id": GUILD, "generation": first_generation,
        "event_id": "evt-stale-death", "event_seq": 1,
    })
    assert status == 200
    assert pipeline.hub.generation_for(GUILD) == new_generation
    assert pipeline.manager._players.get(GUILD) is not None

    # Positive control: the live generation's death applies.
    status, _ = await pipeline.consumer.consume({
        "type": "voice-death", "guild_id": GUILD, "generation": new_generation,
        "event_id": "evt-current-death", "event_seq": 2,
    })
    assert status == 200
    assert pipeline.hub.generation_for(GUILD) is None
    assert pipeline.manager._players.get(GUILD) is None


async def test_a_voice_only_session_exits_when_its_channel_empties(
    pipeline: _Pipeline,
) -> None:
    """A hub session with no music player still leaves on `channel-emptied`
    — the hub exits the connection directly (there is no player whose
    `leave` would do it)."""
    await pipeline.hub.join(GUILD, CHANNEL)
    generation = pipeline.hub.generation_for(GUILD)
    assert generation is not None
    assert pipeline.fake.voice == "connected"

    status, _ = await pipeline.consumer.consume({
        "type": "channel-emptied", "guild_id": GUILD, "generation": generation,
        "channel_id": CHANNEL, "event_id": "evt-empty", "event_seq": 1,
    })

    assert status == 200
    assert pipeline.hub.session_for(GUILD) is None
    assert pipeline.fake.voice == "idle"


async def test_a_stale_generation_channel_emptied_leaves_the_voice_only_session(
    pipeline: _Pipeline,
) -> None:
    """The voice-only path: a delayed `channel-emptied` from BEFORE
    a rejoin names a dead generation and is ignored."""
    await pipeline.hub.join(GUILD, CHANNEL)
    assert pipeline.hub.generation_for(GUILD) is not None

    status, _ = await pipeline.consumer.consume({
        "type": "channel-emptied", "guild_id": GUILD, "generation": "gen-long-gone",
        "channel_id": CHANNEL, "event_id": "evt-stale-empty", "event_seq": 1,
    })

    assert status == 200
    assert pipeline.hub.session_for(GUILD) is not None
    assert pipeline.fake.voice == "connected"


async def test_a_controller_restart_mid_session_reconciles_the_next_command(
    pipeline: _Pipeline,
) -> None:
    """Across the seams: after a restart the next manager command
    reconciles the generation (one status read, one retry) instead of
    409-looping — and because the hub and the player hold the SAME session
    object, the hub agrees with the new generation afterwards."""
    await pipeline.play()
    pipeline.fake.restart()
    restart_generation = pipeline.fake.generation
    assert restart_generation is not None

    result = await pipeline.manager.set_volume(GUILD, 0.8)

    assert result is True
    assert pipeline.fake.volume_percent == 80
    assert pipeline.hub.generation_for(GUILD) == restart_generation
    assert pipeline.manager._players.get(GUILD) is not None


async def test_an_advance_after_a_controller_restart_plays_on_the_new_generation(
    pipeline: _Pipeline,
) -> None:
    """A `track-finished` consumed after a restart advances the queue: the
    play 409s once, the session adopts the live generation, the retry
    lands, and the hub's expected playback is the controller's fresh one."""
    await pipeline.play("track a")
    queued = await pipeline.manager.play(
        guild_id=GUILD, query_or_url="track b", requested_by="miles", added_at=T0,
        connect=None,
    )
    assert queued is not None and queued.status == "queued"
    pipeline.fake.restart()

    advanced = await pipeline.manager.on_track_finished(
        GUILD, playback_id=pipeline.hub.expected_playback_id(GUILD),
    )

    assert advanced is True
    assert pipeline.fake.playback_id is not None
    assert pipeline.hub.expected_playback_id(GUILD) == pipeline.fake.playback_id
    status = await pipeline.manager.status(GUILD)
    assert status is not None
    assert status.now_playing is not None
    assert status.now_playing.title == "Track B"
