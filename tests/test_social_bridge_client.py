"""`OpenCliBridgeClient` — the social bridge read path and its fail-soft /
untrusted-wrap discipline (DEC-151). Runs offline against a local aiohttp
server that behaves like the real bridge (`nora.infra.social.bridge_server`),
INCLUDING its wrong shapes: a non-JSON body, a missing `status` key, an
unknown `status` value, an `ok` envelope with no `data`, and a 400/500.
Reverting the never-raises or the untrusted wrap turns a test here red.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.social.bridge_client import OpenCliBridgeClient


class _Bridge:
    """The bridge's `/read` endpoint, behaving like the real one: a 200 with a
    `{status, ...}` envelope for every READ outcome, a 400 for a refusal, and
    per-mode wrong shapes for the envelope-discipline probes."""

    def __init__(self) -> None:
        # ok | nothing | unavailable | refused | notjson | nostatus |
        # badstatus | nodata | error | slow
        self.mode = "ok"
        self.requests: list[dict] = []

    async def read(self, request: web.Request) -> web.Response:
        self.requests.append(await request.json())
        if self.mode == "refused":
            return web.json_response(
                {"status": "unavailable", "note": "that read is not supported"},
                status=400,
            )
        if self.mode == "error":
            return web.Response(text="boom", status=500)
        if self.mode == "notjson":
            return web.Response(text="<html>gateway</html>", content_type="text/html")
        if self.mode == "nostatus":
            return web.json_response({"data": {"items": []}})
        if self.mode == "badstatus":
            return web.json_response({"status": "everything_is_fine", "data": {}})
        if self.mode == "nodata":
            return web.json_response({"status": "ok"})
        if self.mode == "nothing":
            return web.json_response({"status": "nothing_found"})
        if self.mode == "unavailable":
            return web.json_response({"status": "unavailable"})
        if self.mode == "slow":
            await asyncio.sleep(5)
            return web.json_response({"status": "nothing_found"})
        return web.json_response({
            "status": "ok",
            "source_url": "https://x.com/nasa",
            "data": {"profile": "nasa", "bio": "x" * 5000},   # long → clamp probe
        })


@pytest.fixture
async def bridge():
    fake = _Bridge()
    app = web.Application()
    app.router.add_post("/read", fake.read)
    server = TestServer(app)
    await server.start_server()
    client = OpenCliBridgeClient(
        bridge_url=str(server.make_url("")).rstrip("/"), timeout_seconds=1.0,
        content_max_chars=1500,
    )
    yield fake, client
    await server.close()


async def _read(client: OpenCliBridgeClient) -> dict:
    return await client.read(platform="x", what="profile", target="nasa", limit=5)


async def test_ok_content_is_wrapped_and_the_url_rides_plain(bridge):
    fake, client = bridge
    out = await _read(client)
    assert out["status"] == "ok"
    assert out["url"] == "https://x.com/nasa"               # url rides PLAIN
    assert out["content"].startswith("<untrusted")           # content wrapped
    assert "nasa" in out["content"]
    # The request body is the four fields, nothing else.
    assert fake.requests[0] == {
        "platform": "x", "what": "profile", "target": "nasa", "limit": 5,
    }


async def test_long_content_is_clamped_and_flagged(bridge):
    _fake, client = bridge
    out = await _read(client)
    assert out["truncated"] is True
    # The wrapped body carries at most the clamp (plus the untrusted envelope).
    assert len(out["content"]) < 1800


async def test_nothing_found_passes_through(bridge):
    fake, client = bridge
    fake.mode = "nothing"
    assert (await _read(client))["status"] == "nothing_found"


async def test_bridge_unavailable_passes_through(bridge):
    fake, client = bridge
    fake.mode = "unavailable"
    assert (await _read(client))["status"] == "unavailable"


async def test_a_400_refusal_is_unavailable_never_nothing_found(bridge):
    """The bridge refusing the pair is a wiring gap (drift between the tool's
    vocabulary and the host allowlist) — «I could not check», not «nothing
    there»."""
    fake, client = bridge
    fake.mode = "refused"
    assert (await _read(client))["status"] == "unavailable"


async def test_a_500_is_unavailable(bridge):
    fake, client = bridge
    fake.mode = "error"
    assert (await _read(client))["status"] == "unavailable"


async def test_a_non_json_body_is_unavailable(bridge):
    fake, client = bridge
    fake.mode = "notjson"
    assert (await _read(client))["status"] == "unavailable"


async def test_an_envelope_with_no_status_key_is_unavailable(bridge):
    fake, client = bridge
    fake.mode = "nostatus"
    assert (await _read(client))["status"] == "unavailable"


async def test_an_unknown_status_value_is_unavailable(bridge):
    """An unknown status is the envelope not matching the documented shape —
    unavailable (R-27), never passed through to the model as-is."""
    fake, client = bridge
    fake.mode = "badstatus"
    assert (await _read(client))["status"] == "unavailable"


async def test_an_ok_envelope_with_no_data_is_unavailable(bridge):
    fake, client = bridge
    fake.mode = "nodata"
    assert (await _read(client))["status"] == "unavailable"


async def test_a_timeout_is_unavailable_never_raises(bridge):
    fake, client = bridge
    fake.mode = "slow"
    assert (await _read(client))["status"] == "unavailable"


async def test_a_dead_host_is_unavailable_never_raises():
    client = OpenCliBridgeClient(
        bridge_url="http://127.0.0.1:1/dead", timeout_seconds=1.0,
    )
    assert (await _read(client))["status"] == "unavailable"
