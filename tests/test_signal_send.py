
import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.platform.signal.send import SignalSender

TARGET = ConversationTarget("signal", "Z3JvdXA=", "group")


@pytest.fixture
async def rpc_server(aiohttp_server):
    calls: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append(body)
        if body.get("_fail"):
            return web.Response(status=500, text="boom")
        return web.json_response({"jsonrpc": "2.0", "id": body.get("id"), "result": {}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    server.calls = calls
    return server


async def test_send_posts_the_expected_jsonrpc_shape(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))
    assert await sender.send(TARGET, "hello", request_id="tag-1") is True
    body = rpc_server.calls[0]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "send"
    assert body["params"]["groupId"] == "Z3JvdXA="
    assert body["params"]["message"] == "hello"
    assert body["id"] == "tag-1"


async def test_send_returns_false_on_a_server_error(aiohttp_server):
    async def handler(request):
        return web.Response(status=500, text="boom")

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    assert await sender.send(TARGET, "hello") is False


async def test_send_returns_false_on_a_jsonrpc_error_body(aiohttp_server):
    async def handler(request):
        return web.json_response({"jsonrpc": "2.0", "id": 1,
                                  "error": {"code": -1, "message": "no such group"}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    assert await sender.send(TARGET, "hello") is False


async def test_send_returns_false_when_the_daemon_is_unreachable():
    sender = SignalSender("http://127.0.0.1:1", timeout=0.5)
    assert await sender.send(TARGET, "hello") is False


async def test_send_never_raises_on_a_timeout(aiohttp_server):
    import asyncio

    async def handler(request):
        await asyncio.sleep(2)
        return web.json_response({})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"), timeout=0.2)
    assert await sender.send(TARGET, "hello") is False


async def test_send_does_not_log_the_message_body(rpc_server, caplog):
    """Group content is private; the send log line carries ids and length only."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))
    await sender.send(TARGET, "a very private secret")
    assert "a very private secret" not in caplog.text


async def test_a_dm_target_is_refused_in_v2(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))
    dm = ConversationTarget("signal", "+85212345678", "dm")
    assert await sender.send(dm, "hi") is False
    assert rpc_server.calls == []
