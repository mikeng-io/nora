"""`TavilySearchProvider` — Tavily web search and its fail-soft / untrusted-wrap
discipline (DEC-73). Runs offline against a local aiohttp server that behaves like
the real Tavily `/search`, INCLUDING its wrong-call shapes: a 401 on a bad key, a
422 on a bad param, and a `{results:[…]}` envelope. Reverting the never-raises or
the untrusted wrap turns a test here red.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.search.tavily import TavilySearchProvider


class _Tavily:
    """The Tavily `/search` endpoint, behaving like the real host: results with
    title/url/content, and per-mode failures (401 bad key, 422 bad param)."""

    def __init__(self) -> None:
        self.mode = "ok"          # ok | empty | unauth | badparam | notjson
        self.requests: list[dict] = []

    async def search(self, request: web.Request) -> web.Response:
        self.requests.append(await request.json())
        if self.mode == "unauth":
            return web.json_response({"detail": "Unauthorized"}, status=401)
        if self.mode == "badparam":
            return web.json_response({"detail": {"error": "Invalid country."}}, status=422)
        if self.mode == "notjson":
            return web.Response(text="<html>gateway</html>", content_type="text/html")
        if self.mode == "empty":
            return web.json_response({"query": "q", "results": []})
        return web.json_response({
            "query": "q",
            "answer": None,
            "results": [
                {"title": "HKO", "url": "https://hko.gov.hk/x",
                 "content": "Very hot, 34C in Yuen Long.", "score": 0.9},
                {"title": "OpenRice", "url": "https://openrice.com/y",
                 "content": "x" * 5000, "score": 0.5},   # long → clamp probe
            ],
        })


@pytest.fixture
async def tavily(monkeypatch):
    fake = _Tavily()
    app = web.Application()
    app.router.add_post("/search", fake.search)
    server = TestServer(app)
    await server.start_server()
    url = str(server.make_url("/search"))
    provider = TavilySearchProvider(
        api_key="tvly-test", url=url, max_results=5,
        content_max_chars=1500, timeout_seconds=5.0,
    )
    yield fake, provider
    await server.close()


async def test_search_returns_wrapped_results_with_plain_urls(tavily):
    fake, provider = tavily
    out = await provider.search(query="hong kong weather")
    assert out["status"] == "ok"
    assert len(out["results"]) == 2
    first = out["results"][0]
    assert first["url"] == "https://hko.gov.hk/x"          # url rides PLAIN
    assert first["content"].startswith("<untrusted")        # snippet wrapped
    assert "Very hot" in first["content"]
    # The bearer key was sent, not in the body.
    assert fake.requests[0]["query"] == "hong kong weather"


async def test_long_content_is_clamped_and_flagged(tavily):
    _fake, provider = tavily
    out = await provider.search(query="q")
    long_result = out["results"][1]
    assert long_result["truncated"] is True
    # The wrapped body carries at most the clamp (plus the untrusted envelope).
    assert len(long_result["content"]) < 1800


async def test_no_results_is_nothing_found_not_unavailable(tavily):
    fake, provider = tavily
    fake.mode = "empty"
    out = await provider.search(query="q")
    assert out["status"] == "nothing_found"


async def test_a_bad_key_is_unavailable_not_nothing_found(tavily):
    fake, provider = tavily
    fake.mode = "unauth"
    out = await provider.search(query="q")
    assert out["status"] == "unavailable"   # 401 must not read as "found nothing"


async def test_a_rejected_param_is_unavailable(tavily):
    fake, provider = tavily
    fake.mode = "badparam"
    assert (await provider.search(query="q"))["status"] == "unavailable"


async def test_a_non_json_body_is_unavailable(tavily):
    fake, provider = tavily
    fake.mode = "notjson"
    assert (await provider.search(query="q"))["status"] == "unavailable"


async def test_a_dead_host_is_unavailable_never_raises():
    provider = TavilySearchProvider(
        api_key="k", url="http://127.0.0.1:1/dead", timeout_seconds=1.0,
    )
    assert (await provider.search(query="q"))["status"] == "unavailable"


async def test_country_is_never_sent(tavily):
    """Hong Kong 422s Tavily's country list — the adapter must not send it."""
    fake, provider = tavily
    await provider.search(query="q")
    assert "country" not in fake.requests[0]
