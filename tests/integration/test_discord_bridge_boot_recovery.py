"""Boot recovery on the Discord bridge's `on_ready`: a guild with a persisted
music session ends up with its player whichever join first yields a valid
snapshot, a track-finished arriving mid-recovery advances that player, and
each guild recovers on its own."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import discord
import pytest
from aiohttp import web

from nora.app.voice_events import VoiceEventConsumer
from nora.domain.music import MusicSession
from nora.infra.voice.http_controller import VoiceControllerClient
from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.discord import music_commands
from nora.platform.discord.bridge_app import DiscordBridge
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.testing.music import FakeMusicQueueStore, FakeMusicSessionStore
from nora.testing.voice import operation_envelope, read_envelope
from tests.integration.test_discord_bridge_control import (
    GUILD,
    MEMBER,
    TEXT_CHANNEL,
    TRACK,
    TRACK_B,
    VOICE_CHANNEL,
    FakeDiscordClient,
    _Channel,
    _Guild,
    _Member,
    _StubSource,
)

GUILD_2 = "100000000000000011"
VOICE_2 = "100000000000000013"


def _state(*, voice: str = VOICE_CHANNEL, playback_id: str | None = "pb-live",
           malformed: int = 0, hold: asyncio.Event | None = None, down: bool = False) -> dict:
    return {"voice": voice, "playback_id": playback_id, "malformed": malformed, "hold": hold,
            "down": down, "join_arrived": asyncio.Event(), "calls": [], "plays": []}


def _controller(state: dict) -> web.Application:
    # The first `malformed` joins omit `playback_id`; with `hold`, the
    # first join's answer (snapshotted on arrival) waits for it.

    def snap() -> dict:
        return {"voice": "connected", "generation": "gen-1", "channel_id": state["voice"],
                "playback_id": state["playback_id"],
                "player": {"status": "playing", "position_ms": 0}}

    async def operation(request: web.Request) -> web.Response:
        body = await request.json()
        kind, effect = body["kind"], body["effect"]
        state["calls"].append(kind)
        if state["down"]:
            return web.json_response({"error": "down"}, status=500)
        snapshot = snap()
        joins = state["calls"].count("join")
        if kind == "join" and joins <= state["malformed"]:
            snapshot.pop("playback_id")
        if kind == "join" and joins == 1 and state["hold"] is not None:
            state["join_arrived"].set()
            await state["hold"].wait()
            state["held_answered"] = True
        if kind == "play":
            state["plays"].append(effect["stream_url"])
            state["playback_id"] = f"pb-{len(state['plays'])}"
            snapshot = snap()
        return web.json_response(operation_envelope("complete", {"state": snapshot}))

    async def status(request: web.Request) -> web.Response:
        state["calls"].append("status")
        if state["down"]:
            return web.json_response({"error": "down"}, status=500)
        return web.json_response(read_envelope("ok", snap()))

    app = web.Application()
    app.router.add_post("/v1/voice/operations", operation)
    app.router.add_post("/v1/voice/queries", status)
    return app


async def _world(aiohttp_client, monkeypatch, states: dict[str, dict],
                 wake_only: frozenset[str] = frozenset()):
    """A bridge whose guilds each persist Track A playing, Track B queued."""
    async def _sync(self):
        return None

    monkeypatch.setattr(discord.app_commands.CommandTree, "sync", _sync)
    clients = {}
    for guild_id, state in states.items():
        controller = await aiohttp_client(_controller(state))
        clients[guild_id] = VoiceControllerClient(str(controller.make_url("")).rstrip("/"))
    hub = VoiceControllerHub(clients)
    sessions, queue = FakeMusicSessionStore(), FakeMusicQueueStore()
    manager = MusicPlayerManager(
        queue_store=queue, session_store=sessions,
        track_source=_StubSource({TRACK.locator: TRACK, TRACK_B.locator: TRACK_B}),
        idle_timeout_seconds=300.0, spawn=asyncio.ensure_future,
        playback_probe=hub.expected_playback_id, read_playback=hub.read_playback,
        generation_probe=hub.generation_for, drop_voice_session=hub.drop,
        exit_voice_session=hub.exit)
    now = datetime.now(UTC)
    guilds = []
    for guild_id, state in states.items():
        guild = _Guild(int(guild_id))
        voice = _Channel(int(state["voice"]), "voice", guild, 2)
        guild.channels.extend([_Channel(int(TEXT_CHANNEL), "general", guild, 0), voice])
        voice.members.append(guild.add_member(_Member(int(MEMBER))))
        guilds.append(guild)
        if guild_id in wake_only:
            continue
        await sessions.upsert(MusicSession(
            guild_id=guild_id, voice_channel_id=state["voice"], notify_channel_id=TEXT_CHANNEL,
            now_playing_source=TRACK.source, now_playing_locator=TRACK.locator,
            now_playing_title=TRACK.title, paused=False, volume=1.0, updated_at=now))
        await queue.enqueue(guild_id=guild_id, source=TRACK_B.source, url=TRACK_B.locator,
                            title=TRACK_B.title, added_by_person_id=MEMBER, added_at=now)
    bridge = DiscordBridge(api_token="t", account_key="a",  # noqa: S106 — synthetic
                           client=FakeDiscordClient(guilds),
                           manager=manager, hub=hub, voice_guild_ids=frozenset(states))
    return hub, manager, bridge


def _finished(playback_id: str, guild_id: str = GUILD) -> dict:
    return {"type": "track-finished", "event_id": f"ev-{guild_id}-{playback_id}",
            "event_seq": 1, "guild_id": guild_id, "playback_id": playback_id}


def _stop_watch(manager: MusicPlayerManager) -> None:
    if manager._liveness_task is not None:
        manager._liveness_task.cancel()


@pytest.mark.parametrize(("live", "malformed", "plays_on_boot", "finishing"), [
    ("pb-live", 0, [], "pb-live"),
    (None, 0, [TRACK.stream_url], "pb-1"),
    ("pb-live", 1, [], "pb-live"),
])
async def test_recovery_keeps_or_restarts_the_track_then_advances(
        aiohttp_client, monkeypatch, live, malformed, plays_on_boot, finishing):
    """A live id is adopted, not replayed; none restarts the track; one
    malformed join is retried. Either way the finish advances to the queue."""
    state = _state(playback_id=live, malformed=malformed)
    hub, manager, bridge = await _world(aiohttp_client, monkeypatch, {GUILD: state})
    try:
        await bridge.on_gateway_ready()
        assert hub.session_for(GUILD) is not None
        assert state["plays"] == plays_on_boot
        finished = await VoiceEventConsumer(manager, hub).consume(_finished(finishing))
    finally:
        _stop_watch(manager)

    assert finished == (200, {"status": "ok"})
    assert state["plays"] == [*plays_on_boot, TRACK_B.stream_url]


async def test_two_malformed_joins_leave_no_session_and_no_player(aiohttp_client, monkeypatch):
    state = _state(malformed=2)
    hub, manager, bridge = await _world(aiohttp_client, monkeypatch, {GUILD: state})
    try:
        await bridge.on_gateway_ready()
    finally:
        _stop_watch(manager)

    assert hub.session_for(GUILD) is None
    assert GUILD not in manager._players
    assert state["calls"].count("join") == 2 and state["plays"] == []


async def test_a_finish_during_recovery_waits_for_the_player_and_advances(
        aiohttp_client, monkeypatch):
    """The live track ends while recovery awaits its join answer: the finish
    waits for the guild's player, then advances to the queued track; the
    finished track is never adopted as live and replayed."""
    hold = asyncio.Event()
    state = _state(hold=hold)
    hub, manager, bridge = await _world(aiohttp_client, monkeypatch, {GUILD: state})
    consumer = VoiceEventConsumer(manager, hub)
    try:
        ready = asyncio.create_task(bridge.on_gateway_ready())
        await asyncio.wait_for(state["join_arrived"].wait(), 5)
        state["playback_id"] = None  # the controller's own state after the end
        finish = asyncio.create_task(consumer.consume(_finished("pb-live")))
        for _ in range(20):
            await asyncio.sleep(0)
        assert not finish.done(), "the finish waits for recovery, never answers stale"
        hold.set()
        await asyncio.wait_for(ready, 5)
        finished = await asyncio.wait_for(finish, 5)
    finally:
        _stop_watch(manager)

    assert finished == (200, {"status": "ok"})
    assert state["plays"] == [TRACK_B.stream_url]


async def test_a_held_join_in_one_guild_delays_no_other(aiohttp_client, monkeypatch):
    """Guild 1's join answer is held: guild 2 still recovers, its finish
    advances to its queued track and its speech reaches its controller."""
    hold = asyncio.Event()
    first, second = _state(hold=hold), _state(voice=VOICE_2)
    hub, manager, bridge = await _world(
        aiohttp_client, monkeypatch, {GUILD: first, GUILD_2: second})
    consumer = VoiceEventConsumer(manager, hub)
    try:
        ready = asyncio.create_task(bridge.on_gateway_ready())
        await asyncio.wait_for(first["join_arrived"].wait(), 5)
        for _ in range(50):
            if GUILD_2 in manager._players:
                break
            await asyncio.sleep(0.01)
        finished = await asyncio.wait_for(consumer.consume(_finished("pb-live", GUILD_2)), 5)
        spoke = await asyncio.wait_for(hub.stream_speech(GUILD_2, b"pcm", "audio/wav"), 5)
        assert not ready.done()
        hold.set()
        await asyncio.wait_for(ready, 5)
    finally:
        _stop_watch(manager)

    assert finished == (200, {"status": "ok"})
    assert second["plays"] == [TRACK_B.stream_url]
    assert spoke is True and "speak" in second["calls"]
    assert hub.session_for(GUILD) is not None and first["plays"] == []


async def test_one_guilds_failed_recovery_leaves_the_other_recovered(
        aiohttp_client, monkeypatch, caplog):
    down, up = _state(down=True), _state(voice=VOICE_2)
    hub, manager, bridge = await _world(aiohttp_client, monkeypatch, {GUILD: down, GUILD_2: up})
    try:
        await bridge.on_gateway_ready()
    finally:
        _stop_watch(manager)

    assert hub.session_for(GUILD) is None and GUILD not in manager._players
    assert hub.session_for(GUILD_2) is not None and GUILD_2 in manager._players
    prefixes = ("voice recovery failed", "boot voice adoption failed")
    failures = [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefixes)]
    assert failures == [
        "boot voice adoption failed: VoiceControllerUnavailable — will retry on the next join/send",
        "voice recovery failed: VoiceControllerUnavailable"]
    assert not [m for m in failures if GUILD in m or "HTTP" in m or "/v1/" in m]


async def test_a_late_adoption_never_replaces_the_session_playback_started(
        aiohttp_client, monkeypatch):
    """Guild 1's recovery is held; wake-only guild 2's adoption answer is
    delayed while `/music play` starts and queues there. The late answer is
    older than the session play wrote, so it is refused: the finish advances."""
    hold_1, hold_2 = asyncio.Event(), asyncio.Event()
    first, second = _state(hold=hold_1), _state(voice=VOICE_2, playback_id=None, hold=hold_2)
    hub, manager, bridge = await _world(
        aiohttp_client, monkeypatch, {GUILD: first, GUILD_2: second},
        wake_only=frozenset({GUILD_2}))
    connect = music_commands._connect_factory(hub, GUILD_2, VOICE_2)
    try:
        ready = asyncio.create_task(bridge.on_gateway_ready())
        await asyncio.wait_for(first["join_arrived"].wait(), 5)
        await asyncio.wait_for(second["join_arrived"].wait(), 5)
        for track in (TRACK, TRACK_B):
            await manager.play(guild_id=GUILD_2, query_or_url=track.locator,
                               requested_by=MEMBER, added_at=datetime.now(UTC),
                               connect=connect, voice_channel_id=VOICE_2,
                               notify_channel_id=TEXT_CHANNEL)
        assert second["plays"] == [TRACK.stream_url]
        hold_2.set()
        for _ in range(100):
            if second.get("held_answered"):
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)  # the hub reads the late answer
        assert second.get("held_answered")
        assert hub.expected_playback_id(GUILD_2) == "pb-1"
        finished = await asyncio.wait_for(
            VoiceEventConsumer(manager, hub).consume(_finished("pb-1", GUILD_2)), 5)
        hold_1.set()
        await asyncio.wait_for(ready, 5)
    finally:
        _stop_watch(manager)

    assert finished == (200, {"status": "ok"})
    assert second["plays"] == [TRACK.stream_url, TRACK_B.stream_url]
