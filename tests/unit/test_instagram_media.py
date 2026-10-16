"""The seam between parser, fetcher and custody.

The parser's tests, and the custody stage's tests (which run against a fake
source keyed by `platform_id`), each pass on their own end; neither can see
whether the two connect. So this file asserts the one thing neither end can: an
Instagram image event, taken through the real parser,
the real fetcher and the real custody stage, ends up as a label AND a handle
in the body — R-90, on the platform that did not have it.

The CDN is a loopback server here; the allowlist is pointed at its host, since
a real `scontent.*.fbcdn.net` socket cannot be opened from a test. What that
leaves unproven is stated rather than papered over: that the live CDN answers
these urls unauthenticated is a measurement taken outside this suite (200 with no
cookies, no user-agent, no referer), not something any test here can re-derive.
"""

from __future__ import annotations

import hashlib

from aiohttp import web

from nora.ingest.stages import media as media_stage
from nora.obs.metrics import Metrics
from nora.platform.instagram.media import InstagramMediaSource
from nora.platform.instagram.parser import parse_event
from nora.platform.registry import MediaSourceRegistry
from nora.testing import FakeBlobStore

# Real JPEG magic, so the wildcard type Instagram records is groundable — the
# byte-level reason the comprehension pass can read it at all.
PHOTO = b"\xff\xd8\xff\xe0\x00\x10JFIF pretend jpeg body"
PHOTO_SHA = hashlib.sha256(PHOTO).hexdigest()

ROOM = "1183780976338188"


def _event(media: dict | None, text: str = "睇下") -> dict:
    return {
        "kind": "message",
        "room_id": ROOM,
        "message_id": "mid.$gAASG4nvQLWim9MCLrWgw3tChgAAq",
        "sender": {"fbid": "186217604552756", "username": "example_handle"},
        "from_me": False,
        "timestamp_ms": 1789985625166,
        "text": text,
        "mentions": [],
        "reply_to": None,
        "media": media,
    }


async def _cdn(aiohttp_server, *, body: bytes = PHOTO):
    async def serve(request):
        return web.Response(body=body, content_type="image/jpeg")

    app = web.Application()
    app.router.add_get("/v/t51.2885-15/photo.jpg", serve)
    return await aiohttp_server(app)


async def test_an_instagram_image_ends_up_with_a_label_AND_a_handle(aiohttp_server):
    """End to end: the body is not a bare `睇下 [image]`, a claim that something
    arrived with nothing Nora could resolve."""
    server = await _cdn(aiohttp_server)
    url = server.make_url("/v/t51.2885-15/photo.jpg")
    message = parse_event(_event({
        "kind": "image", "url": str(url),
        "attachment_fbid": "970288316113989", "width": 720, "height": 960,
    }))
    assert message is not None
    assert message.body == "睇下 [image]", "the parser writes the placeholder"

    metrics = Metrics()
    blobs = FakeBlobStore()
    out = await media_stage.run(
        message=message,
        sources=MediaSourceRegistry([InstagramMediaSource(
            attachment_host_suffixes={url.host},
        )]),
        blobs=blobs, metrics=metrics,
    )

    assert out.body == f"睇下 [image {PHOTO_SHA[:6]}]"
    assert blobs.blobs[PHOTO_SHA] == PHOTO
    assert metrics.get("nora_media_custody_total", platform="instagram",
                       status="stored") == 1


async def test_the_stored_type_is_ground_from_the_bytes_not_left_a_wildcard(
    aiohttp_server,
):
    """The wire carries no mimetype, so the parser records `image/*` — true,
    and unusable by the one consumer that does not classify it but declares
    it. Custody grounds it against the bytes, so what reaches the comprehension
    pass is a container a decoder can act on."""
    server = await _cdn(aiohttp_server)
    url = server.make_url("/v/t51.2885-15/photo.jpg")
    message = parse_event(_event({"kind": "image", "url": str(url)}))
    assert message is not None
    assert message.media[0].media_type == "image/*"

    out = await media_stage.run(
        message=message,
        sources=MediaSourceRegistry([InstagramMediaSource(
            attachment_host_suffixes={url.host},
        )]),
        blobs=FakeBlobStore(), metrics=Metrics(),
    )

    assert out.media_blobs[0].media_type == "image/jpeg"


async def test_one_time_media_is_recorded_and_never_fetched(aiohttp_server):
    """View-once media must stay unfetched: on Instagram, fetching IS viewing
    and is visible to the sender. The turn still says something arrived — with
    no handle, because Nora does not hold it — and the loss is counted with a
    reason that says «nothing to fetch», not «the CDN failed»."""
    server = await _cdn(aiohttp_server)
    message = parse_event(_event({
        "kind": "one_time", "url": None, "one_time": True, "view_mode": 0,
    }, text=""))
    assert message is not None

    metrics = Metrics()
    out = await media_stage.run(
        message=message,
        sources=MediaSourceRegistry([InstagramMediaSource(
            attachment_host_suffixes={server.host},
        )]),
        blobs=FakeBlobStore(), metrics=metrics,
    )

    assert out.body == "[file]"
    assert out.media_blobs == ()
    assert metrics.get("nora_media_custody_total", platform="instagram",
                       status="not_supported") == 1


async def test_an_image_from_a_host_the_allowlist_refuses_never_gains_a_handle(
    aiohttp_server,
):
    """The url comes off the EVENT, so a forged payload is a fetcher pointed
    anywhere. A refused host must cost the bytes and never the message, and
    must not leave a handle claiming custody Nora does not have."""
    server = await _cdn(aiohttp_server)
    url = server.make_url("/v/t51.2885-15/photo.jpg")
    message = parse_event(_event({"kind": "image", "url": str(url)}))
    assert message is not None

    metrics = Metrics()
    out = await media_stage.run(
        message=message,
        # The default allowlist: Instagram's own two domains, which a loopback
        # host is not under.
        sources=MediaSourceRegistry([InstagramMediaSource()]),
        blobs=FakeBlobStore(), metrics=metrics,
    )

    assert out.body == "睇下 [image]"
    assert out.media_blobs == ()
    assert metrics.get("nora_media_custody_total", platform="instagram",
                       status="bad_token") == 1
