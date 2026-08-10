
import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.domain.ports import LinkPreview, Mention, SendResult
from nora.platform.signal.send import SignalSender

TARGET = ConversationTarget("signal", "Z3JvdXA=", "group")
ACI = "b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b"


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


# --------------------------------------------------------- decoration on the wire
#
# The parameter NAMES, which nothing else in the suite can check. They were read
# out of the shipped signal-cli 0.14.7 jar rather than inferred from the man
# page — `JsonRpcNamespace.getList(name)` tries the literal key FIRST and only
# falls back to `name + "s"`, and `SendCommand` reads `getList("mention")` and
# `getString("preview-url" | "preview-title" | "preview-description" |
# "preview-image")` — because the man page documents both a camelCase rule AND a
# pluralising example (`--attachment` becoming `attachments`) and does not say
# which applies to a repeatable option. Guessing wrong here is SILENT:
# signal-cli ignores a parameter it never reads, the message goes out
# unmentioned, and no error appears anywhere.
#
# NOT verified against a live daemon — `nora.platform.signal.send`'s module
# docstring says so in the code as well as here, because a test that looks like
# a wire test is exactly the instrument someone would later read as one.

async def test_a_mention_is_sent_as_start_length_recipient_under_the_mention_key(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "@Grant hi",
        mentions=(Mention(start=0, length=8, recipient=ACI),),
    )

    assert rpc_server.calls[0]["params"]["mention"] == [f"0:8:{ACI}"]


async def test_several_mentions_ride_one_array(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "@Grant @Miles",
        mentions=(
            Mention(start=0, length=8, recipient=ACI),
            Mention(start=9, length=5, recipient="+85251234567"),
        ),
    )

    assert rpc_server.calls[0]["params"]["mention"] == [
        f"0:8:{ACI}", "9:5:+85251234567",
    ]


async def test_a_plain_message_carries_neither_decoration_key(rpc_server):
    """A plain send's payload is still exactly the two keys it always was.

    Not cosmetic: an argparse4j namespace holding an EMPTY list is a different
    object from one holding no key at all, and this is what keeps the shipped
    shape of the overwhelmingly common message from drifting as a side effect
    of a feature nobody used on it."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(TARGET, "hello")

    assert set(rpc_server.calls[0]["params"]) == {"groupId", "message"}


async def test_a_preview_is_sent_as_the_four_camelcase_scalars(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "https://n.example.net/a",
        preview=LinkPreview(
            url="https://n.example.net/a", title="A page", description="about it",
            image_data_uri="data:image/png;base64,AAAA",
        ),
    )

    params = rpc_server.calls[0]["params"]
    assert params["previewUrl"] == "https://n.example.net/a"
    assert params["previewTitle"] == "A page"
    assert params["previewDescription"] == "about it"
    assert params["previewImage"] == "data:image/png;base64,AAAA"


async def test_the_optional_preview_scalars_are_omitted_when_empty(rpc_server):
    """`--preview-description`/`--preview-image` are documented optional;
    `--preview-title` is documented MANDATORY and is always sent."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "https://n.example.net/a",
        preview=LinkPreview(url="https://n.example.net/a", title="A page"),
    )

    params = rpc_server.calls[0]["params"]
    assert params["previewTitle"] == "A page"
    assert "previewDescription" not in params
    assert "previewImage" not in params


async def test_a_preview_image_travels_as_a_data_uri_not_a_path(rpc_server):
    """signal-cli runs in a DIFFERENT container from Nora with no shared
    volume (`docker-compose.yml`), so a PATH here would be unreadable in
    production while passing every local test. `AttachmentHelper.
    getAttachmentStream` skips its file-path validation entirely for a `data:`
    string (read out of the 0.14.7 jar), which is what makes this work."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "https://n.example.net/a",
        preview=LinkPreview(
            url="https://n.example.net/a", title="A page",
            image_data_uri="data:image/jpeg;base64,/9j/4AAQ",
        ),
    )

    assert rpc_server.calls[0]["params"]["previewImage"].startswith("data:image/")


async def test_a_malformed_mention_range_is_dropped_rather_than_posted(rpc_server):
    """signal-cli throws `UserErrorException` on a range its
    `(\\d+):(\\d+):(.+)` regex rejects, and that fails the WHOLE send. A bad
    range must therefore cost the CHIP, never the message."""
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    result = await sender.send(
        TARGET, "hello",
        mentions=(
            Mention(start=-1, length=3, recipient=ACI),
            Mention(start=0, length=0, recipient=ACI),
            Mention(start=0, length=3, recipient=""),
        ),
    )

    assert result.ok is True
    assert "mention" not in rpc_server.calls[0]["params"]


async def test_a_valid_range_survives_alongside_a_malformed_one(rpc_server):
    sender = SignalSender(str(rpc_server.make_url("")).rstrip("/"))

    await sender.send(
        TARGET, "hello",
        mentions=(
            Mention(start=-5, length=3, recipient=ACI),
            Mention(start=0, length=5, recipient=ACI),
        ),
    )

    assert rpc_server.calls[0]["params"]["mention"] == [f"0:5:{ACI}"]
