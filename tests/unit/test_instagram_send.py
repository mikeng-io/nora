"""`nora.platform.instagram.send.InstagramSender` against the bridge's pinned
request shapes (design/instagram-adoption/03-transport-contract.md).

The stand-in bridge RECORDS EVERY REQUEST, because the properties that matter
here are about what reached the wire: that the text went as its own `/send`
and not as a caption nothing would carry, that the room key is the short
thread id, and that a 409 never produces a retry. A fake that answered 200 to
everything without inspecting the body would let all three regress green.
"""

from __future__ import annotations

import base64

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.models import ConversationTarget
from nora.domain.ports import Mention, OutboundAttachment, OutboundQuote
from nora.platform.instagram.send import InstagramSender

ROOM = "1009543911792316"
GROUP = ConversationTarget("instagram", ROOM, "group")
DM = ConversationTarget("instagram", ROOM, "dm")
PNG = OutboundAttachment(content=b"\x89PNG\r\n", media_type="image/png", filename="a.png")


@pytest.fixture
async def bridge():
    """`(box, sender)`. `box["calls"]` is `[(path, body), …]` in order."""
    box: dict = {"status": 200, "calls": [], "body": {"ok": True, "id": "mid.$x",
                                                      "timestamp_ms": 1789985625166}}

    async def handler(request: web.Request) -> web.Response:
        box["calls"].append((request.path, await request.json()))
        if box["status"] != 200:
            return web.json_response({"error": "boom"}, status=box["status"])
        return web.json_response(box["body"])

    app = web.Application()
    app.router.add_post("/send", handler)
    app.router.add_post("/send-media", handler)
    server = TestServer(app)
    await server.start_server()
    yield box, InstagramSender(str(server.make_url("")).rstrip("/"))
    await server.close()


# --- the text path -----------------------------------------------------------


async def test_a_plain_send_posts_the_short_room_id_and_the_text(bridge):
    box, sender = bridge

    result = await sender.send(GROUP, "hello")

    assert result.ok is True
    assert box["calls"] == [("/send", {"room_id": ROOM, "text": "hello"})]


async def test_the_platforms_own_sent_timestamp_is_reported(bridge):
    """What a later reply's quote resolves against."""
    _box, sender = bridge

    result = await sender.send(GROUP, "hello")

    assert result.timestamp == 1789985625166


async def test_a_missing_timestamp_does_not_turn_a_delivered_send_into_a_failure(bridge):
    box, sender = bridge
    box["body"] = {"ok": True, "id": "mid.$x"}

    result = await sender.send(GROUP, "hello")

    assert result.ok is True
    assert result.timestamp is None


# --- the media path: the caption inversion -----------------------------------


async def test_text_and_media_are_TWO_calls_and_the_text_goes_FIRST(bridge):
    """The inversion against WhatsApp. `SendMediaRequest` has no text field of
    any kind, so WhatsApp's caption-on-the-first-attachment shape would drop
    the words silently, with a 200 on every call."""
    box, sender = bridge

    result = await sender.send(GROUP, "look at this", attachments=(PNG,))

    assert result.ok is True
    paths = [path for path, _ in box["calls"]]
    assert paths == ["/send", "/send-media"]
    assert box["calls"][0][1]["text"] == "look at this"


async def test_send_media_carries_NO_caption_field(bridge):
    """The wire has nowhere to put one and the bridge ignores it. A caption
    key here would be a field that reads like it works."""
    box, sender = bridge

    await sender.send(GROUP, "look", attachments=(PNG,))

    _path, media_body = box["calls"][1]
    assert "caption" not in media_body
    assert "text" not in media_body


async def test_the_attachment_bytes_ride_base64_with_their_declared_type(bridge):
    box, sender = bridge

    await sender.send(GROUP, "look", attachments=(PNG,))

    _path, media_body = box["calls"][1]
    assert base64.b64decode(media_body["data_base64"]) == b"\x89PNG\r\n"
    assert media_body["mimetype"] == "image/png"
    assert media_body["filename"] == "a.png"
    assert media_body["room_id"] == ROOM


async def test_a_failed_TEXT_send_does_not_then_post_the_media(bridge):
    """The words did not land. Posting the image anyway would put it in the
    room with nothing explaining it, and would report the text's failure as a
    success."""
    box, sender = bridge
    box["status"] = 500

    result = await sender.send(GROUP, "look", attachments=(PNG,))

    assert result.ok is False
    assert [path for path, _ in box["calls"]] == ["/send"]


async def test_an_attachment_with_no_text_skips_the_text_call(bridge):
    box, sender = bridge

    result = await sender.send(GROUP, "", attachments=(PNG,))

    assert result.ok is True
    assert [path for path, _ in box["calls"]] == ["/send-media"]


# --- decoration ---------------------------------------------------------------


async def test_a_mention_is_FORWARDED_as_fbid_offset_length(bridge):
    """Unlike WhatsApp v1, which accepts and ignores them. The wire takes
    them and 02 measured the round trip."""
    box, sender = bridge

    await sender.send(GROUP, "@nora hi", mentions=(Mention(start=0, length=5, recipient="1784"),))

    _path, body = box["calls"][0]
    assert body["mentions"] == [{"fbid": "1784", "offset": 0, "length": 5}]


async def test_a_mention_naming_NOBODY_is_dropped_rather_than_sent_empty(bridge):
    box, sender = bridge

    await sender.send(GROUP, "hi", mentions=(Mention(start=0, length=2, recipient=""),))

    _path, body = box["calls"][0]
    assert "mentions" not in body


async def test_no_mentions_means_no_mentions_key(bridge):
    box, sender = bridge

    await sender.send(GROUP, "hi")

    assert "mentions" not in box["calls"][0][1]


async def test_a_quote_threads_the_reply_by_the_platform_message_id(bridge):
    box, sender = bridge

    await sender.send(GROUP, "hi", quote=OutboundQuote(
        ts=1, author_id="110966695387753", platform_message_id="mid.$parent",
    ))

    assert box["calls"][0][1]["reply_to"] == "mid.$parent"


async def test_a_quote_with_no_platform_id_sends_UNTHREADED_rather_than_refusing(bridge):
    """Signal's `(ts, author)` scheme cannot reconstruct a `mid.$…`. A reply
    that lost its thread is still a reply."""
    box, sender = bridge

    result = await sender.send(GROUP, "hi", quote=OutboundQuote(ts=1, author_id="x"))

    assert result.ok is True
    assert "reply_to" not in box["calls"][0][1]


# --- posture and status mapping ----------------------------------------------


async def test_a_dm_is_REFUSED_WITHOUT_AN_HTTP_CALL(bridge):
    """Deny-by-default this phase, and the refusal must not reach the wire —
    a 1:1 send that the bridge merely happened to reject would be a posture
    that depends on Instagram's mood."""
    box, sender = bridge

    result = await sender.send(DM, "hello")

    assert result.ok is False
    assert box["calls"] == []


async def test_a_CHECKPOINT_409_is_NOT_retryable(bridge):
    """Retrying a challenged account is how a captcha becomes a ban. The
    distinction from 503 is the whole reason the bridge emits two codes."""
    box, sender = bridge
    box["status"] = 409

    result = await sender.send(GROUP, "hello")

    assert result.ok is False
    assert result.retryable is False


async def test_a_503_IS_retryable(bridge):
    """The socket is down or reconnecting and the bridge heals it itself."""
    box, sender = bridge
    box["status"] = 503

    result = await sender.send(GROUP, "hello")

    assert result.ok is False
    assert result.retryable is True


async def test_a_500_is_not_retryable(bridge):
    """The bridge's fail-closed default: 503 is a CLAIM that retrying will
    work, and an unclassifiable refusal has not earned it."""
    box, sender = bridge
    box["status"] = 500

    result = await sender.send(GROUP, "hello")

    assert result.ok is False
    assert result.retryable is False


async def test_an_unreachable_bridge_returns_a_failure_rather_than_raising():
    """`Deliverer` needs the outcome distinction to record a FAILED delivery
    status; an exception loses it."""
    sender = InstagramSender("http://127.0.0.1:1", timeout=0.2)

    result = await sender.send(GROUP, "hello")

    assert result.ok is False
