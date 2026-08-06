"""`MediaSource` has two implementations, so it gets one suite both pass (DEC-45).

`nora.platform.signal.media.SignalMediaSource` runs against a real aiohttp
daemon serving the JSON-RPC shapes signal-cli actually returns;
`nora.testing.FakeMediaSource` is what the ingest tests use.

**The fake daemon here answers DIFFERENTLY per request, deliberately.** That is
the whole point of the rule this file exists to satisfy: a stub returning bytes
for anything would prove that the adapter can decode base64 and prove nothing
about whether it asks signal-cli the right question. So the daemon below keys
off `params.id` and returns the real not-found error for an id it does not
hold — the shape observed from the live daemon on 2026-08-06:

    {"jsonrpc":"2.0","error":{"code":-1,
     "message":"Could not find attachment with ID: nonexistent-probe"},"id":"p3"}

and that error is provably distinct from an unimplemented method, which the
same probe confirmed answers `-32601 "Method not implemented"`. Reverting
`SignalMediaSource.fetch`'s method name to something signal-cli does not serve
makes `test_a_missing_attachment_is_not_found` red rather than leaving it green
against a daemon that says yes to everything.

What no test here can establish is the shape of a SUCCESSFUL `getAttachment`
result: no attachment has ever reached this deployment, so there is no id to
fetch and the success case is fixtures only. The adapter accepts both plausible
shapes and counts anything else as `bad_payload`; see its docstring.
"""

from __future__ import annotations

import base64

import pytest
from aiohttp import web

from nora.domain.media import MediaRef
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
    """Both plausible `getAttachment` result shapes are accepted, because which
    one signal-cli 0.14.7 actually returns cannot be verified here — no
    attachment has ever arrived on this deployment."""
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
    signal-cli does not serve, the daemon answers -32601 and this stays red —
    which is the property the live probe on 2026-08-06 established by hand."""
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
