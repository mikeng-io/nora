from dataclasses import dataclass, field

from aiohttp import web

from nora.domain.identity import Identifier
from nora.domain.models import ConversationTarget
from nora.domain.ports import PlatformIdentity, SpeakResult
from nora.infra.voice.http_controller import VoiceControllerClient
from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.listener import DiscordListener


@dataclass
class FakeUser:
    id: int
    display_name: str = "someone"


@dataclass
class FakeGuild:
    id: int


@dataclass
class FakeChannel:
    id: int


@dataclass
class FakeMessage:
    id: int
    author: FakeUser
    guild: FakeGuild | None
    channel: FakeChannel
    content: str = "hi"
    mentions: list = field(default_factory=list)
    attachments: list = field(default_factory=list)
    reference: object = None


def test_adapter_declares_its_platform():
    assert DiscordAdapter("tok").platform == "discord"


def test_parse_is_not_on_the_public_surface():
    assert not hasattr(DiscordAdapter, "parse")


async def test_listen_parses_and_forwards_only_parseable_events(monkeypatch):
    """White-box swap of `adapter._listener.listen`, mirroring
    `tests/test_signal_adapter.py::test_listen_parses_and_forwards_only_parseable_events`
    — the Gateway connection itself is `test_discord_listener.py`'s job."""
    adapter = DiscordAdapter("tok")
    # Bypass the network resolve_identity() call — own id supplied directly.
    adapter._identity = PlatformIdentity(platform="discord", primary="999")
    adapter._own_id = "999"

    guild, channel = FakeGuild(id=1), FakeChannel(id=2)
    events = [
        FakeMessage(id=1, author=FakeUser(id=42), guild=None, channel=channel),  # DM
        FakeMessage(id=2, author=FakeUser(id=999), guild=guild, channel=channel),  # self-send
        FakeMessage(  # empty
            id=3, author=FakeUser(id=42), guild=guild, channel=channel, content=""
        ),
        FakeMessage(  # kept
            id=4, author=FakeUser(id=42), guild=guild, channel=channel, content="hello"
        ),
    ]

    async def _fake_listen(self, on_message_cb):
        for event in events:
            await on_message_cb(event)

    # Patched on the CLASS, not on an instance: `listen()` builds a fresh
    # `DiscordListener` (and a fresh `discord.Client`) per attempt, because a
    # closed client cannot be restarted — so an instance-level patch applied
    # beforehand would be thrown away by the very call under test.
    monkeypatch.setattr(DiscordListener, "listen", _fake_listen)

    received = []

    async def _capture(message):
        received.append(message)

    await adapter.listen(on_message=_capture)

    assert len(received) == 1
    assert received[0].body == "hello"
    assert received[0].sender_id == "42"


async def test_health_reports_connection_state():
    adapter = DiscordAdapter("tok")
    health = await adapter.health()
    assert health.platform == "discord"
    assert health.connected is False
    assert health.last_event_age_seconds is None


async def test_send_delegates_to_the_sender():
    adapter = DiscordAdapter("tok", base_url="http://127.0.0.1:1")
    result = await adapter.send(
        target=ConversationTarget("discord", "1/2", "group"), text="x"
    )
    assert result.ok is False  # unreachable API -> not ok, never an exception


async def test_react_without_a_message_id_is_unsupported():
    """Signal's `(author, timestamp)` pair cannot name a Discord message —
    `snowflake.timestamp_ms` throws the low 22 bits away — so with no snowflake
    there is still nothing to react to, and that is `supported=False`, not a
    failed attempt."""
    adapter = DiscordAdapter("tok")
    result = await adapter.react(
        target=ConversationTarget("discord", "1/2", "group"),
        emoji="\U0001f44d",
        to_author="42",
        to_timestamp=1234,
    )
    assert result.ok is False
    assert result.supported is False


async def test_react_delegates_to_the_reactor_with_the_snowflake(aiohttp_server):
    """The adapter half of the reaction path: given a message id it must
    actually call Discord, not keep answering the old `supported=False`.

    Drives the REAL `DiscordReactor` against a stand-in endpoint rather than
    asserting on a mock, so a wiring that dropped `to_message_id` on the floor
    would reach the server without one and fail here.
    """
    seen: list[tuple[str, str, str]] = []

    async def handler(request):
        seen.append((
            request.method,
            request.match_info["channel"],
            request.match_info["message"],
        ))
        return web.Response(status=204)

    app = web.Application()
    app.router.add_route(
        "*", "/channels/{channel}/messages/{message}/reactions/{emoji}/@me", handler,
    )
    server = await aiohttp_server(app)

    adapter = DiscordAdapter("tok", base_url=str(server.make_url("")).rstrip("/"))
    result = await adapter.react(
        target=ConversationTarget("discord", "111/222", "group"),
        emoji="\U0001f440",
        to_author="42",
        to_timestamp=1234,
        to_message_id="999888777",
    )

    assert result.ok is True
    assert result.supported is True
    assert seen == [("PUT", "222", "999888777")], (
        "the reaction must address the channel and the message snowflake"
    )


async def test_resolve_identity_returns_a_platform_identity(aiohttp_server):
    async def handler(request):
        return web.json_response({"id": "555", "username": "nora"})

    app = web.Application()
    app.router.add_get("/users/@me", handler)
    server = await aiohttp_server(app)

    adapter = DiscordAdapter("tok", base_url=str(server.make_url("")).rstrip("/"))
    identity = await adapter.resolve_identity()

    assert identity.platform == "discord"
    assert identity.primary == "555"
    assert Identifier("discord", "user_id", "555") in identity.identifiers


class _FakeSpeaker:
    """The minimal shape `DiscordAdapter._send_voice` reads — same seam the
    production wiring uses (`nora.infra.tts.MiniMaxTtsSpeaker`)."""

    def __init__(self, result: SpeakResult) -> None:
        self.result = result
        self.calls: list[str] = []

    async def speak(self, *, text: str, voice: str = "") -> SpeakResult:
        self.calls.append(text)
        return self.result


async def test_voice_channel_send_goes_through_speaker_not_rest(aiohttp_server):
    """A SPOKEN turn must never hit the REST text path — it is answered aloud
    through the audio-controller's `/stream` endpoint (design/voice-channel/07
    §3). Routed on `ConversationTarget.voice` since 2026-09-17 (§12.2
    reversed): the conversation is the guild's ordinary text room now, so the
    room can no longer say which way to answer."""

    seen: list[str] = []

    async def join_handler(request: web.Request) -> web.Response:
        seen.append(request.method + request.path)
        return web.json_response(
            {"ok": True, "request_id": "r",
             "state": {"voice": "connected", "generation": "gen-1", "channel_id": "222",
                       "playback_id": None, "speech_queue": 0}},
        )

    async def stream_handler(request: web.Request) -> web.Response:
        seen.append(request.method + request.path)
        body = await request.json()
        seen.append(body.get("media_type", ""))
        return web.json_response(
            {"ok": True, "request_id": "r",
             "state": {"voice": "connected", "generation": "gen-1", "channel_id": "222",
                       "playback_id": None, "speech_queue": 1}},
            status=202,
        )

    app = web.Application()
    app.router.add_post("/voice/join", join_handler)
    app.router.add_post("/stream", stream_handler)
    server = await aiohttp_server(app)

    hub = VoiceControllerHub(
        {"111": VoiceControllerClient(str(server.make_url("")).rstrip("/"))}
    )
    await hub.join("111", "222")
    speaker = _FakeSpeaker(
        SpeakResult(available=True, audio=b"\x00\x01fake-mp3", media_type="audio/mpeg")
    )
    adapter = DiscordAdapter(
        "tok", base_url=str(server.make_url("")).rstrip("/"),
        speaker=speaker, voice_hub=hub,
        voice_guild_ids=frozenset({"111"}),
    )
    result = await adapter.send(
        target=ConversationTarget("discord", "111/999", "group", voice=True),
        text="喂 Nora，收到。",
    )

    assert result.ok is True
    assert speaker.calls == ["喂 Nora，收到。"]
    assert seen == ["POST/voice/join", "POST/stream", "audio/mpeg"], (
        "must route to the controller /stream, never REST"
    )


async def test_voice_channel_send_without_a_voice_session_is_a_refused_send(aiohttp_server):
    """Fail-closed: the hub has no session for the guild (never joined), so
    speech must not pretend to play."""

    app = web.Application()
    server = await aiohttp_server(app)

    hub = VoiceControllerHub(
        {"111": VoiceControllerClient(str(server.make_url("")).rstrip("/"))}
    )
    speaker = _FakeSpeaker(SpeakResult(available=True, audio=b"mp3", media_type="audio/mpeg"))
    adapter = DiscordAdapter(
        "tok", base_url=str(server.make_url("")).rstrip("/"),
        speaker=speaker, voice_hub=hub,
        voice_guild_ids=frozenset({"111"}),
    )
    result = await adapter.send(
        target=ConversationTarget("discord", "111/999", "group", voice=True),
        text="喂 Nora，收到。",
    )

    assert result.ok is False


async def test_voice_channel_send_without_speaker_is_silent_fail(aiohttp_server):
    """Fail-closed: a voice-channel target with no speaker/endpoint returns a
    refused send — never a text POST that Discord would 400 anyway."""

    app = web.Application()
    server = await aiohttp_server(app)

    adapter = DiscordAdapter("tok", base_url=str(server.make_url("")).rstrip("/"))
    result = await adapter.send(
        target=ConversationTarget("discord", "111/999", "group", voice=True),
        text="喂 Nora。",
    )

    assert result.ok is False


async def test_non_speakable_text_is_refused_on_voice_channel(aiohttp_server):
    """design/voice-channel/01 §7 — a machine token must never be read aloud."""

    app = web.Application()
    server = await aiohttp_server(app)

    speaker = _FakeSpeaker(SpeakResult(available=True, audio=b"mp3", media_type="audio/mpeg"))
    hub = VoiceControllerHub(
        {"111": VoiceControllerClient(str(server.make_url("")).rstrip("/"))}
    )
    adapter = DiscordAdapter(
        "tok", base_url=str(server.make_url("")).rstrip("/"),
        speaker=speaker, voice_hub=hub,
        voice_guild_ids=frozenset({"111"}),
    )
    result = await adapter.send(
        target=ConversationTarget("discord", "111/999", "group", voice=True),
        text="個 hash 係 a3f2c1d4e5b60718293a4b5c",
    )

    assert result.ok is False
    assert speaker.calls == [], "must not synthesize a machine token either"


async def test_a_typed_turn_in_a_voice_enabled_room_still_goes_out_as_text(aiohttp_server):
    """THE REGRESSION THIS GUARDS (design/voice-channel/07 §12.2, reversed
    2026-09-17). A guild's speech now lands in its ordinary text room, so one
    `conversation_id` carries BOTH surfaces. Routing on the room — the rule
    before this reversal — would have answered every typed message in that
    room aloud, and POSTed nothing to the channel the person was reading.

    `voice=False` is the default, so this is also the assertion that the
    default did not quietly become "speak"."""

    posted: list[str] = []

    async def messages_handler(request: web.Request) -> web.Response:
        posted.append(request.path)
        return web.json_response({"id": "1"})

    app = web.Application()
    app.router.add_post("/channels/{cid}/messages", messages_handler)
    server = await aiohttp_server(app)

    speaker = _FakeSpeaker(SpeakResult(available=True, audio=b"mp3", media_type="audio/mpeg"))
    hub = VoiceControllerHub(
        {"111": VoiceControllerClient(str(server.make_url("")).rstrip("/"))}
    )
    adapter = DiscordAdapter(
        "tok", base_url=str(server.make_url("")).rstrip("/"),
        speaker=speaker, voice_hub=hub,
        voice_guild_ids=frozenset({"111"}),
    )
    result = await adapter.send(
        target=ConversationTarget("discord", "111/222", "group"), text="收到。",
    )

    assert result.ok is True
    assert speaker.calls == [], "a typed turn must never be synthesized"
    assert posted == ["/channels/222/messages"], "a typed turn goes out as REST text"
