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

    async def _fake_listen(self, on_message_cb, on_edit_cb=None, on_touch_cb=None):
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


async def test_an_edit_reaches_the_same_callback_marked_as_an_edit(monkeypatch):
    """`on_raw_message_edit`'s message goes
    through the SAME parse as a new one, then is marked with its own snowflake
    as the target and stamped with `edited_at`. It reaches the one `on_message`
    every network feeds, where ingest routes it."""
    import datetime as _dt

    from nora.domain.models import EditTarget

    adapter = DiscordAdapter("tok")
    adapter._identity = PlatformIdentity(platform="discord", primary="999")
    adapter._own_id = "999"
    guild, channel = FakeGuild(id=1), FakeChannel(id=2)
    edited = FakeMessage(
        id=4, author=FakeUser(id=42), guild=guild, channel=channel,
        content="hello, fixed",
    )
    edited.edited_at = _dt.datetime(2026, 9, 23, 12, 0, tzinfo=_dt.UTC)
    self_edit = FakeMessage(id=5, author=FakeUser(id=999), guild=guild, channel=channel)
    self_edit.edited_at = edited.edited_at
    # A link preview resolving, a pin, a thread: an update to a message its
    # author never edited. No `edited_at`, so not an edit.
    embed_only = FakeMessage(
        id=6, author=FakeUser(id=42), guild=guild, channel=channel, content="see link",
    )
    embed_only.edited_at = None

    async def _fake_listen(self, on_message_cb, on_edit_cb=None, on_touch_cb=None):
        assert on_edit_cb is not None, "the adapter must wire the edit callback"
        await on_edit_cb(self_edit)
        await on_edit_cb(embed_only)
        await on_edit_cb(edited)

    monkeypatch.setattr(DiscordListener, "listen", _fake_listen)
    received = []

    async def _capture(message):
        received.append(message)

    await adapter.listen(on_message=_capture)

    assert len(received) == 1, \
        "Nora's own edit (R-12) and a non-edit update must not reach ingest"
    assert received[0].edit_of == EditTarget(message_id="4")
    assert received[0].body == "hello, fixed"
    assert received[0].timestamp == int(edited.edited_at.timestamp() * 1000)


async def test_a_reaction_and_a_delete_reach_the_same_callback_as_touches(monkeypatch):
    """The adapter wires the listener's touch
    callback and parses each raw event into a `Touch`; Nora's own reaction
    never reaches ingest (R-12)."""
    from types import SimpleNamespace

    from nora.domain.models import EditTarget

    adapter = DiscordAdapter("tok")
    adapter._identity = PlatformIdentity(platform="discord", primary="999")
    adapter._own_id = "999"
    reaction = SimpleNamespace(message_id=4, channel_id=2, guild_id=1, user_id=42, emoji="👍")
    own = SimpleNamespace(message_id=4, channel_id=2, guild_id=1, user_id=999, emoji="👀")
    delete = SimpleNamespace(message_id=4, channel_id=2, guild_id=1)

    async def _fake_listen(self, on_message_cb, on_edit_cb=None, on_touch_cb=None):
        assert on_touch_cb is not None, "the adapter must wire the touch callback"
        await on_touch_cb("reaction_add", own)
        await on_touch_cb("reaction_add", reaction)
        await on_touch_cb("reaction_remove", reaction)
        await on_touch_cb("delete", delete)

    monkeypatch.setattr(DiscordListener, "listen", _fake_listen)
    received = []

    async def _capture(message):
        received.append(message)

    await adapter.listen(on_message=_capture)
    assert [(m.touch.kind, m.touch.removed, m.sender_id) for m in received] == [
        ("reaction", False, "42"), ("reaction", True, "42"), ("delete", False, ""),
    ]
    assert all(m.touch.target == EditTarget(message_id="4") for m in received)


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


async def test_own_handle_reuses_the_startup_users_me_call(aiohttp_server):
    """No extra per-turn network call — Discord's
    `own_handle` must answer from the SAME `/users/@me` response
    `resolve_identity()` fetches once, not a fresh request each time."""
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return web.json_response({"id": "555", "username": "nora", "discriminator": "1234"})

    app = web.Application()
    app.router.add_get("/users/@me", handler)
    server = await aiohttp_server(app)

    adapter = DiscordAdapter("tok", base_url=str(server.make_url("")).rstrip("/"))
    first = await adapter.own_handle()
    second = await adapter.own_handle()

    assert first == "@nora#1234"
    assert second == "@nora#1234"
    assert calls == 1, "own_handle must reuse resolve_identity()'s single call, never re-fetch"


async def test_own_handle_drops_the_tag_for_a_migrated_discriminator(aiohttp_server):
    async def handler(request):
        return web.json_response({"id": "555", "username": "nora", "discriminator": "0"})

    app = web.Application()
    app.router.add_get("/users/@me", handler)
    server = await aiohttp_server(app)

    adapter = DiscordAdapter("tok", base_url=str(server.make_url("")).rstrip("/"))

    assert await adapter.own_handle() == "@nora"


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
    through the audio-controller's `/stream` endpoint. Routed on
    `ConversationTarget.voice`: the conversation is the guild's ordinary text
    room, so the room cannot say which way to answer."""

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


async def test_voice_channel_send_without_a_voice_session_falls_back_to_text(aiohttp_server):
    """No session for the guild (never joined), so speech cannot play — and
    the reply is POSTED instead of dropped. Speech is a surface of an ordinary
    room, so the target IS a text channel.

    Asserted on the REST call, not just on `ok`: without a `/messages` route
    this test would pass on a failed fallback and prove nothing."""

    posted: list[str] = []

    async def messages_handler(request: web.Request) -> web.Response:
        posted.append(request.path)
        return web.json_response({"id": "1"})

    app = web.Application()
    app.router.add_post("/channels/{cid}/messages", messages_handler)
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

    assert result.ok is True
    assert posted == ["/channels/999/messages"], "the reply reached the room"


async def test_a_voice_target_with_no_speaker_wired_falls_back_to_text(aiohttp_server):
    """The unwired posture: the send falls back to text, so the answer is not
    lost and the room can still read it."""

    posted: list[str] = []

    async def messages_handler(request: web.Request) -> web.Response:
        posted.append(request.path)
        return web.json_response({"id": "1"})

    app = web.Application()
    app.router.add_post("/channels/{cid}/messages", messages_handler)
    server = await aiohttp_server(app)

    adapter = DiscordAdapter("tok", base_url=str(server.make_url("")).rstrip("/"))
    result = await adapter.send(
        target=ConversationTarget("discord", "111/999", "group", voice=True),
        text="喂 Nora。",
    )

    assert result.ok is True
    assert posted == ["/channels/999/messages"]


async def test_non_speakable_text_is_not_spoken_but_is_posted(aiohttp_server):
    """A machine token must never be read aloud. `speakable`'s docstring says the
    caller should "send it text-only", and the caller does, so a hash-bearing
    answer reaches the room instead of vanishing into a silent-failure counter."""

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
        target=ConversationTarget("discord", "111/999", "group", voice=True),
        text="個 hash 係 a3f2c1d4e5b60718293a4b5c",
    )

    assert result.ok is True
    assert speaker.calls == [], "a machine token is never synthesized"
    assert posted == ["/channels/999/messages"], "but the room still gets it"
    assert speaker.calls == [], "must not synthesize a machine token either"


async def test_a_typed_turn_in_a_voice_enabled_room_still_goes_out_as_text(aiohttp_server):
    """A guild's speech lands in its ordinary text room, so one
    `conversation_id` carries BOTH surfaces. Routing on the room would answer
    every typed message in that room aloud, and POST nothing to the channel the
    person was reading.

    `voice=False` is the default, so this is also the assertion that the
    default does not become "speak"."""

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


async def test_a_recorded_guild_with_no_controller_is_never_synthesized(aiohttp_server):
    """`policy.toml`'s `voice = true` and `[speech.tts] sidecars` are independent
    on purpose — a guild's speech may be RECORDED with no controller able to
    play a reply.

    A guard in `_send_voice` that only asks whether a speaker and hub exist AT
    ALL passes, because they exist for the OTHER guild, and every spoken turn in
    the unwired guild would bill MiniMax for a synthesis and discard the audio.
    The send is refused before the vendor call; the answer still reaches the
    room as text."""

    posted: list[str] = []

    async def messages_handler(request: web.Request) -> web.Response:
        posted.append(request.path)
        return web.json_response({"id": "1"})

    app = web.Application()
    app.router.add_post("/channels/{cid}/messages", messages_handler)
    server = await aiohttp_server(app)

    speaker = _FakeSpeaker(
        SpeakResult(available=True, audio=b"mp3", media_type="audio/mpeg")
    )
    # A hub that HAS a controller — for guild 111, not for 222.
    hub = VoiceControllerHub(
        {"111": VoiceControllerClient(str(server.make_url("")).rstrip("/"))}
    )
    adapter = DiscordAdapter(
        "tok", base_url=str(server.make_url("")).rstrip("/"),
        speaker=speaker, voice_hub=hub,
        voice_guild_ids=frozenset({"111"}),
    )

    result = await adapter.send(
        target=ConversationTarget("discord", "222/333", "group", voice=True),
        text="收到，聽日見。",
    )

    assert speaker.calls == [], "no synthesis is paid for"
    assert result.ok is True
    assert posted == ["/channels/333/messages"], "the room still gets the answer"


async def test_a_channels_message_and_its_edit_are_ingested_in_order(monkeypatch):
    """discord.py runs each gateway event as its own task. A message whose
    ingest is still running (custody, transcription) must finish before its
    edit is looked up, or the edit finds nothing and is dropped as an orphan."""
    import asyncio
    import datetime as _dt

    adapter = DiscordAdapter("tok")
    adapter._identity = PlatformIdentity(platform="discord", primary="999")
    adapter._own_id = "999"
    guild, channel = FakeGuild(id=1), FakeChannel(id=2)
    original = FakeMessage(id=4, author=FakeUser(id=42), guild=guild, channel=channel,
                           content="v1")
    edited = FakeMessage(id=4, author=FakeUser(id=42), guild=guild, channel=channel,
                         content="v2")
    edited.edited_at = _dt.datetime(2026, 9, 24, tzinfo=_dt.UTC)
    order: list[str] = []

    async def _fake_listen(self, on_message_cb, on_edit_cb=None, on_touch_cb=None):
        # Both dispatched at once, as discord.py does.
        await asyncio.gather(on_message_cb(original), on_edit_cb(edited))

    async def _slow_ingest(message):
        order.append(f"start {message.body}")
        await asyncio.sleep(0.05 if message.edit_of is None else 0)
        order.append(f"end {message.body}")

    monkeypatch.setattr(DiscordListener, "listen", _fake_listen)
    await adapter.listen(on_message=_slow_ingest)
    assert order == ["start v1", "end v1", "start v2", "end v2"]
