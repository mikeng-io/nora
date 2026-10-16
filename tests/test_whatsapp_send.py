"""`nora.platform.whatsapp.send.WhatsAppSender`, against a stand-in for the
bridge's `/send` and `/send-media` routes — mirroring
`tests/test_signal_send.py`'s shape. The wire names (`jid`, `text`,
`caption`, `data_base64`) are the transport contract's."""

import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.domain.ports import OutboundAttachment, SendResult
from nora.platform.whatsapp.send import WhatsAppSender, _bridge_media_kind

# The bridge's OWN closed kind vocabulary for `/send-media`'s `media_type`
# (`buildMediaPayload`'s `switch`, `deploy/whatsapp-bridge/bridge_helpers.js`).
# Anything outside this set is the bridge's 400
# `{"error": "media_type must be image|video|audio|sticker|document"}` —
# this is the oracle the contract-style tests below check against, kept
# separate from `nora.platform.whatsapp.send._BRIDGE_MEDIA_KINDS` so a typo
# in the production set cannot also hide from its own test.
_BRIDGE_VALID_KINDS = frozenset({"image", "video", "audio", "sticker", "document"})

TARGET = ConversationTarget("whatsapp", "152099000000000000@g.us", "group")


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


async def test_a_dm_to_a_jid_not_in_identity_toml_is_refused_without_an_http_call(bridge):
    """A 1:1 message goes only to somebody in
    identity.toml. A sender handed no JIDs refuses every one; one handed some
    refuses the rest."""
    dm = ConversationTarget("whatsapp", "86183254648@s.whatsapp.net", "dm")
    assert (await _sender(bridge).send(dm, "hi")).ok is False
    url = str(bridge.make_url("")).rstrip("/")
    other = WhatsAppSender(url, dm_recipients=frozenset({"85250000000@s.whatsapp.net"}))
    assert (await other.send(dm, "hi")).ok is False
    assert bridge.calls == []


async def test_a_dm_to_a_known_jid_is_sent_to_that_jid(bridge, caplog):
    jid = "86183254648@s.whatsapp.net"
    url = str(bridge.make_url("")).rstrip("/")
    caplog.set_level("INFO")
    result = await WhatsAppSender(url, dm_recipients=frozenset({jid})).send(
        ConversationTarget("whatsapp", jid, "dm"), "hi",
    )
    assert result.ok is True
    assert bridge.calls == [("/send", {"jid": jid, "text": "hi"})]
    assert "86183254648" not in caplog.text


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
    `/send` is NOT also called — the group would hear the words twice.
    `media_type` is the bridge's KIND (`image`), never the raw MIME
    (`image/gif`) — that travels separately as `mimetype`."""
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
        "media_type": "image",
        "mimetype": "image/gif",
        "data_base64": "R0lGODlh",
        "filename": "reaction.gif",
        "caption": "two things",
    }
    assert second == {
        "jid": TARGET.conversation_id,
        "media_type": "image",
        "mimetype": "image/jpeg",
        "data_base64": "/9j/",
        "filename": "photo.jpg",
    }
    assert "caption" not in second


# ── media_type is the bridge's KIND, never the raw MIME ──────────────────────
#
# Posting the attachment's MIME type verbatim in `media_type` (e.g.
# `"image/png"`) gets HTTP 400
# `{"error": "media_type must be image|video|audio|sticker|document"}` from
# `handleSendMedia`/`buildMediaPayload` (`deploy/whatsapp-bridge/bridge.js`,
# `bridge_helpers.js`). These tests pin the exact payload for the MIME types
# Nora actually produces (`nora/infra/giphy.py`, `nora/agent/tools.py`) plus
# `document`'s catch-all, and prove the 400 is impossible for ANY MIME
# input — not just the ones under test.


@pytest.mark.parametrize(
    ("mime", "expected_kind"),
    [
        ("image/png", "image"),
        ("image/jpeg", "image"),
        ("video/mp4", "video"),
        ("audio/ogg", "audio"),
        ("application/pdf", "document"),
    ],
)
async def test_the_exact_payload_for_each_mime_the_bridge_must_accept(
    bridge, mime, expected_kind,
):
    attachments = (OutboundAttachment(b"\x00\x01", mime, "attachment.bin"),)
    result = await _sender(bridge).send(TARGET, "", attachments=attachments)
    assert result.ok is True
    assert bridge.calls == [(
        "/send-media",
        {
            "jid": TARGET.conversation_id,
            "media_type": expected_kind,
            "mimetype": mime,
            "data_base64": "AAE=",
            "filename": "attachment.bin",
        },
    )]
    # Every one of these is a real bridge kind — the contract-style oracle.
    assert expected_kind in _BRIDGE_VALID_KINDS


@pytest.mark.parametrize(
    "mime",
    [
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "video/mp4", "video/quicktime",
        "audio/ogg", "audio/mpeg", "audio/mp4",
        "application/pdf", "application/octet-stream", "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "",             # never declared / unparseable
        "garbage",      # malformed, no "/"
        "image/*",      # an undergrounded wildcard should never reach here,
                         # but the mapping must still fail closed if it does
    ],
)
def test_bridge_media_kind_never_produces_the_400_for_any_mime_input(mime):
    """`_bridge_media_kind` is total and closed over the bridge's own
    vocabulary — the contract-style oracle both sides must agree on
    (`deploy/whatsapp-bridge/bridge_helpers.js`'s `buildMediaPayload` switch).
    A result outside this set earns the 400, for ANY MIME string, not only the
    cases enumerated above."""
    kind = _bridge_media_kind(mime)
    assert kind in _BRIDGE_VALID_KINDS
    # And never the MIME itself passed through unmapped — the literal bug.
    assert kind != mime


def test_webp_maps_to_image_not_sticker():
    """`image/webp` is an ordinary image MIME (Nora's GIF/generated-image
    attachments never claim `sticker` — `OutboundAttachment` carries no
    sticker flag to decide it from, the same reason `nora.domain.media`
    keeps `sticker` a separate boolean axis rather than something a MIME type
    alone can express)."""
    assert _bridge_media_kind("image/webp") == "image"


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
