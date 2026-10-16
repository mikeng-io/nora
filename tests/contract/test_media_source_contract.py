"""`MediaSource` has three implementations, so it gets one suite they all pass
(DEC-43).

`nora.platform.signal.media.SignalMediaSource` runs against a real aiohttp
daemon serving the JSON-RPC shapes signal-cli actually returns;
`nora.testing.FakeMediaSource` is what the ingest tests use.

**The fake daemon here answers DIFFERENTLY per request, deliberately.** That is
the whole point of the rule this file exists to satisfy: a stub returning bytes
for anything would prove that the adapter can decode base64 and prove nothing
about whether it asks signal-cli the right question. So the daemon below keys
off `params.id` and returns the real not-found error for an id it does not
hold, in the shape the live daemon uses:

    {"jsonrpc":"2.0","error":{"code":-1,
     "message":"Could not find attachment with ID: nonexistent-probe"},"id":"p3"}

and that error is provably distinct from an unimplemented method, which answers
`-32601 "Method not implemented"`. Reverting `SignalMediaSource.fetch`'s method
name to something signal-cli does not serve
makes `test_a_missing_attachment_is_not_found` red rather than leaving it green
against a daemon that says yes to everything.

What no test here can establish is the shape of a SUCCESSFUL `getAttachment`
result: the success case is fixtures only. The adapter accepts both plausible
shapes and counts anything else as `bad_payload`; see its docstring.
"""

from __future__ import annotations

import asyncio
import base64

import pytest
from aiohttp import web

from nora.domain.media import MediaRef
from nora.platform.discord.media import DiscordMediaSource
from nora.platform.instagram import media as instagram_media
from nora.platform.instagram.media import InstagramMediaSource
from nora.platform.signal.media import SignalMediaSource
from nora.testing import FakeMediaSource

HELD_ID = "F-lmZ8QK8xhLnHzp1sIe"
PAYLOAD = b"\xff\xd8\xff\xe0 jpeg-ish bytes"


def _daemon(*, held: dict[str, bytes], status: int = 200, result_shape: str = "object"):
    """A daemon that answers per request, not a stub that answers alike."""

    async def rpc(request):
        payload = await request.json()
        if status != 200:
            return web.json_response({}, status=status)
        if payload.get("method") != "getAttachment":
            # Exactly what the live daemon returns for a method it does not
            # implement — so an adapter calling the wrong method fails here
            # instead of being handed bytes anyway.
            return web.json_response({
                "jsonrpc": "2.0", "id": payload.get("id"),
                "error": {"code": -32601, "message": "Method not implemented", "data": None},
            })
        attachment_id = (payload.get("params") or {}).get("id")
        data = held.get(attachment_id)
        if data is None:
            return web.json_response({
                "jsonrpc": "2.0", "id": payload.get("id"),
                "error": {"code": -1, "data": None,
                          "message": f"Could not find attachment with ID: {attachment_id}"},
            })
        encoded = base64.b64encode(data).decode()
        result = encoded if result_shape == "string" else {"data": encoded}
        return web.json_response({"jsonrpc": "2.0", "result": result, "id": payload.get("id")})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", rpc)
    return app


async def _signal(aiohttp_server, **kwargs):
    server = await aiohttp_server(_daemon(**kwargs))
    return SignalMediaSource(base_url=str(server.make_url("")), max_bytes=1024)


@pytest.fixture(params=["signal", "fake"])
async def sources(request, aiohttp_server):
    """One (healthy, unreachable) pair per implementation — both members from
    the SAME implementation, so every assertion is about one class run twice."""
    if request.param == "signal":
        return {
            "healthy": await _signal(aiohttp_server, held={HELD_ID: PAYLOAD}),
            "unreachable": await _signal(aiohttp_server, held={}, status=500),
        }
    return {
        "healthy": FakeMediaSource({HELD_ID: PAYLOAD}, max_bytes=1024),
        "unreachable": FakeMediaSource({HELD_ID: PAYLOAD}, unavailable=True, max_bytes=1024),
    }


async def test_a_held_attachment_comes_back_byte_for_byte(sources):
    got = await sources["healthy"].fetch(ref=MediaRef(platform_id=HELD_ID))
    assert got.available is True
    assert got.data == PAYLOAD


async def test_a_missing_attachment_is_not_found_not_an_exception(sources):
    got = await sources["healthy"].fetch(ref=MediaRef(platform_id="no-such-id"))
    assert got.available is False
    assert got.reason == "not_found"
    assert got.data == b""


async def test_an_unreachable_daemon_is_unavailable_and_never_raises(sources):
    """The port forbids raising: the caller is the ingest path, and an
    exception there costs the MESSAGE to save bytes that are a bonus."""
    got = await sources["unreachable"].fetch(ref=MediaRef(platform_id=HELD_ID))
    assert got.available is False
    assert got.reason == "unreachable"


async def test_a_ref_with_no_platform_id_is_answered_without_asking(sources):
    """A sticker, or an attachment described without an id. There is nothing to
    ask for, so this must not be counted as the daemon failing."""
    got = await sources["healthy"].fetch(ref=MediaRef(platform_id=""))
    assert got.available is False
    assert got.reason == "no_id"


async def test_an_oversized_attachment_is_refused_on_the_declared_size(sources):
    """Refused before the bytes move, on the platform's claim. A policy, not a
    failure — and a distinct reason so it never reads as a broken daemon."""
    got = await sources["healthy"].fetch(ref=MediaRef(platform_id=HELD_ID, size=99_999))
    assert got.available is False
    assert got.reason == "too_large"


async def test_every_implementation_declares_the_platform_it_is_bound_to(sources):
    for source in sources.values():
        assert source.platform == "signal"


# --- Signal-only: the wire shapes the fake has no equivalent for -------------


async def test_the_adapter_reads_a_bare_string_result_too(aiohttp_server):
    """Both plausible `getAttachment` result shapes are accepted, because no test
    here can verify which one signal-cli 0.14.7 actually returns."""
    source = await _signal(aiohttp_server, held={HELD_ID: PAYLOAD}, result_shape="string")
    got = await source.fetch(ref=MediaRef(platform_id=HELD_ID))
    assert got.available is True
    assert got.data == PAYLOAD


async def test_an_unreadable_result_shape_is_bad_payload_not_silence(aiohttp_server):
    """A signal-cli upgrade that renames the result key must be loud. The
    alternative — treating it as "no attachment" — is how a no-op ships green."""
    async def rpc(request):
        payload = await request.json()
        return web.json_response(
            {"jsonrpc": "2.0", "result": {"unexpected": 1}, "id": payload.get("id")}
        )

    app = web.Application()
    app.router.add_post("/api/v1/rpc", rpc)
    server = await aiohttp_server(app)
    source = SignalMediaSource(base_url=str(server.make_url("")))
    got = await source.fetch(ref=MediaRef(platform_id=HELD_ID))
    assert got.available is False
    assert got.reason == "bad_payload"


async def test_calling_a_method_the_daemon_does_not_implement_is_not_success(aiohttp_server):
    """The negative control, as a test. If `fetch` ever asked for a method
    signal-cli does not serve, the daemon answers -32601 and this goes red."""
    server = await aiohttp_server(_daemon(held={HELD_ID: PAYLOAD}))
    source = SignalMediaSource(base_url=str(server.make_url("")))
    got = await source.fetch(ref=MediaRef(platform_id=HELD_ID))
    assert got.available is True, (
        "the daemon fixture only serves getAttachment; anything else gets -32601"
    )


async def test_bytes_over_the_ceiling_are_refused_even_when_the_size_was_not_declared(
    aiohttp_server,
):
    """The declared size is a claim and can be wrong or absent, so the measured
    length is checked too."""
    source = await _signal(aiohttp_server, held={HELD_ID: b"x" * 4096})
    got = await source.fetch(ref=MediaRef(platform_id=HELD_ID))
    assert got.available is False
    assert got.reason == "too_large"


# --- Stickers: `getSticker` ---------------------------------------------------

HELD_STICKER = ("8c6ce85a3156b8e498e1ba0e97bc6620", 15)
STICKER_BYTES = b"RIFF\x00\x00\x00\x00WEBPVP8 sticker-ish"


def _sticker_daemon(*, held: dict[tuple[str, int], bytes]):
    """Answers per request, in signal-cli 0.14.7's OWN shapes. An unknown pack
    and an unknown index are the SAME error there, so they are the same error
    here — a fake that distinguished them would let the adapter claim a
    precision the daemon does not offer."""

    async def rpc(request):
        payload = await request.json()
        method = payload.get("method")
        if method != "getSticker":
            return web.json_response({
                "jsonrpc": "2.0", "id": payload.get("id"),
                "error": {"code": -32601, "message": "Method not implemented", "data": None},
            })
        params = payload.get("params") or {}
        pack, index = params.get("packId"), params.get("stickerId")
        if not isinstance(pack, str) or not isinstance(index, int):
            return web.json_response({
                "jsonrpc": "2.0", "id": payload.get("id"),
                "error": {"code": -32603, "message":
                          'Cannot invoke "String.length()" because "s" is null '
                          "(NullPointerException)", "data": None},
            })
        data = held.get((pack, index))
        if data is None:
            return web.json_response({
                "jsonrpc": "2.0", "id": payload.get("id"),
                "error": {"code": -1, "message":
                          f"Could not find sticker with ID: {index} in pack "
                          f"StickerPackId{{{pack}}}", "data": None},
            })
        return web.json_response({
            "jsonrpc": "2.0", "id": payload.get("id"),
            "result": {"data": base64.b64encode(data).decode("ascii")},
        })

    app = web.Application()
    app.router.add_post("/api/v1/rpc", rpc)
    return app


async def test_a_held_sticker_is_fetched_by_pack_and_index(aiohttp_server):
    server = await aiohttp_server(_sticker_daemon(held={HELD_STICKER: STICKER_BYTES}))
    source = SignalMediaSource(base_url=str(server.make_url("")))

    got = await source.fetch(ref=MediaRef(
        platform_id="", sticker=True,
        fetch_token=f"sticker:{HELD_STICKER[0]}:{HELD_STICKER[1]}",
    ))

    assert got.available is True
    assert got.data == STICKER_BYTES


async def test_an_unknown_sticker_is_not_found_not_a_crash(aiohttp_server):
    """Unknown pack and unknown index are indistinguishable at this seam — one
    reason, not two labels implying precision."""
    server = await aiohttp_server(_sticker_daemon(held={}))
    source = SignalMediaSource(base_url=str(server.make_url("")))

    got = await source.fetch(ref=MediaRef(
        platform_id="", sticker=True, fetch_token="sticker:deadbeef:1"))  # noqa: S106

    assert got.available is False
    assert got.reason == "not_found"


async def test_a_sticker_ref_never_calls_getattachment(aiohttp_server):
    """Routing a sticker to `getAttachment` with an empty id returns `no_id` and
    loses the sticker before the supported call is ever made. The daemon above
    answers -32601 for any other method, so a misrouted call is red rather than
    silently empty."""
    server = await aiohttp_server(_sticker_daemon(held={HELD_STICKER: STICKER_BYTES}))
    source = SignalMediaSource(base_url=str(server.make_url("")))

    got = await source.fetch(ref=MediaRef(
        platform_id="", sticker=True,
        fetch_token=f"sticker:{HELD_STICKER[0]}:{HELD_STICKER[1]}"))

    assert got.reason != "no_id"


async def test_a_malformed_token_is_bad_token_and_makes_no_call(aiohttp_server):
    server = await aiohttp_server(_sticker_daemon(held={}))
    source = SignalMediaSource(base_url=str(server.make_url("")))

    got = await source.fetch(ref=MediaRef(
        platform_id="", sticker=True, fetch_token="sticker:onlyonepart"))  # noqa: S106

    assert got.available is False
    assert got.reason == "bad_token"


# --- Discord: `DiscordMediaSource`, the port's second implementation --------


async def test_discord_fetches_a_sticker_by_cdn_url(aiohttp_server):
    async def cdn(request):
        return web.Response(body=STICKER_BYTES, content_type="image/png")

    app = web.Application()
    app.router.add_get("/stickers/900.png", cdn)
    server = await aiohttp_server(app)
    source = DiscordMediaSource(cdn_base=str(server.make_url("")))

    got = await source.fetch(ref=MediaRef(
        platform_id="900", media_type="image/png", sticker=True,
        fetch_token="sticker:900:png"))  # noqa: S106

    assert got.available is True
    assert got.data == STICKER_BYTES


async def test_discord_refuses_a_ref_with_no_fetch_token():
    """A ref that is neither a sticker nor carries an `attachment:` token —
    the shape an embed or some future Discord object might produce. Nothing
    to fetch, so this must not be counted as the CDN failing."""
    source = DiscordMediaSource(cdn_base="http://unused.invalid")

    got = await source.fetch(ref=MediaRef(platform_id="123", media_type="image/png"))

    assert got.available is False
    assert got.reason == "not_supported"


async def test_discord_fetches_an_attachment_by_its_own_url(aiohttp_server):
    """DEC-118: attachment custody mirrors Signal's `getAttachment` — Discord
    just hands back the URL directly instead of an RPC handle. The allowlist
    is pointed at the test server's own loopback host, since a real
    `cdn.discordapp.com` socket cannot be probed from a test."""
    async def cdn(request):
        return web.Response(body=PAYLOAD, content_type="image/jpeg")

    app = web.Application()
    app.router.add_get("/attachments/1/2/photo.jpg", cdn)
    server = await aiohttp_server(app)
    url = server.make_url("/attachments/1/2/photo.jpg")
    source = DiscordMediaSource(
        cdn_base="http://unused.invalid", attachment_hosts={url.host},
    )

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/jpeg",
        fetch_token=f"attachment:{url}"))

    assert got.available is True
    assert got.data == PAYLOAD


async def test_discord_reports_a_missing_attachment_as_not_found(aiohttp_server):
    app = web.Application()
    server = await aiohttp_server(app)
    url = server.make_url("/attachments/1/2/missing.jpg")
    source = DiscordMediaSource(
        cdn_base="http://unused.invalid", attachment_hosts={url.host},
    )

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/jpeg",
        fetch_token=f"attachment:{url}"))

    assert got.available is False
    assert got.reason == "not_found"


async def test_discord_refuses_an_oversize_attachment(aiohttp_server):
    async def cdn(request):
        return web.Response(body=b"x" * 4096, content_type="image/jpeg")

    app = web.Application()
    app.router.add_get("/attachments/1/2/big.jpg", cdn)
    server = await aiohttp_server(app)
    url = server.make_url("/attachments/1/2/big.jpg")
    source = DiscordMediaSource(
        cdn_base="http://unused.invalid", max_bytes=1024, attachment_hosts={url.host},
    )

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/jpeg",
        fetch_token=f"attachment:{url}"))

    assert got.available is False
    assert got.reason == "too_large"


async def test_discord_refuses_an_attachment_url_off_its_own_cdn():
    """The token is `fetch_token`, not `ref.platform_id` — opaque and
    adapter-written per `MediaRef`'s own contract, but here it is built from
    something the EVENT supplied (`Attachment.url`), unlike a sticker URL
    which is derived from an id and a four-word enum. A forged or
    redirect-poisoned URL pointing off Discord's own CDN must be refused, not
    followed — the exact worry `DiscordMediaSource`'s own docstring raises
    about following a payload-supplied URL."""
    source = DiscordMediaSource(cdn_base="http://unused.invalid")

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/jpeg",
        fetch_token="attachment:https://evil.example/steal.jpg"))  # noqa: S106

    assert got.available is False
    assert got.reason == "bad_token"


async def test_discord_reports_a_missing_sticker_as_not_found(aiohttp_server):
    app = web.Application()
    server = await aiohttp_server(app)
    source = DiscordMediaSource(cdn_base=str(server.make_url("")))

    got = await source.fetch(ref=MediaRef(
        platform_id="404", media_type="image/png", sticker=True,
        fetch_token="sticker:404:png"))  # noqa: S106

    assert got.available is False
    assert got.reason == "not_found"


async def test_discord_refuses_an_oversize_body(aiohttp_server):
    async def cdn(request):
        return web.Response(body=b"x" * 4096, content_type="image/png")

    app = web.Application()
    app.router.add_get("/stickers/900.png", cdn)
    server = await aiohttp_server(app)
    source = DiscordMediaSource(cdn_base=str(server.make_url("")), max_bytes=1024)

    got = await source.fetch(ref=MediaRef(
        platform_id="900", media_type="image/png", sticker=True,
        fetch_token="sticker:900:png"))  # noqa: S106

    assert got.available is False
    assert got.reason == "too_large"


async def test_discord_refuses_an_oversize_body_with_no_declared_length(aiohttp_server):
    """`test_discord_refuses_an_oversize_body` above sends a plain `web.Response`,
    which aiohttp gives a correct `Content-Length` header — so that test only
    ever exercises the DECLARED-length check, never the post-read recheck. A
    `StreamResponse` carries no `Content-Length` at all, so this response can
    only be caught by measuring the bytes actually read: deleting the post-read
    check in `DiscordMediaSource.fetch` turns this test red while leaving the
    declared-length test above green."""
    async def cdn(request):
        resp = web.StreamResponse(status=200, headers={"Content-Type": "image/png"})
        await resp.prepare(request)
        await resp.write(b"x" * 4096)
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/stickers/900.png", cdn)
    server = await aiohttp_server(app)
    source = DiscordMediaSource(cdn_base=str(server.make_url("")), max_bytes=1024)

    got = await source.fetch(ref=MediaRef(
        platform_id="900", media_type="image/png", sticker=True,
        fetch_token="sticker:900:png"))  # noqa: S106

    assert got.available is False
    assert got.reason == "too_large"


async def test_discord_reads_a_chunked_body_to_completion(aiohttp_server):
    """Discord's CDN streams attachments in ~1.4 KB packets, and
    `StreamReader.read(n)` returns as soon as the FIRST buffered chunk arrives —
    it does not wait for `n` bytes or EOF. A body read that way is truncated to
    its first packet and still passes the post-read size check. This response
    writes the body in several small chunks, so a single-shot
    `read(max_bytes + 1)` returns only the first chunk and this test goes red;
    the loop must read until EOF."""
    body = b"".join(bytes([i % 251]) * 1000 for i in range(10))  # 10 KB in 10 chunks

    async def cdn(request):
        resp = web.StreamResponse(status=200, headers={"Content-Type": "image/jpeg"})
        await resp.prepare(request)
        for i in range(10):
            await resp.write(body[i * 1000:(i + 1) * 1000])
            # A real CDN does not deliver all chunks in one flush; without this
            # delay aiohttp coalesces the writes and `read(n)` happens to see the
            # whole body, so a single-shot read would pass. The delay makes the
            # test exercise the real wire shape.
            await asyncio.sleep(0.01)
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/attachments/1/2/photo.jpg", cdn)
    server = await aiohttp_server(app)
    url = server.make_url("/attachments/1/2/photo.jpg")
    source = DiscordMediaSource(
        cdn_base="http://unused.invalid", max_bytes=1024 * 1024,
        attachment_hosts={url.host},
    )

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/jpeg",
        fetch_token=f"attachment:{url}"))

    assert got.available is True
    assert got.data == body


async def test_discord_refuses_a_chunked_body_over_the_ceiling(aiohttp_server):
    """The same chunked path must still refuse a body that exceeds the ceiling
    once the loop has read it all — the post-read measurement, not the
    declared length (a StreamResponse carries none)."""
    async def cdn(request):
        resp = web.StreamResponse(status=200, headers={"Content-Type": "image/jpeg"})
        await resp.prepare(request)
        for _ in range(4):
            await resp.write(b"x" * 1000)
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/attachments/1/2/big.jpg", cdn)
    server = await aiohttp_server(app)
    url = server.make_url("/attachments/1/2/big.jpg")
    source = DiscordMediaSource(
        cdn_base="http://unused.invalid", max_bytes=2048,
        attachment_hosts={url.host},
    )

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/jpeg",
        fetch_token=f"attachment:{url}"))

    assert got.available is False
    assert got.reason == "too_large"


# --- Instagram: `InstagramMediaSource`, the port's third implementation -----

IG_PHOTO = b"\xff\xd8\xff\xe0 instagram jpeg"

# Hosts observed serving media to this account. They differ in the PoP segment,
# which is why the allowlist matches a suffix — an exact set goes stale the
# first time the account is served from another edge, and it goes stale as a
# silent loss of every image.
MEASURED_CDN_HOSTS = (
    "scontent.fhkg3-1.fna.fbcdn.net",
    "instagram.fhkg3-1.fna.fbcdn.net",
    "scontent.cdninstagram.com",
    "external.fhkg3-1.fna.fbcdn.net",
)


class _NoSocketSession:
    """A `ClientSession` that cannot be constructed without being noticed.

    The allowlist's whole claim is that a refused host is refused BEFORE a
    socket opens. A test that only reads `reason` cannot tell a pre-flight
    refusal from a connection that was attempted and failed, so the session
    itself is the instrument: every construction is recorded, and a refused
    host must record none.
    """

    def __init__(self, *args, **kwargs):
        raise AssertionError("a socket was opened for a host the allowlist refuses")


def _watch_sockets(monkeypatch) -> list[str]:
    """Replace the module's `aiohttp.ClientSession` with a recorder."""
    opened: list[str] = []

    class _Recorder:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url):
            opened.append(url)
            raise RuntimeError("no network in this test")

    monkeypatch.setattr(instagram_media.aiohttp, "ClientSession", _Recorder)
    return opened


async def test_instagram_fetches_an_attachment_by_its_own_cdn_url(aiohttp_server):
    """The bridge emits a directly fetchable CDN url on `media.url` — it returns
    200 with no cookies, no user-agent and no referer, so the signature in the
    url is the authorization and the url IS the handle. The allowlist is pointed
    at the test server's loopback host, since a real `scontent.*.fbcdn.net`
    socket cannot be opened from a test."""
    async def cdn(request):
        return web.Response(body=IG_PHOTO, content_type="image/jpeg")

    app = web.Application()
    app.router.add_get("/v/t51.2885-15/photo.jpg", cdn)
    server = await aiohttp_server(app)
    url = server.make_url("/v/t51.2885-15/photo.jpg")
    source = InstagramMediaSource(attachment_host_suffixes={url.host})

    got = await source.fetch(ref=MediaRef(
        platform_id="970288316113989", media_type="image/*",
        fetch_token=f"attachment:{url}"))

    assert got.available is True
    assert got.data == IG_PHOTO


async def test_instagram_refuses_a_url_off_its_own_cdn_without_opening_a_socket(
    monkeypatch,
):
    """The token is built from something the EVENT supplied (`media.url`), so
    a forged payload is a fetcher pointed anywhere. It must be refused before
    a socket opens — proven by the session, not by the reason alone."""
    monkeypatch.setattr(
        instagram_media.aiohttp, "ClientSession", _NoSocketSession,
    )
    source = InstagramMediaSource()

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/*",
        fetch_token="attachment:https://evil.example/steal.jpg"))  # noqa: S106

    assert got.available is False
    assert got.reason == "bad_token"


@pytest.mark.parametrize("host", [
    # A domain that merely ENDS with the right letters, and one that merely
    # CONTAINS the allowed domain — the two forgeries a substring check waves
    # through.
    "notfbcdn.net",
    "fbcdn.net.evil.example",
    "cdninstagram.com.evil.example",
    "evil.example",
    "localhost",
])
async def test_instagram_refuses_a_lookalike_host_without_opening_a_socket(
    monkeypatch, host,
):
    monkeypatch.setattr(
        instagram_media.aiohttp, "ClientSession", _NoSocketSession,
    )
    source = InstagramMediaSource()

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/*",
        fetch_token=f"attachment:https://{host}/x.jpg"))

    assert got.available is False
    assert got.reason == "bad_token"


@pytest.mark.parametrize("host", MEASURED_CDN_HOSTS)
async def test_instagram_admits_every_measured_cdn_host(monkeypatch, host):
    """The observed hosts all reach the socket step. An exact-host allowlist
    would admit none of them once the account is served from another edge."""
    opened = _watch_sockets(monkeypatch)
    source = InstagramMediaSource()

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/*",
        fetch_token=f"attachment:https://{host}/v/t51.2885-15/photo.jpg"))

    assert opened == [f"https://{host}/v/t51.2885-15/photo.jpg"]
    # The recorder raises instead of connecting, so the honest answer past the
    # allowlist is a transport failure — never `bad_token`.
    assert got.reason == "unreachable"


async def test_instagram_refuses_one_time_media_because_it_carries_no_url():
    """`RAVEN_*` arrives with `attachment: null`, so the parser records no
    token. Fetching view-once media IS viewing it and is visible to the
    sender — an operator's consent decision, never a fetcher's default. This
    must read as `not_supported` (nothing to fetch), not as the CDN failing."""
    source = InstagramMediaSource()

    got = await source.fetch(ref=MediaRef(platform_id="", media_type=""))

    assert got.available is False
    assert got.reason == "not_supported"


async def test_instagram_reports_a_missing_attachment_as_not_found(aiohttp_server):
    app = web.Application()
    server = await aiohttp_server(app)
    url = server.make_url("/v/t51.2885-15/gone.jpg")
    source = InstagramMediaSource(attachment_host_suffixes={url.host})

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/*", fetch_token=f"attachment:{url}"))

    assert got.available is False
    assert got.reason == "not_found"


async def test_instagram_refuses_an_oversize_attachment(aiohttp_server):
    async def cdn(request):
        return web.Response(body=b"x" * 4096, content_type="image/jpeg")

    app = web.Application()
    app.router.add_get("/v/t51.2885-15/big.jpg", cdn)
    server = await aiohttp_server(app)
    url = server.make_url("/v/t51.2885-15/big.jpg")
    source = InstagramMediaSource(
        max_bytes=1024, attachment_host_suffixes={url.host},
    )

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/*", fetch_token=f"attachment:{url}"))

    assert got.available is False
    assert got.reason == "too_large"


async def test_instagram_reads_a_chunked_body_to_completion(aiohttp_server):
    """`InstagramMediaSource._read_limited` is a COPY of Discord's, and the
    defect it encodes (a multi-packet body truncated to its first packet) is the
    one thing that must never be re-derived from scratch. This is the same case
    run against the second copy, so a drift between them is red rather than
    silent."""
    body = b"".join(bytes([i % 251]) * 1000 for i in range(10))  # 10 KB in 10 chunks

    async def cdn(request):
        resp = web.StreamResponse(status=200, headers={"Content-Type": "image/jpeg"})
        await resp.prepare(request)
        for i in range(10):
            await resp.write(body[i * 1000:(i + 1) * 1000])
            await asyncio.sleep(0.01)
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/v/t51.2885-15/photo.jpg", cdn)
    server = await aiohttp_server(app)
    url = server.make_url("/v/t51.2885-15/photo.jpg")
    source = InstagramMediaSource(
        max_bytes=1024 * 1024, attachment_host_suffixes={url.host},
    )

    got = await source.fetch(ref=MediaRef(
        platform_id="2", media_type="image/*", fetch_token=f"attachment:{url}"))

    assert got.available is True
    assert got.data == body
