"""`bridge_control` — the app side of bridge-mode Discord control.

Every test drives the REAL `BridgeControlClient` against a real (local)
aiohttp server speaking the bridge's `/v1/*` shapes, so the fail-closed
mapping is measured on the wire, not asserted against a stub of the client
itself. The fake server is the bridge's surface, not a port double — it
answers wrong-call shapes the way the bridge does (`available: false`,
non-200s, unknown ops).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.models import ConversationTarget
from nora.domain.ports import (
    MusicToolOutcome,
    Speaker,
    SpeakResult,
    VoiceChannelRef,
)
from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint
from nora.platform.discord.bridge_control import (
    BridgeChannelDirectory,
    BridgeControlClient,
    BridgeDiscordAdapter,
    BridgeDiscordPresence,
    BridgeDiscordRoster,
    BridgeMusicTool,
    BridgeVoicePresence,
)

TOKEN = "test-bearer"  # noqa: S105 — a synthetic fixture token, not a secret
GUILD = "100000000000000001"
CHANNEL = "100000000000000002"
MEMBER = "100000000000000003"


def _bridge_app(state: dict) -> web.Application:
    """A fake discord-bridge: records calls, answers the canned `state` —
    and refuses a request that fails the auth the real bridge requires."""

    @web.middleware
    async def auth(req: web.Request, handler):
        if req.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(req)

    async def music(req: web.Request) -> web.Response:
        state["music"].append((req.match_info["op"], await req.json()))
        outcome = state["music_outcome"]
        if outcome is None:
            return web.json_response({"error": "down"}, status=503)
        return web.json_response({"outcome": outcome})

    async def reads(req: web.Request) -> web.Response:
        body = await req.json()
        state["reads"].append(body)
        # Per-guild answers when the test needs them: a guild the bridge
        # cannot enumerate answers `available: false`, never an empty map.
        per_guild = state["reads_by_guild"].get(
            (body.get("op"), (body.get("params") or {}).get("guild_id")))
        if per_guild is not None:
            return web.json_response(per_guild)
        if body.get("op") not in state["reads_ops"]:
            return web.json_response({"error": "unknown op"}, status=400)
        return web.json_response(state["reads_ops"][body["op"]])

    async def voice_state(req: web.Request) -> web.Response:
        state["voice_state"].append(req.match_info["guild_id"])
        return web.json_response(state["voice"])

    async def speech(req: web.Request) -> web.Response:
        data = await req.read()
        state["speech"].append((req.match_info["guild_id"], data,
                                req.headers.get("X-Speech-Media-Type")))
        return web.Response(status=state["speech_status"])

    async def actions(req: web.Request) -> web.Response:
        body = await req.json()
        state["actions"].append(body)
        return web.json_response({
            "action_id": body["action_id"], "state": "confirmed",
            "outcome": "confirmed", "platform_ms": 1, "error": None})

    async def health(req: web.Request) -> web.Response:
        return web.json_response({"ok": True, "connected_at_ms": 1,
                                  "detail": None})

    async def self_id(req: web.Request) -> web.Response:
        return web.json_response({
            "platform": "discord", "primary": "999", "aliases": ["999"],
            "handle": "nora"})

    app = web.Application(middlewares=[auth])
    app.router.add_post("/v1/music/{op}", music)
    app.router.add_post("/v1/reads", reads)
    app.router.add_get("/v1/voice/{guild_id}", voice_state)
    app.router.add_post("/v1/voice/{guild_id}/speech", speech)
    app.router.add_post("/v1/actions", actions)
    app.router.add_get("/v1/health", health)
    app.router.add_get("/v1/self", self_id)
    return app


@pytest.fixture
def bridge_state() -> dict:
    return {
        "music": [], "reads": [], "voice_state": [], "speech": [], "actions": [],
        "music_outcome": {"status": "ok", "title": "t", "url": "u",
                          "queued": False, "note": ""},
        "reads_ops": {}, "reads_by_guild": {},
        "voice": {"connected": True, "session": True, "generation": "g1",
                  "channel_id": CHANNEL},
        "speech_status": 204,
    }


@pytest.fixture
async def server(bridge_state):
    server = TestServer(_bridge_app(bridge_state))
    await server.start_server()
    yield server
    await server.close()


@pytest.fixture
def control(server) -> BridgeControlClient:
    return BridgeControlClient(
        BridgeEndpoint(base_url=str(server.make_url("")).rstrip("/"), token=TOKEN))


async def test_music_tool_forwards_and_maps(control, bridge_state):
    tool = BridgeMusicTool(control=control)
    outcome = await tool.play(
        conversation_id=f"{GUILD}/{CHANNEL}",
        sender_ids=frozenset({MEMBER}), query_or_url="song",
    )
    assert outcome.status == "ok" and outcome.title == "t"
    op, body = bridge_state["music"][-1]
    assert op == "play"
    assert body["conversation_id"] == f"{GUILD}/{CHANNEL}"
    assert body["sender_ids"] == [MEMBER]
    assert body["query_or_url"] == "song"


async def test_music_tool_transport_failure_is_unavailable(control, bridge_state):
    bridge_state["music_outcome"] = None  # bridge answers 503
    outcome = await BridgeMusicTool(control=control).skip(
        conversation_id=f"{GUILD}/{CHANNEL}")
    assert outcome == MusicToolOutcome(status="unavailable")


async def test_voice_presence_maps_members(control, bridge_state):
    bridge_state["reads_ops"]["voice_presence"] = {
        "available": True,
        "value": {MEMBER: {"channel_id": "777", "channel_name": "hall"}},
    }
    out = await BridgeVoicePresence(control=control).in_voice(
        member_ids=frozenset({MEMBER}))
    assert out == {MEMBER: VoiceChannelRef(channel_id="777", channel_name="hall")}


async def test_voice_presence_unavailable_is_none_not_empty(control, bridge_state):
    bridge_state["reads_ops"]["voice_presence"] = {"available": False, "value": None}
    out = await BridgeVoicePresence(control=control).in_voice(
        member_ids=frozenset({MEMBER}))
    assert out is None  # "could not look", never "nobody is in voice"


async def test_member_presence_maps_states(control, bridge_state):
    bridge_state["reads_ops"]["member_presence"] = {
        "available": True,
        "value": {MEMBER: {"status": "online", "game": "x",
                          "listening": None, "custom_status": None}},
    }
    out = await BridgeDiscordPresence(control=control).presence(
        member_ids=frozenset({MEMBER}))
    assert out is not None and out[MEMBER].status == "online"


async def test_member_presence_unavailable_is_none(control, bridge_state):
    bridge_state["reads_ops"]["member_presence"] = {"available": False}
    out = await BridgeDiscordPresence(control=control).presence(
        member_ids=frozenset({MEMBER}))
    assert out is None


async def test_roster_unavailable_is_not_empty(control, bridge_state):
    bridge_state["reads_ops"]["roster"] = {"available": False, "value": None}
    roster = await BridgeDiscordRoster(control=control).roster(
        conversation_id=f"{GUILD}/{CHANNEL}")
    assert roster.available is False


async def test_roster_maps_member_id_sets(control, bridge_state):
    bridge_state["reads_ops"]["roster"] = {
        "available": True,
        "value": {"members": [[MEMBER], ["other-a", "other-b"]]},
    }
    roster = await BridgeDiscordRoster(control=control).roster(
        conversation_id=f"{GUILD}/{CHANNEL}")
    assert roster.available is True
    assert frozenset({MEMBER}) in roster.members
    assert frozenset({"other-a", "other-b"}) in roster.members


async def test_channel_directory_snapshot_and_staleness(control, bridge_state):
    bridge_state["reads_ops"]["channels"] = {
        "available": True,
        "value": {"channels": {CHANNEL: "general"},
                  "threads": {"999": CHANNEL}},
    }
    directory = BridgeChannelDirectory(
        control=control, guild_ids=[GUILD], ttl_seconds=30.0)
    # Nothing refreshed yet — unknown, not empty.
    assert directory.guild_channels(guild_id=GUILD) is None
    await directory.refresh()
    assert directory.guild_channels(guild_id=GUILD) == {CHANNEL: "general"}
    assert directory.parent_channel(channel_id="999") == CHANNEL
    # A stale snapshot is unavailable, not silently old.
    directory._fetched_at = time.monotonic() - 120
    assert directory.guild_channels(guild_id=GUILD) is None
    assert directory.parent_channel(channel_id="999") is None


async def test_channel_directory_never_fabricates_empty(control, bridge_state):
    bridge_state["reads_ops"]["channels"] = {"available": False, "value": None}
    directory = BridgeChannelDirectory(control=control, guild_ids=[GUILD])
    await directory.refresh()
    # Bridge said "could not look" — the directory does not mint an empty guild.
    assert directory.guild_channels(guild_id=GUILD) is None


async def test_channel_directory_refresh_binds_the_real_guild_ids(
        control, bridge_state):
    """Refresh asks ONE read per configured guild: the readable guild's
    map lands, the unreadable one stays `None` — and staleness closes
    both the same way."""
    other_guild = "100000000000000009"
    bridge_state["reads_by_guild"][("channels", GUILD)] = {
        "available": True,
        "value": {"channels": {CHANNEL: "general"}, "threads": {}}}
    bridge_state["reads_by_guild"][("channels", other_guild)] = {
        "available": False, "reason": "cache_unavailable"}
    directory = BridgeChannelDirectory(
        control=control, guild_ids=[GUILD, other_guild], ttl_seconds=30.0)
    await directory.refresh()
    # Both guilds were queried — the refresh really walks the configured set.
    queried = {
        (b["op"], (b.get("params") or {}).get("guild_id"))
        for b in bridge_state["reads"]}
    assert {("channels", GUILD), ("channels", other_guild)} <= queried
    assert directory.guild_channels(guild_id=GUILD) == {CHANNEL: "general"}
    assert directory.guild_channels(guild_id=other_guild) is None
    # Stale → the whole snapshot closes, not just the guild that failed.
    directory._fetched_at = time.monotonic() - 120
    assert directory.guild_channels(guild_id=GUILD) is None
    assert directory.guild_channels(guild_id=other_guild) is None


def _speaker(audio: bytes = b"pcm") -> Speaker:
    speaker = Mock(spec=Speaker)
    speaker.speak = AsyncMock(return_value=SpeakResult(
        available=True, audio=audio, media_type="audio/wav"))
    return speaker


def _delegate(server) -> BridgeAdapter:
    return BridgeAdapter("discord", BridgeEndpoint(
        base_url=str(server.make_url("")).rstrip("/"), token=TOKEN))


async def test_voice_send_forwards_speech_to_session(control, server, bridge_state):
    adapter = BridgeDiscordAdapter(
        delegate=_delegate(server), control=control, speaker=_speaker())
    target = ConversationTarget(
        platform="discord", conversation_id=f"{GUILD}/{CHANNEL}",
        conversation_type="group", voice=True)
    result = await adapter.send(target=target, text="hello there")
    assert result.ok is True
    # Bounded bytes reached the bridge's speech route for the right guild.
    guild, data, media_type = bridge_state["speech"][-1]
    assert guild == GUILD and data == b"pcm" and media_type == "audio/wav"
    assert bridge_state["actions"] == []  # no text send


async def test_voice_send_without_session_falls_back_to_text(
        control, server, bridge_state):
    bridge_state["voice"] = {"connected": False, "session": False,
                             "generation": None, "channel_id": None}
    adapter = BridgeDiscordAdapter(
        delegate=_delegate(server), control=control, speaker=_speaker())
    target = ConversationTarget(
        platform="discord", conversation_id=f"{GUILD}/{CHANNEL}",
        conversation_type="group", voice=True)
    result = await adapter.send(target=target, text="read it instead")
    assert result.ok is True
    # Text fallback went through the shared action API, speech never paid for.
    assert bridge_state["actions"], "expected a text send through /v1/actions"
    assert bridge_state["speech"] == []


async def test_voice_send_unspeakable_text_falls_back(
        control, server, bridge_state):
    adapter = BridgeDiscordAdapter(
        delegate=_delegate(server), control=control, speaker=_speaker())
    target = ConversationTarget(
        platform="discord", conversation_id=f"{GUILD}/{CHANNEL}",
        conversation_type="group", voice=True)
    result = await adapter.send(target=target, text="see https://example.com/x")
    assert result.ok is True
    assert bridge_state["speech"] == []


async def test_adapter_delegates_identity_and_health(control, server):
    adapter = BridgeDiscordAdapter(
        delegate=_delegate(server), control=control, speaker=None)
    identity = await adapter.resolve_identity()
    assert identity.primary == "999"
    health = await adapter.health()
    assert health.connected is True


async def test_unauthenticated_bridge_calls_fail_closed(server, bridge_state):
    """A wrong bearer answers nothing usable — the auth middleware is part
    of the measured surface, not mocked away."""
    control = BridgeControlClient(BridgeEndpoint(
        base_url=str(server.make_url("")).rstrip("/"),
        token="wrong"))  # noqa: S106 — a deliberately bad test token
    outcome = await BridgeMusicTool(control=control).status(
        conversation_id=f"{GUILD}/{CHANNEL}")
    assert outcome.status == "unavailable"
    out = await BridgeVoicePresence(control=control).in_voice(
        member_ids=frozenset({MEMBER}))
    assert out is None
