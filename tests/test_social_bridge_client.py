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

from nora.infra.social.bridge_client import OpenCliBridgeClient, _clamp_chars


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
        if self.mode == "realistic-post":
            # The exact opencli `twitter thread` shape from the live trace
            # (2026-08-31): first item = the post, rest = replies.
            return web.json_response({
                "status": "ok",
                "source_url": "https://x.com/i/status/2093779797512253814",
                "data": [
                    {
                        "id": "2093779797512253814",
                        "author": "MrAhmadAwais",
                        "name": "Ahmad Awais",
                        "text": "opus 5 is so bad lately. complete mess. glad "
                                "open models exist. it failed to find bugs even "
                                "deepseek flash discovered.",
                        "likes": 167,
                        "retweets": 3,
                        "created_at": "Sat Aug 29 19:16:18 +0000 2026",
                        "url": "https://x.com/MrAhmadAwais/status/2093779797512253814",
                        "quoted_tweet": {
                            "author": "OpenAI",
                            "text": "We're ending our partnership with Cursor.",
                        },
                    },
                    {
                        "id": "2094141809891144149",
                        "author": "jembatanaicom",
                        "text": "@MrAhmadAwais This is exactly why single-model"
                                " loyalty makes less sense every month.",
                        "in_reply_to": "2093779797512253814",
                        "created_at": "Sun Aug 30 19:14:49 +0000 2026",
                        "url": "https://x.com/jembatanaicom/status/2094141809891144149",
                    },
                ],
            })
        if self.mode == "realistic-profile":
            return web.json_response({
                "status": "ok",
                "source_url": "https://x.com/nasa",
                "data": [
                    {
                        "screen_name": "nasa",
                        "name": "NASA",
                        "bio": "Exploration lives here.",
                        "followers": 100,
                        "following": 2,
                        "tweets": 5000,
                        "created_at": "Sat Aug 29 19:16:18 +0000 2026",
                        "url": "https://x.com/nasa",
                    }
                ],
            })
        if self.mode == "slow":
            await asyncio.sleep(5)
            return web.json_response({"status": "nothing_found"})
        # Realistic opencli shapes (verified against the live bridge
        # 2026-08-31): `profile` is a single-item list with `screen_name`/
        # `bio`, `thread`/`tweets` are lists of post dicts. Long bio → clamp
        # probe.
        return web.json_response({
            "status": "ok",
            "source_url": "https://x.com/nasa",
            "data": [
                {
                    "screen_name": "nasa",
                    "name": "NASA",
                    "bio": "x" * 5000,
                    "followers": 100,
                    "following": 2,
                    "tweets": 5000,
                    "created_at": "Sat Aug 29 19:16:18 +0000 2026",
                    "url": "https://x.com/nasa",
                }
            ],
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


async def test_content_is_readable_text_not_json_soup(bridge):
    """The regression pin for the 2026-08-31 parse failure.

    Before this fix, `bridge_client` json.dumps'ed the whole opencli payload
    and the char clamp tore mid-record, so the model received `\"`-escaped JSON
    cut at "founder @infer — and read it as "empty" (trace
    2bf6939b96d18d1389f0ea7f1eac99af). The content must now be the post's
    actual text verbatim, with its URL beside it — the same readable-text
    shape Tavily's `web_fetch` returns, which the model demonstrably reads.
    """
    fake, client = bridge
    fake.mode = "realistic-post"
    out = await client.read(platform="x", what="post",
                            target="2093779797512253814", limit=5)
    assert out["status"] == "ok"
    text = out["content"]
    assert "opus 5 is so bad lately. complete mess" in text
    assert "https://x.com/MrAhmadAwais/status/2093779797512253814" in text
    assert "167 likes" in text
    # The post text rides PLAIN — no double-escaped JSON junk the model has to
    # unescape twice. (The untrusted wrapper legitimately contains quotes, but
    # a `\"`-escape sequence from a nested json.dumps must not.)
    assert '\\"' not in text.replace("\\n", "")


async def test_profile_renders_handle_bio_and_stats(bridge):
    """Profile data renders as `handle (name)` + bio + numbers — the shape a
    model quotes directly, not an object dump it has to parse."""
    fake, client = bridge
    fake.mode = "realistic-profile"
    out = await _read(client)
    assert out["status"] == "ok"
    text = out["content"]
    assert "nasa (NASA)" in text
    assert "Exploration lives here." in text
    assert "100 followers" in text
    assert "2 following" in text
    assert "https://x.com/nasa" in text


async def test_clamp_prefers_an_item_boundary():
    """Truncation backs off to the last `\\n\\n` separator instead of tearing
    mid-record — the first post (and its text) stays whole."""
    text = "first post body here\n\nsecond post body here\n\nnasa (NASA)"
    clamped, truncated = _clamp_chars(text, 40)
    assert truncated is True
    assert clamped.startswith("first post body here")
    assert "second" not in clamped


async def test_clamp_marks_truncation_when_single_item_exceeds_budget():
    text = "x" * 5000
    clamped, truncated = _clamp_chars(text, 100)
    assert truncated is True
    assert len(clamped) <= 105  # clamp + the ellipsis marker


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
