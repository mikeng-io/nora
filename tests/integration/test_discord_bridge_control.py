"""The smallest meaningful synthetic split for the Discord bridge.

One `discord-bridge` (real `DiscordBridge` over the REAL
`MusicPlayerManager`, `VoiceControllerHub`, `VoiceEventConsumer`, disposable
PostgreSQL queue/session stores, the real slash-tree registration and the
real `DiscordSender`/`DiscordReactor`/`DiscordRoster` REST implementations)
talks to two fakes that speak the REAL wire protocols:

- a fake audio-controller answering the `voice` contract
  `VoiceControllerClient` calls — including the refusals the real server
  makes (`connected_elsewhere`, `generation_conflict`, no connection); and
- a fake Discord REST base answering the actual `DiscordSender`/
  `DiscordReactor`/`DiscordRoster` endpoints.

The "Gateway" is a client-shaped object driven through the real
`DiscordListener`: `client.event(...)` records the same handler names
discord.py registers and `client.start()` fires `on_ready`, so the whole
ready-path (tree registration, persisted-session resume, liveness watch,
boot adoption) runs unmodified. `CommandTree.sync` is the only seam
replaced — it IS the Discord HTTP call, and there is no Discord.

"The app" is the real `bridge_control` forwarding layer — constructed,
stopped, and constructed AGAIN mid-test: app recreation must not touch the
bridge's client, manager, session or controller generation.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import discord
import pytest
import sqlalchemy as sa
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from nora.app.voice_events import VoiceEventConsumer
from nora.domain.models import ConversationTarget
from nora.domain.ports import ResolvedTrack, SpeakResult
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.music_queue import SqlMusicQueueStore
from nora.infra.sql.music_session import SqlMusicSessionStore
from nora.infra.sql.schema import MESSAGES
from nora.infra.voice.http_controller import VoiceControllerClient
from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint
from nora.platform.bridge.envelope import Envelope
from nora.platform.bridge.journal import Journal
from nora.platform.bridge.service import (
    DurableActionService,
    JournalCaptureSink,
    JournalPublisher,
    receipt_intent_ids,
)
from nora.platform.discord.bridge_app import (
    DiscordBridge,
    DiscordRestActionService,
)
from nora.platform.discord.bridge_control import (
    BridgeControlClient,
    BridgeDiscordAdapter,
    BridgeMusicTool,
)
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.platform.discord.roster import DiscordRoster
from nora.testing.voice import operation_envelope, read_envelope
from tests.integration.platform_contract import (
    Bridge,
    assert_serves_the_contract,
    drive_outcomes,
    envelope,
    swapped,
)

TOKEN = "it-bearer"  # noqa: S105 — a synthetic fixture token, not a secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ACCOUNT = "it-account"
GUILD = "100000000000000001"
TEXT_CHANNEL = "100000000000000002"
VOICE_CHANNEL = "100000000000000003"
MEMBER = "100000000000000004"
ANNOUNCE = "100000000000000006"
ANNOUNCE_2 = "100000000000000007"
CONV = f"{GUILD}/{TEXT_CHANNEL}"
GEN = "gen-1"

TRACK = ResolvedTrack(
    title="Track A", stream_url="https://cdn.example/a.opus",
    duration_seconds=180, source="youtube", source_id="a",
    locator="yt:a")
TRACK_B = ResolvedTrack(
    title="Track B", stream_url="https://cdn.example/b.opus",
    duration_seconds=120, source="youtube", source_id="b",
    locator="yt:b")


# ---- the fake Gateway: a client-shaped object through the real listener -----


class _Channel(discord.abc.Messageable):
    """A guild channel shaped for the cache reads — including
    `discord.abc.Messageable`, which `music_notify._messageable` requires
    of the announce destination (the isinstance is the real code's own
    cross-guild/DM guard)."""

    def __init__(self, channel_id: int, name: str, guild: _Guild,
                 type_value: int) -> None:
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.type = SimpleNamespace(value=type_value)
        self.parent_id = None
        self.members: list = []
        self.send = AsyncMock(return_value=SimpleNamespace(
            id=4242, created_at=datetime.now(UTC)))
        self.typing = AsyncMock()
        self.fetch_message = AsyncMock()


class _Member:
    def __init__(self, member_id: int, *, voice=None) -> None:
        self.id = member_id
        self.name = f"m{member_id}"
        self.display_name = self.name
        self.bot = False
        self.voice = voice
        self.status = "online"
        self.activities: list = []


class _Guild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.channels: list[_Channel] = []
        self.threads: list = []
        self._members: dict[int, _Member] = {}

    @property
    def text_channels(self) -> list[_Channel]:
        return [c for c in self.channels if c.type.value == 0]

    @property
    def members(self) -> list[_Member]:
        return list(self._members.values())

    def get_member(self, member_id: int):
        return self._members.get(member_id)

    def add_member(self, member: _Member) -> _Member:
        self._members[member.id] = member
        return member

    async def fetch_member(self, member_id: int):
        return None

    async def fetch_members(self, limit=None):
        for m in self._members.values():
            yield m


class FakeDiscordClient:
    """`discord.Client`'s surface for `DiscordListener`, `CommandTree` and
    the cache reads — `event`/`start`/`is_closed`/`is_ready`/`close`, the
    guild/channel cache and `_connection`/`http` for `CommandTree`."""

    def __init__(self, guilds: list[_Guild]) -> None:
        self.guilds = guilds
        self.user = SimpleNamespace(id=999999999, name="nora-it")
        self._handlers: dict = {}
        self._closed = False
        self._connection = SimpleNamespace(_command_tree=None)
        self.http = None
        self.started_with: str | None = None

    def event(self, fn):
        self._handlers[fn.__name__] = fn
        return fn

    async def start(self, token: str) -> None:
        self.started_with = token
        await self._handlers["on_ready"]()

    def is_closed(self) -> bool:
        return self._closed

    def is_ready(self) -> bool:
        return not self._closed

    async def close(self) -> None:
        self._closed = True

    def get_guild(self, guild_id: int):
        return {g.id: g for g in self.guilds}.get(guild_id)

    def get_channel(self, channel_id: int):
        for g in self.guilds:
            for ch in (*g.channels, *g.threads):
                if ch.id == channel_id:
                    return ch
        return None

    async def fetch_channel(self, channel_id: int):
        return self.get_channel(channel_id)


# ---- the fake audio-controller: the real `voice` contract ------------------


def _controller_app(state: dict) -> web.Application:
    """The `voice` contract `VoiceControllerClient` calls, with the real
    server's refusals: a join onto a different live channel is `rejected`
    `connected_elsewhere`, a kind on a dead generation `rejected`
    `generation_conflict`, a status for an unconnected guild `nothing_found`."""

    def snap() -> dict:
        return {
            "voice": "connected" if state["connected"] else "disconnected",
            "channel_id": state["channel_id"],
            "generation": state["generation"],
            "playback_id": state["playback_id"],
            "player": {"status": state["player"], "position_ms": 0},
            "autopaused": False,
            "subscribers": 1 if state["connected"] else 0,
            "streaming": state["streaming"],
            "last_event_seq": state["last_event_seq"],
            "last_error": None,
        }

    def answer(reason: str | None = None) -> web.Response:
        error = {"error": {"kind": reason, "message": reason}} if reason else {}
        return web.json_response(operation_envelope(
            "rejected" if reason else "complete", {"state": snap(), **error}))

    async def operation(req: web.Request) -> web.Response:
        body = await req.json()
        kind, effect = body["kind"], body["effect"]
        if kind == "join":
            state["join_calls"].append(effect)
            if state["connected"] and effect.get("channel_id") != state["channel_id"]:
                return answer("connected_elsewhere")
            if not state["connected"]:
                state["connected"] = True
                state["channel_id"] = effect["channel_id"]
                state["generation"] = state["generation"] or GEN
            return answer()
        calls = {"leave": "exit_calls", "speak": "stream_calls"}.get(kind)
        state[calls].append(effect) if calls else state["music_calls"].append((kind, effect))
        if effect.get("generation") != state["generation"]:
            return answer("generation_conflict")
        if kind == "leave":
            state["connected"] = False
            state["channel_id"] = None
        if kind == "play":
            state["playback_id"] = f"pb-{len(state['music_calls'])}"
            state["player"] = "playing"
        if kind == "stop":
            state["player"] = "idle"
            state["playback_id"] = None
        return answer()

    async def status(req: web.Request) -> web.Response:
        guild_id = (await req.json())["params"]["guild_id"]
        state["status_calls"].append(guild_id)
        if guild_id != GUILD or not state["connected"]:
            return web.json_response(read_envelope("nothing_found", None))
        return web.json_response(read_envelope("ok", snap()))

    app = web.Application()
    app.router.add_post("/v1/voice/operations", operation)
    app.router.add_post("/v1/voice/queries", status)
    return app


def _discord_rest_app(state: dict) -> web.Application:
    """`DiscordSender`/`DiscordReactor`/`DiscordRoster`'s REST endpoints:
    member list, message post, reaction PUT/DELETE — 204/200 shapes as
    documented, wrong calls answered the way Discord answers them."""

    async def members(req: web.Request) -> web.Response:
        state["member_reads"].append(req.match_info["guild_id"])
        return web.json_response([
            {"user": {"id": MEMBER, "username": "alice", "bot": False},
             "nick": "Alice"},
            {"user": {"id": "100000000000000005", "username": "bob",
                      "bot": False}, "nick": None},
        ])

    async def post_message(req: web.Request) -> web.Response:
        if req.content_type == "multipart/form-data":
            reader = await req.multipart()
            async for part in reader:
                content = await part.read()
                if part.filename:
                    state.setdefault("uploads", []).append({
                        "name": part.name, "filename": part.filename,
                        "media_type": part.headers.get("Content-Type"),
                        "content": content,
                    })
        state["posts"].append((req.match_info["channel_id"], req))
        return web.json_response({"id": "777000111"}, status=201)

    async def react(req: web.Request) -> web.Response:
        state["reacts"].append((req.method, req.path))
        return web.Response(status=204)

    app = web.Application(client_max_size=32 * 1024 * 1024)
    app.router.add_get("/guilds/{guild_id}/members", members)
    app.router.add_post("/channels/{channel_id}/messages", post_message)
    app.router.add_put(
        "/channels/{c}/messages/{m}/reactions/{e}/@me", react)
    app.router.add_delete(
        "/channels/{c}/messages/{m}/reactions/{e}/@me", react)
    return app


class _StubSource:
    """`TrackSource` shaped to the port: resolve by query AND by locator
    (the lazy re-resolve at play time looks the track up by `locator`)."""

    def __init__(self, tracks: dict[str, ResolvedTrack]) -> None:
        self._tracks = tracks
        self.resolves: list[str] = []

    async def resolve(self, *, query_or_url: str):
        self.resolves.append(query_or_url)
        found = self._tracks.get(query_or_url)
        return [] if found is None else [found]

    async def related(self, *, locator: str):
        return []


class _StubSpeaker:
    async def speak(self, *, text: str) -> SpeakResult:
        return SpeakResult(
            available=True, audio=b"pcm-bytes", media_type="audio/wav")


class _StubProducer:
    """The `StreamProducer` seam `JournalPublisher` writes through — the
    enqueue receipt it returns is the contract's `(stream_id,
    canonical_id)` pair; no Redis needed to prove journal→publish order."""

    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, env) -> tuple[str, str]:
        self.enqueued.append(env.event_id)
        return ("0-1", env.event_id)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The whole synthetic split, built the way `deploy/discord-bridge/
    entry.py` builds it — minus only real Discord, real Redis and real DNS."""
    # `CommandTree.sync` IS the Discord HTTP call — the one seam a fake
    # Gateway cannot serve. Registration itself is asserted on the tree.
    syncs: list[int] = []

    async def _sync(self):
        syncs.append(int(time.time() * 1000))

    monkeypatch.setattr(discord.app_commands.CommandTree, "sync", _sync)

    controller_state = {
        "connected": False, "channel_id": None, "generation": None,
        "playback_id": None, "player": "idle", "streaming": False,
        "last_event_seq": 0,
        "join_calls": [], "exit_calls": [], "status_calls": [],
        "music_calls": [], "stream_calls": [],
    }
    rest_state = {"member_reads": [], "posts": [], "reacts": []}

    guild = _Guild(int(GUILD))
    text = _Channel(int(TEXT_CHANNEL), "general", guild, 0)
    voice = _Channel(int(VOICE_CHANNEL), "voice", guild, 2)
    announce = _Channel(int(ANNOUNCE), "announce", guild, 0)
    announce_2 = _Channel(int(ANNOUNCE_2), "announce-2", guild, 0)
    guild.channels.extend([text, voice, announce, announce_2])
    member = guild.add_member(
        _Member(int(MEMBER), voice=SimpleNamespace(channel=voice)))
    voice.members.append(member)
    client = FakeDiscordClient([guild])

    return {
        "controller_state": controller_state, "rest_state": rest_state,
        "guild": guild, "text": text, "voice": voice,
        "announce": announce, "announce_2": announce_2,
        "member": member, "client": client, "syncs": syncs, "tmp": tmp_path,
    }


@pytest.fixture
async def bridge(world, clean_pg_url):
    """The real bridge graph — disposable Postgres stores, real
    hub/manager/consumer, the real read/send implementations — served by a
    real aiohttp TestServer so every app-side call crosses a socket."""
    engine = create_sql_engine(clean_pg_url)
    controller = TestServer(_controller_app(world["controller_state"]))
    await controller.start_server()
    rest = TestServer(_discord_rest_app(world["rest_state"]))
    await rest.start_server()
    ctrl_url = str(controller.make_url("")).rstrip("/")
    rest_url = str(rest.make_url("")).rstrip("/")

    hub = VoiceControllerHub({GUILD: VoiceControllerClient(ctrl_url)})
    manager = MusicPlayerManager(
        queue_store=SqlMusicQueueStore(engine),
        session_store=SqlMusicSessionStore(engine),
        track_source=_StubSource({"song-a": TRACK, TRACK.locator: TRACK,
                                  "song-b": TRACK_B, TRACK_B.locator: TRACK_B}),
        idle_timeout_seconds=300.0,
        spawn=asyncio.ensure_future,
        playback_probe=hub.expected_playback_id,
        read_playback=hub.read_playback,
        generation_probe=hub.generation_for,
        drop_voice_session=hub.drop,
        exit_voice_session=hub.exit,
    )
    consumer = VoiceEventConsumer(manager, hub)
    journal = Journal(world["tmp"] / "journal.jsonl")
    # The projection is a MUTABLE dict read through `lambda: projection`
    # — the bridge resolves it fresh per call, so tests flip it mid-run
    # exactly the way the publisher rewriting the file does.
    projection: dict = {
        "revision": 7, "enabled": True, "rooms": [CONV],
        "room_receipt_senders": {CONV: [MEMBER]},
        "dm_allowed_senders": [],
        "music_channels": {GUILD: ANNOUNCE},
    }
    producer = _StubProducer()
    publisher = JournalPublisher(journal=journal, producer=producer)
    bridge = DiscordBridge(
        api_token=TOKEN, account_key=ACCOUNT,
        client=world["client"],
        bot_token="it-bot-token",  # noqa: S106 — a synthetic fixture token
        api_base=rest_url,
        correlation_key=b"\x11" * 32, key_version=1,
        custody_dir=world["tmp"] / "custody",
        projection=lambda: projection,
        capture=JournalCaptureSink(journal=journal, publisher=publisher),
        publisher=publisher,
        journal=journal,
        manager=manager, hub=hub,
        voice_guild_ids=frozenset({GUILD}),
        roster=DiscordRoster("it-bot-token", base_url=rest_url),
        voice_event_sink=consumer,
        max_speech_bytes=64,
    )
    # `entry.py`'s attach: the shared durable action service wrapping the
    # REAL REST executor — the same collaborator seats entry wires.
    bridge.attach(actions=DurableActionService(
        store=SqlBridgeStore(engine), platform="discord",
        account_key=ACCOUNT,
        execute=DiscordRestActionService(bridge).run,
        receipt_intents=lambda: receipt_intent_ids(journal),
        ops=frozenset({"send", "react", "edit", "typing"})))
    (world["tmp"] / "custody").mkdir()
    server = TestServer(bridge.routes())
    await server.start_server()
    world["endpoint"] = BridgeEndpoint(
        base_url=str(server.make_url("")).rstrip("/"), token=TOKEN)
    http = TestClient(server)
    await http.start_server()
    world.update(bridge=bridge, manager=manager, hub=hub,
                 consumer=consumer, journal=journal, engine=engine,
                 controller_server=controller, rest_server=rest,
                 projection=projection, http=http, producer=producer)
    yield world
    await bridge.close()
    if manager._liveness_task is not None:
        manager._liveness_task.cancel()
    await http.close()
    await engine.dispose()
    await controller.close()
    await rest.close()
    await server.close()


OPERATIONS = "/v1/platform/operations"
BLOBS = "/v1/platform/blobs"


async def _query(http, kind: str, params: dict) -> dict:
    return await envelope(await http.post(
        "/v1/platform/queries", json={"kind": kind, "params": params}, headers=AUTH))


async def _voice(http, kind: str, **effect) -> dict:
    """A `voice` operation on the bridge, or its `status` query for `kind="status"`."""
    if kind == "status":
        return await envelope(await http.post("/v1/voice/queries", headers=AUTH, json={
            "kind": "status", "params": effect}))
    return await envelope(await http.post("/v1/voice/operations", headers=AUTH, json={
        "operation_id": str(uuid4()), "kind": kind, "effect": effect}))


def _app_side(world) -> tuple[BridgeControlClient, BridgeDiscordAdapter]:
    """One 'app' — the real forwarding layer. Constructed, stopped and
    constructed again mid-test: recreation must leave the bridge's graph
    untouched."""
    control = BridgeControlClient(world["endpoint"])
    adapter = BridgeDiscordAdapter(
        delegate=BridgeAdapter("discord", world["endpoint"]),
        control=control, speaker=_StubSpeaker())
    return control, adapter


async def test_the_bridge_owns_the_whole_graph_while_the_app_proxies(bridge):
    world = bridge
    controller = world["controller_state"]
    client = world["client"]

    # --- the gateway connects; the whole ready-path runs on THIS client ---
    await world["bridge"].start()
    assert world["bridge"].current_client() is client
    assert world["bridge"].self_id == "999999999"
    # The slash tree registered `/music` AND `/discord` on THIS client's
    # tree — with no app process anywhere in the test.
    tree = client._connection._command_tree
    names = {c.name for c in tree.get_commands()}
    assert {"music", "discord"} <= names
    assert world["syncs"], "the tree was never synced"
    # Boot adoption asked the controller; it reported disconnected, so the
    # bridge holds NO session and never invented one.
    assert world["hub"].session_for(GUILD) is None
    assert controller["join_calls"] == []

    http = world["http"]

    # --- shared reads, from the bridge, over the live cache --------------
    body = await _query(http, "voice_presence", {"member_ids": [MEMBER]})
    assert body["outcome"] == "ok"
    assert body["payload"] == {
        MEMBER: {"channel_id": VOICE_CHANNEL, "channel_name": "voice"}}
    assert body["when"]["observed_at_ms"] > 0
    body = await _query(http, "member_presence", {"member_ids": [MEMBER]})
    assert body["payload"][MEMBER]["status"] == "online"
    body = await _query(http, "channels", {"guild_id": GUILD})
    assert body["payload"] == {
        "channels": {
            TEXT_CHANNEL: "general", ANNOUNCE: "announce",
            ANNOUNCE_2: "announce-2"},
        "threads": {}}
    body = await _query(http, "roster", {"conversation_id": CONV})
    assert {MEMBER} in {frozenset(m) for m in body["payload"]["members"]}
    assert world["rest_state"]["member_reads"] == [GUILD]

    # --- the app's forwarded music call, app process "absent" by fiat ----
    # The bridge alone executes it: requester-in-voice check against the
    # live cache, allow-list admission, join, resolve, play — the same
    # `DiscordMusicToolBridge` the slash command drives.
    resp = await http.post("/v1/music/play", json={
        "conversation_id": CONV, "sender_ids": [MEMBER],
        "query_or_url": "song-a"}, headers=AUTH)
    outcome = (await resp.json())["outcome"]
    assert outcome["status"] == "ok" and outcome["title"] == "Track A"
    assert len(controller["join_calls"]) == 1
    kind, play_body = controller["music_calls"][-1]
    assert kind == "play"
    assert play_body["generation"] == GEN
    assert play_body["stream_url"] == TRACK.stream_url

    # ONE session, ONE generation — the status query says what the app sees.
    state = (await _voice(http, "status", guild_id=GUILD))["payload"]
    assert state == {"connected": True, "session": True,
                     "generation": GEN, "channel_id": VOICE_CHANNEL}

    # A second track queues behind the first, through the same session.
    resp = await http.post("/v1/music/play", json={
        "conversation_id": CONV, "sender_ids": [MEMBER],
        "query_or_url": "song-b"}, headers=AUTH)
    assert (await resp.json())["outcome"]["queued"] is True
    assert len(controller["join_calls"]) == 1  # still ONE join, ONE generation

    # --- the REAL consumer: track-finished advances the persisted queue --
    body = await _voice(http, "report", **{
        "type": "track-finished", "event_id": "ev-1", "event_seq": 1,
        "guild_id": GUILD, "generation": GEN, "playback_id": controller["playback_id"]})
    assert body["outcome"] == "complete"
    plays = [b for k, b in controller["music_calls"] if k == "play"]
    assert plays[-1]["stream_url"] == TRACK_B.stream_url  # advanced
    assert plays[-1]["generation"] == GEN                 # same generation

    # --- "the app returns": a NEW forwarding layer over the same bridge --
    control, adapter = _app_side(world)
    tool = BridgeMusicTool(control=control)
    status = await tool.status(conversation_id=CONV)
    assert status.status == "ok"
    assert status.title == "Track B"

    # Bounded speech rides the SAME controller generation the play used.
    target = ConversationTarget(
        platform="discord", conversation_id=CONV,
        conversation_type="group", voice=True)
    result = await adapter.send(target=target, text="spoken reply")
    assert result.ok is True
    streamed = controller["stream_calls"][-1]
    assert streamed["generation"] == GEN
    assert base64.b64decode(streamed["audio_b64"]) == b"pcm-bytes"

    # --- inbound capture: journal-first, projection-gated ----------------
    raw = SimpleNamespace(
        id=10**17 + 5, content="hello room", attachments=[], stickers=[],
        mentions=[], reference=None,
        author=SimpleNamespace(id=int(MEMBER), display_name="Alice"),
        channel=world["text"], guild=world["guild"],
    )
    await world["bridge"]._capture_message(raw)
    # `start()`'s preflight left a probe record — envelopes are the payload.
    records = [r for r in world["journal"].recover() if "envelope" in r]
    assert len(records) == 1
    env = records[0]["envelope"]
    assert env["platform"] == "discord" and env["account_key"] == ACCOUNT
    assert env["conversation_id"] == CONV and env["admission_rev"] == 7
    # ...and the ONE publisher drained it to the stream, in order — a
    # separate task woken by the sink, so let it publish.
    for _ in range(200):
        if world["producer"].enqueued:
            break
        await asyncio.sleep(0.05)
    assert world["producer"].enqueued == [env["event_id"]]

    # --- fail-closed controls: a dead client is UNAVAILABLE, never empty --
    await client.close()  # the gateway dropped; the cache is unreadable
    body = await _query(http, "voice_presence", {"member_ids": [MEMBER]})
    assert body["outcome"] == "unavailable"
    body = await _query(http, "channels", {"guild_id": GUILD})
    # An unreadable cache is unavailable and stamps when it was observed —
    # it can never be mistaken for an empty guild.
    assert body["outcome"] == "unavailable" and body["payload"] is None
    assert isinstance(body["when"]["observed_at_ms"], int)
    resp = await http.post("/v1/music/play", json={
        "conversation_id": CONV, "sender_ids": [MEMBER],
        "query_or_url": "song-a"}, headers=AUTH)
    assert (await resp.json())["outcome"]["status"] == "unavailable"
    await control.close()


async def test_voice_death_reconciles_then_a_new_generation_serves(bridge):
    world = bridge
    controller = world["controller_state"]
    http = world["http"]

    await world["bridge"].start()
    await http.post("/v1/music/play", json={
        "conversation_id": CONV, "sender_ids": [MEMBER],
        "query_or_url": "song-a"}, headers=AUTH)
    assert world["hub"].generation_for(GUILD) == GEN

    # The controller's own death event reconciles the session — the REAL
    # VoiceEventConsumer path: event-id dedupe, generation check, teardown.
    death = {"type": "voice-death", "event_id": "ev-death", "event_seq": 1,
             "guild_id": GUILD, "generation": GEN}
    assert (await _voice(http, "report", **death))["outcome"] == "complete"
    assert world["hub"].session_for(GUILD) is None
    # A replayed delivery is a duplicate, not a second teardown.
    assert (await _voice(http, "report", **death))["payload"] == {"status": "duplicate"}

    # Speech with no session is refused — `no_connection`, never a silent drop.
    body = await _voice(http, "speak", guild_id=GUILD, generation=GEN, media_type="audio/wav",
                        audio_b64=base64.b64encode(b"pcm").decode())
    assert body["payload"]["error"]["kind"] == "no_connection"

    # A fresh join mints a NEW generation; speech lands on it, not gen-1.
    controller["generation"] = "gen-2"
    controller["connected"] = False
    body = await _voice(http, "join", guild_id=GUILD, channel_id=VOICE_CHANNEL)
    assert body["outcome"] == "complete"
    assert world["hub"].generation_for(GUILD) == "gen-2"
    body = await _voice(http, "speak", guild_id=GUILD, generation="gen-2",
                        media_type="audio/wav", audio_b64=base64.b64encode(b"pcm-2").decode())
    assert body["outcome"] == "complete"
    assert controller["stream_calls"][-1]["generation"] == "gen-2"
    assert base64.b64decode(
        controller["stream_calls"][-1]["audio_b64"]) == b"pcm-2"


@pytest.mark.parametrize("mime,filename", [
    ("image/png", "generated.png"),
    ("video/mp4", "generated.mp4"),
    ("audio/mpeg", "generated.mp3"),
    ("audio/ogg", "generated.ogg"),
    ("application/pdf", "generated.pdf"),
    ("application/octet-stream", "generated.bin"),
])
async def test_speech_is_bounded_and_actions_use_the_real_sender(bridge, mime, filename):
    world = bridge
    controller = world["controller_state"]
    http = world["http"]

    await world["bridge"].start()
    await http.post("/v1/music/play", json={
        "conversation_id": CONV, "sender_ids": [MEMBER],
        "query_or_url": "song-a"}, headers=AUTH)

    # Over the bound → 400, and nothing reached the controller.
    too_big = base64.b64encode(b"x" * (world["bridge"]._max_speech_bytes + 1)).decode()
    resp = await http.post("/v1/voice/operations", headers=AUTH, json={
        "operation_id": "big", "kind": "speak", "effect": {
            "guild_id": GUILD, "generation": GEN, "media_type": "audio/wav",
            "audio_b64": too_big}})
    assert resp.status == 400
    assert controller["stream_calls"] == []

    # A real send: through custody, through the action route, through the
    # REAL DiscordSender to the fake REST — thread override honoured.
    # The owner gate is the REAL `validate_owner`: a `live_invocation`
    # must name a committed pending outbound audit row for this
    # platform/account/target.
    key = "out-test-1"
    payload = b"generated-media" * 100_000
    put = await http.put(f"{BLOBS}/{key}", data=payload, headers=AUTH)
    assert (await put.json())["outcome"] == "complete"
    async with world["engine"].begin() as conn:
        rid = int((await conn.execute(sa.insert(MESSAGES).values(
            platform="discord", conversation_id=CONV,
            conversation_type="group", sender_id="999999999", ts=1,
            body="pending", is_nora=1, delivery_status="pending",
            raw_json=json.dumps({"tag_id": "t-1"}))
            .returning(MESSAGES.c.id))).scalar_one())
    resp = await http.post(OPERATIONS, json={
        "operation_id": "live:test:1",
        "owner": {"kind": "live_invocation", "id": str(rid)},
        "kind": "send",
        "effect": {
            "target": {"kind": "group", "id": CONV, "thread": "999888"},
            "text": "posted reply",
            "mentions": [{"start": 0, "length": 5, "recipient": MEMBER,
                          "handle": None}],
            "styles": [], "preview": None,
            "attachments": [{"key": key, "file_name": filename,
                             "media_type": mime}],
            "quote": None,
        }}, headers=AUTH)
    body = await resp.json()
    assert body["outcome"] == "complete"
    assert body["from"]["platform_ref"] == "777000111"
    # The post went to the THREAD's channel id, as DiscordSender routes it.
    channel_id, _req = world["rest_state"]["posts"][-1]
    assert channel_id == "999888"
    assert world["rest_state"]["uploads"][-1] == {
        "name": "files[0]", "filename": filename,
        "media_type": mime, "content": payload,
    }

    # Unauthenticated calls answer 401 before reaching any handler.
    resp = await http.post(OPERATIONS, json={
        "operation_id": "x", "owner": {"kind": "live_invocation", "id": "app"},
        "kind": "send", "effect": {}})
    assert resp.status == 401


def _interaction(
    world, *, user_id: str = MEMBER, channel=None, guild_id: str | None = GUILD,
) -> SimpleNamespace:
    """A slash-interaction shaped object: what `interaction_check` reads —
    guild/channel/user plus `response.send_message` recording the denial
    reply the check leaves."""
    sent: list = []
    ch = channel if channel is not None else world["text"]
    return SimpleNamespace(
        guild_id=int(guild_id) if guild_id else None,
        channel=ch if guild_id else None,
        channel_id=int(ch.id) if guild_id else None,
        user=SimpleNamespace(id=int(user_id)),
        response=SimpleNamespace(send_message=AsyncMock(
            side_effect=lambda *a, **k: sent.append((a, k)))),
        _sent=sent)


async def test_forwarded_music_ops_are_deny_closed(bridge):
    """`/v1/music/{op}` refuses BEFORE any effect: disabled projection,
    missing conversation_id, an unadmitted room, and a requester without
    the room's sender grant are all 403s — and the controller never sees
    a join. `dm_allowed_senders` is a DM grant, never the group allowlist."""
    world = bridge
    http = world["http"]
    await world["bridge"].start()

    async def play(conv: str = CONV, senders=(MEMBER,)) -> web.Response:
        return await http.post("/v1/music/play", json={
            "conversation_id": conv, "sender_ids": list(senders),
            "query_or_url": "song-a"}, headers=AUTH)

    # Disabled projection — denied before ANY effect.
    world["projection"]["enabled"] = False
    resp = await play()
    assert resp.status == 403
    assert (await resp.json())["error"] == "room_not_admitted"
    world["projection"]["enabled"] = True

    # Missing conversation_id — denied.
    resp = await http.post("/v1/music/play", json={
        "sender_ids": [MEMBER], "query_or_url": "song-a"}, headers=AUTH)
    assert resp.status == 403

    # A room the projection does not name — denied.
    resp = await play(conv=f"{GUILD}/100000000000000099")
    assert resp.status == 403

    # Room admitted, sender NOT in `room_receipt_senders` — denied.
    world["projection"]["room_receipt_senders"][CONV] = ["100000000000000005"]
    resp = await play()
    assert resp.status == 403
    assert (await resp.json())["error"] == "sender_not_admitted"

    # A DM grant alone does not authorize the group mutation.
    world["projection"]["dm_allowed_senders"] = [MEMBER]
    resp = await play()
    assert resp.status == 403
    world["projection"]["dm_allowed_senders"] = []

    # So far ZERO controller joins — nothing reached playout.
    assert world["controller_state"]["join_calls"] == []

    # Wildcard grant admits any sender; an allowed room member needs NO
    # DM grant.
    world["projection"]["room_receipt_senders"][CONV] = ["*"]
    resp = await play()
    assert (await resp.json())["outcome"]["status"] == "ok"
    world["projection"]["room_receipt_senders"][CONV] = [MEMBER]
    resp = await play()
    assert (await resp.json())["outcome"]["status"] == "ok"

    # The same gate binds skip/stop/volume (the mutations that carry no
    # requester on the wire): unknown room → refused before any effect.
    resp = await http.post("/v1/music/stop", json={
        "conversation_id": f"{GUILD}/100000000000000099"}, headers=AUTH)
    assert resp.status == 403
    resp = await http.post("/v1/music/set_volume", json={
        "conversation_id": CONV, "percent": 50}, headers=AUTH)
    # Gate passed → a real outcome, whatever the session's state.
    assert resp.status == 200 and "outcome" in await resp.json()


async def test_slash_commands_answer_to_the_projection_not_the_allowlist(bridge):
    """The tree-wide `interaction_check` gates guild+channel+requester off
    the CURRENT projection — every command on the tree, no parallel
    per-handler checks."""
    world = bridge
    await world["bridge"].start()
    b = world["bridge"]
    tree = world["client"]._connection._command_tree
    # The ONE seam — installed on the tree, covering /music AND /discord.
    assert tree.interaction_check == b._slash_admission

    # Granted member in the admitted room → the command may run.
    assert await b._slash_admission(_interaction(world)) is True

    # A member without the room's sender grant → denied + ephemeral reply
    # (they are in `dm_allowed_senders` below — the DM list is NOT the
    # group allowlist).
    world["projection"]["dm_allowed_senders"] = ["100000000000000005"]
    denied = _interaction(world, user_id="100000000000000005")
    assert await b._slash_admission(denied) is False
    assert denied.response.send_message.called

    # A thread resolves to its PARENT room — admitted.
    thread = _Channel(100000000000000020, "t", world["guild"], 11)
    thread.parent_id = int(TEXT_CHANNEL)
    world["guild"].threads.append(thread)
    assert await b._slash_admission(_interaction(world, channel=thread)) is True

    # A channel the projection does not name → denied even though the
    # guild is on the voice allow-list.
    other = _Channel(100000000000000021, "other", world["guild"], 0)
    world["guild"].channels.append(other)
    assert await b._slash_admission(
        _interaction(world, channel=other)) is False

    # Disabled projection → nothing admitted.
    world["projection"]["enabled"] = False
    assert await b._slash_admission(_interaction(world)) is False
    world["projection"]["enabled"] = True

    # DM → denied with the existing server line.
    dm = _interaction(world, guild_id=None)
    assert await b._slash_admission(dm) is False
    assert dm._sent[0][0][0].endswith("not a DM.")


async def test_music_channel_routing_follows_the_current_projection(bridge):
    """`music_channels[guild]` out of the CURRENT projection drives the
    announcement destination — before AND after an app-side proxy
    recreation and a policy change; an absent map falls back to where
    the session started."""
    world = bridge
    http = world["http"]
    await world["bridge"].start()

    async def play(query: str) -> None:
        resp = await http.post("/v1/music/play", json={
            "conversation_id": CONV, "sender_ids": [MEMBER],
            "query_or_url": query}, headers=AUTH)
        assert (await resp.json())["outcome"]["status"] in ("ok",)

    # Queue A + B; the skip's "now playing" announcement routes to the
    # PROJECTION's channel, not the room it was summoned from.
    await play("song-a")
    await play("song-b")
    resp = await http.post("/v1/music/skip", json={
        "conversation_id": CONV}, headers=AUTH)
    assert (await resp.json())["outcome"]["status"] == "ok"
    world["announce"].send.assert_called()
    assert "Track B" in world["announce"].send.call_args.args[0]
    world["text"].send.assert_not_called()

    # "The app" recreates its proxy — and policy rewires the channel mid-
    # flight; the next announcement reads the CURRENT projection.
    control, _adapter = _app_side(world)
    world["projection"]["music_channels"][GUILD] = ANNOUNCE_2
    world["announce"].send.reset_mock()
    await play("song-a")
    resp = await http.post("/v1/music/skip", json={
        "conversation_id": CONV}, headers=AUTH)
    world["announce_2"].send.assert_called()
    assert "Track A" in world["announce_2"].send.call_args.args[0]
    world["announce"].send.assert_not_called()

    # An absent map is the EXISTING fallback — the room the session
    # started in (CONV's channel).
    world["projection"].pop("music_channels")
    world["text"].send.reset_mock()
    world["announce_2"].send.reset_mock()
    await play("song-b")
    resp = await http.post("/v1/music/skip", json={
        "conversation_id": CONV}, headers=AUTH)
    world["text"].send.assert_called()
    assert "Track B" in world["text"].send.call_args.args[0]
    world["announce_2"].send.assert_not_called()
    await control.close()


async def test_attachment_body_limit_applies_only_to_uploads(bridge, monkeypatch):
    client = bridge["http"]
    limit = 2 * 1024 * 1024
    monkeypatch.setattr("nora.platform.discord.bridge_app.MAX_ATTACHMENT_BYTES", limit)
    for index, size in enumerate((1300 * 1024, limit)):
        payload = b"x" * size
        path = f"{BLOBS}/large-{index}"
        put = await client.put(path, data=payload, headers=AUTH)
        assert (await put.json())["outcome"] == "complete"
        got = await client.get(path, headers=AUTH)
        assert got.status == 200 and await got.read() == payload
        repeated = await client.put(path, data=payload, headers=AUTH)
        assert (await repeated.json())["outcome"] == "complete"
        conflict = await client.put(path, data=b"different bytes", headers=AUTH)
        assert (await conflict.json())["payload"]["error"]["kind"] == "conflict"
        unchanged = await client.get(path, headers=AUTH)
        assert unchanged.status == 200 and await unchanged.read() == payload
    refused = await client.put(f"{BLOBS}/too-large", data=b"x" * (limit + 1), headers=AUTH)
    assert (await envelope(refused))["payload"]["error"]["kind"] == "too_large"
    missing = await client.get(f"{BLOBS}/too-large", headers=AUTH)
    assert (await missing.json())["outcome"] == "nothing_found"
    ordinary = await client.post(OPERATIONS, data=b"x" * (1300 * 1024), headers=AUTH)
    assert ordinary.status == 413


@pytest.mark.parametrize("method", ["PUT", "GET"])
async def test_attachment_paths_stay_in_custody(bridge, method):
    client = bridge["http"]
    outside = bridge["tmp"] / "outside"
    outside.write_bytes(b"outside custody")
    (bridge["tmp"] / "custody" / "escape").symlink_to(outside)
    refused = await client.request(method, f"{BLOBS}/escape",
                                   data=b"outside custody", headers=AUTH)
    assert refused.status == 400
    assert outside.read_bytes() == b"outside custody"
    accepted = await client.put(f"{BLOBS}/safe", data=b"safe", headers=AUTH)
    assert (await accepted.json())["outcome"] == "complete"
    got = await client.get(f"{BLOBS}/safe", headers=AUTH)
    assert got.status == 200 and await got.read() == b"safe"


async def test_actions_and_capture_fail_closed_without_collaborators(bridge):
    """The collaborator fence: no `ActionService` → an operation is
    `unavailable` (503), and NO SDK side-effect is attempted; no
    `CaptureSink` → an admitted room's event is dropped, not written
    to journal or stream half-formed."""
    world = bridge
    http = world["http"]
    await world["bridge"].start()
    b = world["bridge"]

    actions, capture = b._actions, b._capture_sink
    try:
        b._actions = None
        posts_before = len(world["rest_state"]["posts"])
        resp = await http.post(OPERATIONS, json={
            "operation_id": "w:test:1",
            "owner": {"kind": "work", "id": "w", "generation": 1},
            "kind": "send",
            "effect": {"target": {"kind": "group", "id": CONV},
                       "text": "must never post"}}, headers=AUTH)
        body = await resp.json()
        assert resp.status == 503 and body["outcome"] == "unavailable"
        assert body["payload"]["error"]["kind"] == "unavailable"
        assert len(world["rest_state"]["posts"]) == posts_before

        b._capture_sink = None
        raw = SimpleNamespace(
            id=10**17 + 9, content="held", attachments=[], stickers=[],
            mentions=[], reference=None,
            author=SimpleNamespace(id=int(MEMBER), display_name="Alice"),
            channel=world["text"], guild=world["guild"])
        await b._capture_message(raw)
        # The preflight probe is journaled; the held event must NOT be.
        assert [r for r in world["journal"].recover()
                if "envelope" in r] == []
    finally:
        b._actions, b._capture_sink = actions, capture


async def test_the_bridge_serves_the_platform_contract_routes(bridge, monkeypatch):
    assert_serves_the_contract(bridge["bridge"].routes())
    assert (await bridge["http"].get("/ready", headers=AUTH)).status == 200
    monkeypatch.setattr(bridge["client"], "is_ready", lambda: False)
    assert (await bridge["http"].get("/ready", headers=AUTH)).status == 503


async def test_operation_outcomes_follow_the_contract(bridge, monkeypatch):
    b = bridge["bridge"]
    await b.start()
    await drive_outcomes(Bridge(
        http=bridge["http"], auth=AUTH, store=b._actions._store, engine=bridge["engine"],
        platform="discord", account_key=ACCOUNT, conversation_id=CONV,
        no_ledger=lambda: swapped(b, "_actions"), logged_out=lambda: swapped(b, "self_id"),
        query_ok=("channels", {"guild_id": GUILD}),
        query_unavailable=("channels", {"guild_id": "1"}),
        blob_limit=lambda n: monkeypatch.setattr(
            "nora.platform.discord.bridge_app.MAX_ATTACHMENT_BYTES", n)))


class _Emoji:
    """`PartialEmoji`'s two str() shapes — `name` for unicode, `name:id`
    for custom — the value `Touch.emoji` and the frame fingerprint share."""
    def __init__(self, name: str, eid: int | None = None) -> None:
        self.name = name
        self.id = eid

    def __str__(self) -> str:
        return f"{self.name}:{self.id}" if self.id else self.name


async def test_clockless_transitions_get_distinct_durable_identities(bridge):
    """Finding 2 at the real capture seam: generated Gateway reaction
    dispatches flow `_record_wire_frame` (the socket boundary, where `s`
    still rides) then `_capture_touch` (the parsed surface the listener
    calls) — the same order discord.py dispatches them. Wire-identified
    transitions stay distinct across add/remove/add, an at-least-once
    redelivery of the SAME frame keeps ONE identity, and a frame-less
    event resolves through the journal's own durable ledger."""
    world = bridge
    await world["bridge"].start()
    b = world["bridge"]
    journal = world["journal"]
    # The wire boundary needs the session the gateway established — the
    # fake client gains the `ws` attribute the real discord.py client has.
    world["client"].ws = SimpleNamespace(session_id="it-sess-1")

    target = 10**17 + 9
    d = {"channel_id": TEXT_CHANNEL, "message_id": target,
         "user_id": MEMBER, "emoji": {"name": "\U0001F44D", "id": None},
         "type": 0}

    def reaction() -> SimpleNamespace:
        return SimpleNamespace(
            guild_id=int(GUILD), user_id=int(MEMBER), message_id=target,
            channel_id=int(TEXT_CHANNEL), emoji=_Emoji("\U0001F44D"),
            type=SimpleNamespace(value=0))

    # add → remove → add on the wire: three real dispatches (s 21/22/23).
    for t, s, kind in (("MESSAGE_REACTION_ADD", 21, "reaction_add"),
                       ("MESSAGE_REACTION_REMOVE", 22, "reaction_remove"),
                       ("MESSAGE_REACTION_ADD", 23, "reaction_add")):
        await b._record_wire_frame({"t": t, "s": s, "d": dict(d)})
        await b._capture_touch(kind, reaction())
    envs = [Envelope.from_fields(r["envelope"])
            for r in journal.recover() if "envelope" in r]
    assert len(envs) == 3
    assert len({e.event_id for e in envs}) == 3
    # The transition ledger rides the same journal records — durable, and
    # continuing the sequence a later frame-less event must see.
    transitions = [r["transition"] for r in journal.recover()
                   if "transition" in r]
    assert [t["state"] for t in transitions] == ["add", "remove", "add"]

    # At-least-once redelivery of the SAME frame — same session, same s —
    # keeps the SAME identity: the queue's dedupe suppresses it downstream.
    await b._record_wire_frame(
        {"t": "MESSAGE_REACTION_ADD", "s": 21, "d": dict(d)})
    await b._capture_touch("reaction_add", reaction())
    envs = [Envelope.from_fields(r["envelope"])
            for r in journal.recover() if "envelope" in r]
    assert len(envs) == 4
    assert envs[3].event_id == envs[0].event_id

    # A frame-less parsed event whose observation DISAGREES with the
    # recorded state is the reconnect-replay ambiguity, nothing provably
    # not a delayed old transition (design/platform-bridges/02-wire-and-platforms.md)
    # — retained as journal evidence, never published under a minted seq.
    b._frames.clear()
    await b._capture_touch("reaction_remove", reaction())
    envs = [Envelope.from_fields(r["envelope"])
            for r in journal.recover() if "envelope" in r]
    assert len(envs) == 4                     # nothing new published
    ambiguous = [r["ambiguous"] for r in journal.recover()
                 if "ambiguous" in r]
    assert len(ambiguous) == 1 and ambiguous[0]["state"] == "remove"

    # A frame-less SAME-state callback is an identical redelivery: it
    # resolves to the upstream identity its transition committed under —
    # here the s=21 redelivery's `g:` (the ledger's latest for this state).
    await b._capture_touch("reaction_add", reaction())
    envs = [Envelope.from_fields(r["envelope"])
            for r in journal.recover() if "envelope" in r]
    assert len(envs) == 5
    assert envs[4].event_id == envs[3].event_id   # the s=21 g: identity

    # A delete names the message it removes — the durable identity is the
    # target, and a second delete of a DIFFERENT message never collides.
    for mid in (target, target + 1):
        await b._capture_touch("delete", SimpleNamespace(
            guild_id=int(GUILD), message_id=mid,
            channel_id=int(TEXT_CHANNEL)))
    envs = [Envelope.from_fields(r["envelope"])
            for r in journal.recover() if "envelope" in r]
    assert len(envs) == 7
    deletes = [e for e in envs if e.kind == "delete"]
    assert deletes[0].event_id != deletes[1].event_id
