import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.platform.discord.send import DiscordSender
from nora.platform.discord.snowflake import timestamp_ms

TARGET = ConversationTarget("discord", "1/2", "group")


@pytest.fixture
async def rest_server(aiohttp_server):
    calls: list[dict] = []
    headers: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append(body)
        headers.append(dict(request.headers))
        return web.json_response({"id": "999999999999999999"}, status=200)

    app = web.Application()
    app.router.add_post("/channels/2/messages", handler)
    server = await aiohttp_server(app)
    server.calls = calls
    server.headers = headers
    return server


async def test_send_posts_content_to_the_channel_from_the_conversation_id(rest_server):
    sender = DiscordSender("tok", base_url=str(rest_server.make_url("")).rstrip("/"))

    result = await sender.send(TARGET, "hello")

    assert result.ok is True
    assert rest_server.calls[0] == {"content": "hello"}


async def test_send_authenticates_with_the_bot_token(rest_server):
    sender = DiscordSender("my-secret-token", base_url=str(rest_server.make_url("")).rstrip("/"))

    await sender.send(TARGET, "hello")

    assert rest_server.headers[0]["Authorization"] == "Bot my-secret-token"


async def test_send_returns_the_platform_sent_timestamp(rest_server):
    sender = DiscordSender("tok", base_url=str(rest_server.make_url("")).rstrip("/"))

    result = await sender.send(TARGET, "hello")

    assert result.timestamp == timestamp_ms(999999999999999999)


async def test_send_refuses_a_non_group_conversation_type(rest_server):
    dm_target = ConversationTarget("discord", "1/2", "dm")
    sender = DiscordSender("tok", base_url=str(rest_server.make_url("")).rstrip("/"))

    result = await sender.send(dm_target, "hello")

    assert result.ok is False
    assert rest_server.calls == []


async def test_send_returns_a_non_retryable_failure_on_a_server_error(aiohttp_server):
    async def handler(request):
        return web.Response(status=500, text="boom")

    app = web.Application()
    app.router.add_post("/channels/2/messages", handler)
    server = await aiohttp_server(app)
    sender = DiscordSender("tok", base_url=str(server.make_url("")).rstrip("/"))

    result = await sender.send(TARGET, "hello")

    assert result.ok is False
    assert result.retryable is False


async def test_send_maps_http_429_to_a_retryable_failure(aiohttp_server):
    async def handler(request):
        return web.json_response({"retry_after": 2.5}, status=429)

    app = web.Application()
    app.router.add_post("/channels/2/messages", handler)
    server = await aiohttp_server(app)
    sender = DiscordSender("tok", base_url=str(server.make_url("")).rstrip("/"))

    result = await sender.send(TARGET, "hello")

    assert result.ok is False
    assert result.retryable is True
    assert result.retry_after == 2.5


async def test_send_never_raises_on_a_connection_failure():
    sender = DiscordSender("tok", base_url="http://127.0.0.1:1")

    result = await sender.send(TARGET, "hello")

    assert result.ok is False
