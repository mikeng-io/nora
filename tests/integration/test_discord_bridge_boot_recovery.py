"""Boot recovery on the Discord bridge's `on_ready`: whichever join first
yields a valid connection snapshot, the guild with a persisted music session
ends up with its player, so the live track's finish still advances the queue."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import discord
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
    # Connected and playing `pb-live`; the first join answer leaves out
    # the `playback_id` key, as a malformed reply would.

    def snap() -> dict:
        return {"voice": "connected", "generation": "gen-1", "channel_id": VOICE_CHANNEL,
                "playback_id": state["playback_id"],
                "player": {"status": "playing", "position_ms": 0}}

    async def operation(request: web.Request) -> web.Response:
        body = await request.json()
        kind, effect = body["kind"], body["effect"]
        state["calls"].append(kind)
        snapshot = snap()
        if kind == "join" and state["calls"].count("join") == 1:
            snapshot.pop("playback_id")
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


async def test_a_malformed_first_join_still_leaves_the_guild_its_player(
        aiohttp_client, monkeypatch):
    async def _sync(self):
        return None

    monkeypatch.setattr(discord.app_commands.CommandTree, "sync", _sync)
    state: dict = {"playback_id": "pb-live", "calls": [], "plays": []}
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

    try:
        await bridge.on_gateway_ready()
        assert hub.session_for(GUILD) is not None
        assert state["plays"] == [], "the live track is adopted, never replayed"
        finished = await VoiceEventConsumer(manager, hub).consume({
            "type": "track-finished", "event_id": "ev-1", "event_seq": 1,
            "guild_id": GUILD, "playback_id": "pb-live"})
    finally:
        if manager._liveness_task is not None:
            manager._liveness_task.cancel()

    assert finished == (200, {"status": "ok"})
    assert state["plays"] == [TRACK_B.stream_url]

