"""Boot recovery on the Discord bridge's `on_ready`: a guild with a persisted
music session ends up with its player whichever join first yields a valid
snapshot, and a track-finished arriving mid-recovery advances that player."""

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


def _controller(state: dict) -> web.Application:
    # The first `malformed` joins omit `playback_id`; with `hold`, the
    # first join's answer (snapshotted on arrival) waits for it.

    def snap() -> dict:
        return {"voice": "connected", "generation": "gen-1", "channel_id": VOICE_CHANNEL,
                "playback_id": state["playback_id"],
                "player": {"status": "playing", "position_ms": 0}}

    async def operation(request: web.Request) -> web.Response:
        body = await request.json()
        kind, effect = body["kind"], body["effect"]
        state["calls"].append(kind)
        snapshot = snap()
        joins = state["calls"].count("join")
        if kind == "join" and joins <= state["malformed"]:
            snapshot.pop("playback_id")
        if kind == "join" and joins == 1 and state["hold"] is not None:
            state["join_arrived"].set()
            await state["hold"].wait()
        if kind == "play":
            state["plays"].append(effect["stream_url"])
            state["playback_id"] = f"pb-{len(state['plays'])}"
            snapshot = snap()
        return web.json_response(operation_envelope("complete", {"state": snapshot}))

    async def status(request: web.Request) -> web.Response:
        state["calls"].append("status")
        return web.json_response(read_envelope("ok", snap()))

    app = web.Application()
    app.router.add_post("/v1/voice/operations", operation)
    app.router.add_post("/v1/voice/queries", status)
    return app


async def _boot(aiohttp_client, monkeypatch, *, playback_id: str | None, malformed: int = 0,
                hold: asyncio.Event | None = None):
    async def _sync(self):
        return None

    monkeypatch.setattr(discord.app_commands.CommandTree, "sync", _sync)
    state: dict = {"playback_id": playback_id, "malformed": malformed, "hold": hold,
                   "join_arrived": asyncio.Event(), "calls": [], "plays": []}
    controller = await aiohttp_client(_controller(state))
    hub = VoiceControllerHub({GUILD: VoiceControllerClient(
        str(controller.make_url("")).rstrip("/"))})
    sessions, queue = FakeMusicSessionStore(), FakeMusicQueueStore()
    manager = MusicPlayerManager(
        queue_store=queue, session_store=sessions,
        track_source=_StubSource({TRACK.locator: TRACK, TRACK_B.locator: TRACK_B}),
        idle_timeout_seconds=300.0, spawn=asyncio.ensure_future,
        playback_probe=hub.expected_playback_id, read_playback=hub.read_playback,
        generation_probe=hub.generation_for, drop_voice_session=hub.drop,
        exit_voice_session=hub.exit)
    now = datetime.now(UTC)
    await sessions.upsert(MusicSession(
        guild_id=GUILD, voice_channel_id=VOICE_CHANNEL, notify_channel_id=TEXT_CHANNEL,
        now_playing_source=TRACK.source, now_playing_locator=TRACK.locator,
        now_playing_title=TRACK.title, paused=False, volume=1.0, updated_at=now))
    await queue.enqueue(guild_id=GUILD, source=TRACK_B.source, url=TRACK_B.locator,
                        title=TRACK_B.title, added_by_person_id=MEMBER, added_at=now)
    guild = _Guild(int(GUILD))
    voice = _Channel(int(VOICE_CHANNEL), "voice", guild, 2)
    guild.channels.extend([_Channel(int(TEXT_CHANNEL), "general", guild, 0), voice])
    voice.members.append(guild.add_member(_Member(int(MEMBER))))
    bridge = DiscordBridge(api_token="t", account_key="a",  # noqa: S106 — synthetic
                           client=FakeDiscordClient([guild]),
                           manager=manager, hub=hub, voice_guild_ids=frozenset({GUILD}))
    return state, hub, manager, bridge


def _finished(playback_id: str) -> dict:
    return {"type": "track-finished", "event_id": f"ev-{playback_id}", "event_seq": 1,
            "guild_id": GUILD, "playback_id": playback_id}


@pytest.mark.parametrize(("live", "malformed", "plays_on_boot", "finishing"), [
    ("pb-live", 0, [], "pb-live"),
    (None, 0, [TRACK.stream_url], "pb-1"),
    ("pb-live", 1, [], "pb-live"),
])
async def test_recovery_keeps_or_restarts_the_track_then_advances(
        aiohttp_client, monkeypatch, live, malformed, plays_on_boot, finishing):
    """A live id is adopted, not replayed; none restarts the track; one
    malformed join is retried. Either way the finish advances to the queue."""
    state, hub, manager, bridge = await _boot(
        aiohttp_client, monkeypatch, playback_id=live, malformed=malformed)
    try:
        await bridge.on_gateway_ready()
        assert hub.session_for(GUILD) is not None
        assert state["plays"] == plays_on_boot
        finished = await VoiceEventConsumer(manager, hub).consume(_finished(finishing))
    finally:
        if manager._liveness_task is not None:
            manager._liveness_task.cancel()

    assert finished == (200, {"status": "ok"})
    assert state["plays"] == [*plays_on_boot, TRACK_B.stream_url]


async def test_two_malformed_joins_leave_no_session_and_no_player(aiohttp_client, monkeypatch):
    state, hub, manager, bridge = await _boot(
        aiohttp_client, monkeypatch, playback_id="pb-live", malformed=2)
    try:
        await bridge.on_gateway_ready()
    finally:
        if manager._liveness_task is not None:
            manager._liveness_task.cancel()

    assert hub.session_for(GUILD) is None
    assert GUILD not in manager._players
    assert state["calls"].count("join") == 2 and state["plays"] == []


async def test_a_finish_during_recovery_waits_for_the_player_and_advances(
        aiohttp_client, monkeypatch):
    """The live track ends while recovery awaits its join answer: the finish
    waits for the guild's player, then advances to the queued track; the
    finished track is never adopted as live and replayed."""
    hold = asyncio.Event()
    state, hub, manager, bridge = await _boot(
        aiohttp_client, monkeypatch, playback_id="pb-live", hold=hold)
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
        if manager._liveness_task is not None:
            manager._liveness_task.cancel()

    assert finished == (200, {"status": "ok"})
    assert state["plays"] == [TRACK_B.stream_url]
