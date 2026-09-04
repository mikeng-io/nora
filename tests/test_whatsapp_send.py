"""`nora.platform.whatsapp.send.WhatsAppSender`, against a stand-in for the
bridge's `/send` and `/send-media` routes — mirroring
`tests/test_signal_send.py`'s shape. The wire names (`jid`, `text`,
`caption`, `data_base64`) are the contract's
(`design/whatsapp-adoption/03-transport-contract.md`)."""

import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.domain.ports import OutboundAttachment, SendResult
from nora.platform.whatsapp.send import WhatsAppSender

TARGET = ConversationTarget("whatsapp", "176569000000000000@g.us", "group")


@pytest.fixture
async def bridge(aiohttp_server):
    calls: list[tuple[str, dict]] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append((request.path, body))
        return web.json_response({"ok": True, "id": "3EB0F1",
                                  "timestamp_ms": 1756963200000})

    app = web.Application()
    app.router.add_post("/send", handler)
    app.router.add_post("/send-media", handler)
    server = await aiohttp_server(app)
    server.calls = calls
    return server


def _sender(server) -> WhatsAppSender:
    return WhatsAppSender(str(server.make_url("")).rstrip("/"))


async def test_send_posts_jid_and_text_to_the_send_route(bridge):
    result = await _sender(bridge).send(TARGET, "hello")
    assert result.ok is True
    assert result.timestamp == 1756963200000
    assert bridge.calls == [("/send", {"jid": TARGET.conversation_id, "text": "hello"})]


async def test_a_200_without_a_timestamp_degrades_to_none(aiohttp_server):
    """A send that reached the group must never read as failed over a missing
    field — and 0 is a real epoch value, never a stand-in for "absent"."""
    async def handler(request):
        return web.json_response({"ok": True, "id": "3EB0F1"})

    app = web.Application()
    app.router.add_post("/send", handler)
    server = await aiohttp_server(app)
    result = await _sender(server).send(TARGET, "hello")
    assert result == SendResult(ok=True, timestamp=None)


async def test_a_dm_target_is_refused_without_an_http_call(bridge):
    """1:1 is deny-by-default this phase — the same posture Signal v2 and
    Discord v1 shipped with (design 02 §Open decisions)."""
    dm = ConversationTarget("whatsapp", "85202411020@s.whatsapp.net", "dm")
    result = await _sender(bridge).send(dm, "hi")
    assert result.ok is False
    assert bridge.calls == []


async def test_http_503_is_retryable_because_the_socket_reconnects(aiohttp_server):
    """The bridge's 503 `{"error": "not_connected"}` means the WhatsApp
    socket is down — transient by definition, so retryable=True."""
    async def handler(request):
        return web.json_response({"error": "not_connected"}, status=503)

    app = web.Application()
    app.router.add_post("/send", handler)
    server = await aiohttp_server(app)
    result = await _sender(server).send(TARGET, "hello")
    assert result == SendResult(ok=False, retryable=True)


async def test_http_429_maps_to_a_retryable_failure_with_retry_after(aiohttp_server):
    async def handler(request):
        return web.Response(status=429, headers={"Retry-After": "5"})

    app = web.Application()
    app.router.add_post("/send", handler)
    server = await aiohttp_server(app)
    result = await _sender(server).send(TARGET, "hello")
    assert result == SendResult(ok=False, retryable=True, retry_after=5.0)


async def test_another_non_200_is_a_plain_failure(aiohttp_server):
    async def handler(request):
        return web.json_response({"error": "send failed"}, status=500)

    app = web.Application()
    app.router.add_post("/send", handler)
    server = await aiohttp_server(app)
    result = await _sender(server).send(TARGET, "hello")
    assert result == SendResult(ok=False)


async def test_an_unreachable_bridge_is_ok_false_never_an_exception():
    sender = WhatsAppSender("http://127.0.0.1:1", timeout=0.5)
    result = await sender.send(TARGET, "hello")
    assert result.ok is False


# ── attachments: /send-media, caption on the first only ──────────────────────


async def test_attachments_route_through_send_media_with_the_caption_on_the_first(bridge):
    """The caption carries the message text on the FIRST attachment, and
    `/send` is NOT also called — the group would hear the words twice."""
    attachments = (
        OutboundAttachment(b"GIF89a", "image/gif", "reaction.gif"),
        OutboundAttachment(b"\xff\xd8\xff", "image/jpeg", "photo.jpg"),
    )
    result = await _sender(bridge).send(TARGET, "two things", attachments=attachments)
    assert result.ok is True

    paths = [path for path, _ in bridge.calls]
    assert paths == ["/send-media", "/send-media"]
    first, second = bridge.calls[0][1], bridge.calls[1][1]
    assert first == {
        "jid": TARGET.conversation_id,
        "media_type": "image/gif",
        "data_base64": "R0lGODlh",
        "filename": "reaction.gif",
        "caption": "two things",
    }
    assert "caption" not in second


async def test_a_failed_attachment_send_stops_the_sequence(aiohttp_server):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        await request.json()
        return web.json_response({"error": "boom"}, status=500)

    app = web.Application()
    app.router.add_post("/send-media", handler)
    server = await aiohttp_server(app)
    attachments = (
        OutboundAttachment(b"GIF89a", "image/gif", "a.gif"),
        OutboundAttachment(b"GIF89a", "image/gif", "b.gif"),
    )
    result = await _sender(server).send(TARGET, "x", attachments=attachments)
    assert result.ok is False
    assert calls == 1
