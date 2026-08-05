
import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.domain.ports import SendResult
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
    result = await sender.send(TARGET, "hello", request_id="tag-1")
    assert result == SendResult(ok=True)
    body = rpc_server.calls[0]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "send"
    assert body["params"]["groupId"] == "Z3JvdXA="
    assert body["params"]["message"] == "hello"
    assert body["id"] == "tag-1"


async def test_send_returns_a_non_retryable_failure_on_a_server_error(aiohttp_server):
    async def handler(request):
        return web.Response(status=500, text="boom")

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result.ok is False
    assert result.retryable is False


async def test_send_maps_http_429_to_a_retryable_failure(aiohttp_server):
    async def handler(request):
        return web.Response(status=429, headers={"Retry-After": "5"})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result == SendResult(ok=False, retryable=True, retry_after=5.0)


async def test_send_maps_http_429_without_retry_after_to_a_retryable_failure(aiohttp_server):
    async def handler(request):
        return web.Response(status=429)

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result.ok is False
    assert result.retryable is True
    assert result.retry_after is None


async def test_send_returns_false_on_a_jsonrpc_error_body(aiohttp_server):
    async def handler(request):
        return web.json_response({"jsonrpc": "2.0", "id": 1,
                                  "error": {"code": -1, "message": "no such group"}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result.ok is False


async def test_send_returns_false_when_the_daemon_is_unreachable():
    sender = SignalSender("http://127.0.0.1:1", timeout=0.5)
    result = await sender.send(TARGET, "hello")
    assert result.ok is False


async def test_send_never_raises_on_a_timeout(aiohttp_server):
    import asyncio

    async def handler(request):
        await asyncio.sleep(2)
        return web.json_response({})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"), timeout=0.2)
    result = await sender.send(TARGET, "hello")
    assert result.ok is False


async def test_send_does_not_log_the_message_body(rpc_server, caplog):
    """Group content is private; the send log line carries ids and length only."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))
    await sender.send(TARGET, "a very private secret")
    assert "a very private secret" not in caplog.text


async def test_a_dm_target_is_refused_in_v2(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))
    dm = ConversationTarget("signal", "+85212345678", "dm")
    result = await sender.send(dm, "hi")
    assert result.ok is False
    assert rpc_server.calls == []


# ── the platform's own sent-timestamp (design/11 §2.3, Bug B) ────────────────


async def test_send_returns_the_platform_assigned_timestamp(aiohttp_server):
    """`Deliverer` stamps `int(time.time() * 1000)` locally; Signal assigns its
    OWN sent-timestamp, and that is what a reply's `dataMessage.quote.id` points
    at — measured 8 ms apart on live data, so a join by `ts` silently never
    matched Nora's own rows.

    The response body carries it and `send` used to discard the whole body after
    checking for `error`. Shape confirmed from the shipped jar rather than by a
    live send: `org.asamk.signal.util.SendMessageResultUtils.outputResult`
    writes `Map.of("timestamp", <long>, "results", [...])` to the `JsonWriter`,
    and `SignalJsonRpcCommandHandler.runCommand` returns exactly that object as
    the JSON-RPC `result`.
    """
    async def handler(request):
        return web.json_response({
            "jsonrpc": "2.0", "id": "tag-1",
            "result": {
                "timestamp": 1785950108291,
                "results": [{"recipientAddress": {"number": "+85212345678"},
                             "type": "SUCCESS"}],
            },
        })

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello", request_id="tag-1")
    assert result.ok is True
    assert result.timestamp == 1785950108291


async def test_a_response_without_a_timestamp_leaves_it_none(rpc_server):
    """The fixture server answers `"result": {}`. A missing timestamp must
    degrade to `None` — the row keeps its locally stamped `ts` — never to 0,
    which is a real epoch value and would sort every Nora row to the top."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result.ok is True
    assert result.timestamp is None


async def test_a_non_integer_timestamp_is_ignored_rather_than_raising(aiohttp_server):
    async def handler(request):
        return web.json_response({"jsonrpc": "2.0", "id": 1,
                                  "result": {"timestamp": "nonsense"}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    sender = SignalSender(str(server.make_url("")).rstrip("/"))
    result = await sender.send(TARGET, "hello")
    assert result.ok is True
    assert result.timestamp is None
