"""Direct `WhatsAppSender.send` multi-attachment identity.

One action → several `/send-media` calls → a bounded set of CONFIRMED
native ids. The compat single mirrors the last confirmed id; a failed
piece after earlier confirmations returns `uncertain` — a whole-send
retry must never re-post what WhatsApp already acknowledged.

The stand-in bridge records every request and answers each one with its
queued status+body, in the bridge's own `{ok, id, timestamp_ms}` shape,
so a sender still on `/send` for the media, or one that stops recording
the confirmed ids, goes red.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.models import ConversationTarget
from nora.domain.ports import OutboundAttachment
from nora.platform.whatsapp.send import WhatsAppSender

ROOM = "12036@g.us"
GROUP = ConversationTarget("whatsapp", ROOM, "group")
PNG = OutboundAttachment(content=b"\x89PNG\r\n", media_type="image/png", filename="a.png")
JPG = OutboundAttachment(content=b"\xff\xd8\xff", media_type="image/jpeg", filename="b.jpg")


def _ok(mid: str) -> dict:
    return {"ok": True, "id": mid, "timestamp_ms": 1_790_000_000_000}


@pytest.fixture
async def bridge():
    """`(box, sender)` — `box["queue"]` is `(status, body)` per call."""
    box: dict = {"calls": [], "queue": []}

    async def handler(request: web.Request) -> web.Response:
        payload = await request.json()
        box["calls"].append((request.path, payload))
        valid = payload.get("jid") == ROOM
        if request.path == "/send":
            valid = valid and isinstance(payload.get("text"), str)
        elif request.path == "/send-media":
            valid = valid and payload.get("media_type") in {
                "image", "video", "audio", "sticker", "document"}
            valid = valid and bool(payload.get("data_base64"))
        else:
            valid = False
        if not valid:
            return web.json_response({"error": "bad_request"}, status=400)
        status, body = box["queue"].pop(0)
        return web.json_response(body, status=status)

    app = web.Application()
    app.router.add_post("/send", handler)
    app.router.add_post("/send-media", handler)
    server = TestServer(app)
    await server.start_server()
    sender = WhatsAppSender(str(server.make_url("")), dm_recipients={ROOM})
    yield box, sender
    await server.close()


async def test_two_confirmed_posts_yield_both_native_ids(bridge) -> None:
    box, sender = bridge
    box["queue"] += [(200, _ok("wamid.one")), (200, _ok("wamid.two"))]
    res = await sender.send(GROUP, "cap", attachments=(PNG, JPG))
    assert res.ok is True
    assert res.platform_message_id == "wamid.two"  # compat single = last
    assert res.platform_message_ids == ("wamid.one", "wamid.two")
    assert [path for path, _ in box["calls"]] == ["/send-media", "/send-media"]
    # The caption rides the FIRST piece only — never its own /send call.
    first, second = box["calls"][0][1], box["calls"][1][1]
    assert first.get("caption") == "cap" and "caption" not in second


async def test_a_failed_second_post_keeps_the_confirmed_id_uncertain(bridge) -> None:
    box, sender = bridge
    box["queue"] += [(200, _ok("wamid.one")), (500, {"error": "server"})]
    res = await sender.send(GROUP, "cap", attachments=(PNG, JPG))
    assert res.ok is False and res.uncertain is True
    assert len(box["calls"]) == 2  # the failed piece is not retried
    # The confirmed piece's id is NOT minted back onto a failed result —
    # a partial outcome cannot prove the whole action landed.
    assert res.platform_message_id is None


async def test_a_body_without_an_id_mints_nothing(bridge) -> None:
    box, sender = bridge
    box["queue"] += [(200, _ok("wamid.one")), (200, {"ok": True})]
    res = await sender.send(GROUP, "cap", attachments=(PNG, JPG))
    assert res.ok is True
    assert res.platform_message_ids == ("wamid.one",)


async def test_a_text_send_sets_the_alias_list_too(bridge) -> None:
    box, sender = bridge
    box["queue"] += [(200, _ok("wamid.t1"))]
    res = await sender.send(GROUP, "hi")
    assert res.platform_message_id == "wamid.t1"
    assert res.platform_message_ids == ("wamid.t1",)


async def test_a_missing_id_does_not_make_a_partial_send_retryable(bridge) -> None:
    box, sender = bridge
    box["queue"] += [(200, {"ok": True}), (503, {"error": "not_connected"})]
    res = await sender.send(GROUP, "cap", attachments=(PNG, JPG))
    assert not res.ok and res.uncertain and not res.retryable
    assert res.platform_message_ids == ()
    assert len(box["calls"]) == 2


async def test_an_instagram_confirmation_without_id_still_blocks_partial_retry() -> None:
    from nora.platform.instagram.send import InstagramSender

    calls = []

    async def handle(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append(body)
        if len(calls) == 1 and body == {"text": "caption"}:
            return web.json_response({"timestamp_ms": 1790000000000})
        if len(calls) == 2 and isinstance(body.get("media"), dict):
            return web.json_response({"error": {"code": "reconnecting"}}, status=503)
        return web.json_response({"error": {"code": "bad_request"}}, status=400)

    app = web.Application()
    app.router.add_post("/threads/example-thread/messages", handle)
    async with TestServer(app) as server:
        sender = InstagramSender(str(server.make_url("")))
        result = await sender.send(
            ConversationTarget("instagram", "example-thread", "group"),
            "caption", attachments=(PNG,))
    assert len(calls) == 2
    assert not result.ok and result.uncertain and not result.retryable
    assert result.platform_message_ids == ()
