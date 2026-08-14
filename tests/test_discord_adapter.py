from dataclasses import dataclass, field

from aiohttp import web

from nora.domain.identity import Identifier
from nora.domain.models import ConversationTarget
from nora.domain.ports import PlatformIdentity
from nora.platform.discord.adapter import DiscordAdapter


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


async def test_listen_parses_and_forwards_only_parseable_events():
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

    async def _fake_listen(on_message_cb):
        for event in events:
            await on_message_cb(event)

    adapter._listener.listen = _fake_listen

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
