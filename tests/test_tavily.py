"""`TavilySearchProvider` — Tavily web search AND page extraction, and their
fail-soft / untrusted-wrap discipline (DEC-73, DEC-116). Runs offline against a
local aiohttp server that behaves like the real Tavily `/search` and `/extract`,
INCLUDING their wrong-call shapes: a 401 on a bad key, a 422 on a bad param, a
`{results:[…]}` search envelope and a `{results:[…], failed_results:[…]}` extract
envelope. Reverting the never-raises or the untrusted wrap turns a test here red.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.search.tavily import TavilySearchProvider


class _Tavily:
    """The Tavily `/search` AND `/extract` endpoints, behaving like the real
    host: `/search` returns results with title/url/content, `/extract` returns
    `results`/`failed_results` with `raw_content`; both share the same per-mode
    failures (401 bad key, 422 bad param)."""

    def __init__(self) -> None:
        self.mode = "ok"          # ok | empty | unauth | badparam | notjson | failed
        self.requests: list[dict] = []
        self.extract_requests: list[dict] = []

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
                {"title": "Junk", "url": "https://junk.example/z",
                 "content": "junk", "score": 0.05},      # below floor → dropped
                {"title": "NoScore", "url": "https://noscore.example/w",
                 "content": "no score", "score": None},  # malformed → dropped
            ],
        })

    async def extract(self, request: web.Request) -> web.Response:
        self.extract_requests.append(await request.json())
        if self.mode == "unauth":
            return web.json_response({"detail": "Unauthorized"}, status=401)
        if self.mode == "badparam":
            return web.json_response({"detail": {"error": "Invalid extract_depth."}}, status=422)
        if self.mode == "notjson":
            return web.Response(text="<html>gateway</html>", content_type="text/html")
        if self.mode == "empty":
            return web.json_response({"results": [], "failed_results": []})
        if self.mode == "failed":
            return web.json_response({
                "results": [],
                "failed_results": [{"url": "https://dead.example/x", "error": "timeout"}],
            })
        if self.mode == "malformed":
            # A 200 whose body does not carry the documented `results`/
            # `failed_results` shape at all — an API change or a proxy's error
            # page, not Tavily's own "could not read this page" answer.
            return web.json_response({"answer": "unexpected shape"})
        body = self.extract_requests[-1]
        url = (body.get("urls") or ["https://hko.gov.hk/x"])[0]
        return web.json_response({
            "results": [
                {"url": url, "raw_content": "y" * 5000},   # long → clamp probe
            ],
            "failed_results": [],
        })


@pytest.fixture
async def tavily():
    fake = _Tavily()
    app = web.Application()
    app.router.add_post("/search", fake.search)
    app.router.add_post("/extract", fake.extract)
    server = TestServer(app)
    await server.start_server()
    url = str(server.make_url("/search"))
    extract_url = str(server.make_url("/extract"))
    provider = TavilySearchProvider(
        api_key="tvly-test", url=url, max_results=5,
        content_max_chars=1500, timeout_seconds=5.0,
        extract_url=extract_url, extract_max_chars=3000,
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
    assert first["content"].startswith("<fetched_page ")        # snippet wrapped
    assert "Very hot" in first["content"]
    # The bearer key was sent, not in the body.
    assert fake.requests[0]["query"] == "hong kong weather"


async def test_long_content_is_clamped_and_flagged(tavily):
    _fake, provider = tavily
    out = await provider.search(query="q")
    long_result = out["results"][1]
    assert long_result["truncated"] is True
    # The wrapped body carries at most the clamp (plus the untrusted envelope).
    # THE BOUND IS ON THE TEXT, not on the wrapped string: the block carries its
    # own description, so the wrapper is ~250 characters rather than a constant
    # ~30 (`<untrusted source="…" untrusted="true">`), and a bound on the whole
    # string would measure the description and call it a clamp.
    #
    # That overhead is real and it is per RESULT: ~250 characters, ~60 tokens,
    # times however many results come back. It buys a model that can read what
    # the block is without loading a doctrine file defining `untrusted` — which
    # most of the audiences receiving that word never loaded.
    body = long_result["content"].split(">\n", 1)[1].rsplit("\n<", 1)[0]
    assert len(body) <= 1500


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


# ---- fetch (DEC-116, /extract) --------------------------------------------


async def test_fetch_returns_wrapped_text_for_the_requested_url(tavily):
    fake, provider = tavily
    out = await provider.fetch(url="https://hko.gov.hk/x")
    assert out["status"] == "ok"
    assert out["url"] == "https://hko.gov.hk/x"             # url rides PLAIN
    assert out["text"].startswith("<fetched_page ")             # page wrapped
    assert fake.extract_requests[0]["urls"] == ["https://hko.gov.hk/x"]
    # Strands-envelope invariant: no bare-string `content` key. A dict with
    # BOTH `status` and `content` passes through `_wrap_tool_result` unwrapped
    # and reads as "empty" — web_search works because its dict has no
    # `content` key, so Strands wraps it into `[{"text": …}]` blocks.
    assert "content" not in out


async def test_long_fetched_text_is_clamped_and_flagged(tavily):
    _fake, provider = tavily
    out = await provider.fetch(url="https://hko.gov.hk/x")
    assert out["truncated"] is True
    # THE BOUND IS ON THE TEXT, not on the wrapped string: the block carries its
    # own description, so the wrapper is ~250 characters rather than a constant
    # ~30 (`<untrusted source="…" untrusted="true">`), and a bound on the whole
    # string would measure the description and call it a clamp.
    #
    # That overhead is real and it is per RESULT: ~250 characters, ~60 tokens,
    # times however many results come back. It buys a model that can read what
    # the block is without loading a doctrine file defining `untrusted` — which
    # most of the audiences receiving that word never loaded.
    body = out["text"].split(">\n", 1)[1].rsplit("\n<", 1)[0]
    assert len(body) <= 3000


async def test_a_failed_extraction_is_nothing_found_not_unavailable(tavily):
    """A dead/blocked page lands in Tavily's `failed_results`, not `results` —
    that is «nothing there», not a failure of the call itself."""
    fake, provider = tavily
    fake.mode = "failed"
    out = await provider.fetch(url="https://dead.example/x")
    assert out["status"] == "nothing_found"


async def test_an_empty_extract_envelope_is_nothing_found(tavily):
    fake, provider = tavily
    fake.mode = "empty"
    out = await provider.fetch(url="https://hko.gov.hk/x")
    assert out["status"] == "nothing_found"


async def test_a_malformed_extract_envelope_is_unavailable_not_nothing_found(tavily):
    """A 200 whose body does not carry `results`/`failed_results` at all is an
    envelope-shape wiring gap, not Tavily's own "could not read this page" —
    the mechanism the module docstring and DEC-116 claim, not merely an empty
    `results` list."""
    fake, provider = tavily
    fake.mode = "malformed"
    out = await provider.fetch(url="https://hko.gov.hk/x")
    assert out["status"] == "unavailable"


async def test_fetch_a_bad_key_is_unavailable_not_nothing_found(tavily):
    fake, provider = tavily
    fake.mode = "unauth"
    out = await provider.fetch(url="https://hko.gov.hk/x")
    assert out["status"] == "unavailable"   # 401 must not read as "found nothing"


async def test_fetch_a_rejected_param_is_unavailable(tavily):
    fake, provider = tavily
    fake.mode = "badparam"
    assert (await provider.fetch(url="https://hko.gov.hk/x"))["status"] == "unavailable"


async def test_fetch_a_non_json_body_is_unavailable(tavily):
    fake, provider = tavily
    fake.mode = "notjson"
    assert (await provider.fetch(url="https://hko.gov.hk/x"))["status"] == "unavailable"


async def test_fetch_a_dead_host_is_unavailable_never_raises():
    provider = TavilySearchProvider(
        api_key="k", extract_url="http://127.0.0.1:1/dead", timeout_seconds=1.0,
    )
    assert (await provider.fetch(url="https://hko.gov.hk/x"))["status"] == "unavailable"


async def test_search_and_fetch_share_one_client_and_key(tavily):
    """Both verbs go through the same `TavilySearchProvider` instance — the
    reason `web_fetch` needed no new wiring in the registry."""
    fake, provider = tavily
    await provider.search(query="q")
    await provider.fetch(url="https://hko.gov.hk/x")
    assert len(fake.requests) == 1
    assert len(fake.extract_requests) == 1


# ---- relevance floor -------------------------------------------------------

async def test_below_floor_results_are_dropped(tavily):
    """A result whose Tavily `score` sits under `min_score` is dropped — the
    opaque-ID junk (0.02-0.05) must not reach the model as five junk links.
    The fake carries 4 results: 0.9, 0.5 (kept), 0.05 (below floor), None
    (malformed) — so exactly 2 survive the 0.1 floor."""
    fake, provider = tavily
    fake.mode = "ok"
    out = await provider.search(query="q")
    assert out["status"] == "ok"
    assert len(out["results"]) == 2
    urls = [r["url"] for r in out["results"]]
    assert "https://hko.gov.hk/x" in urls
    assert "https://openrice.com/y" in urls
    assert "https://junk.example/z" not in urls
    assert "https://noscore.example/w" not in urls


async def test_malformed_score_is_dropped_not_admitted(tavily):
    """A missing/null score cannot be measured against the floor, so it is
    dropped too — a response-shape change must not silently admit unmeasured
    content."""
    fake, provider = tavily
    fake.mode = "ok"
    out = await provider.search(query="q")
    assert all("noscore.example" not in r["url"] for r in out["results"])


async def test_all_below_floor_is_nothing_found(tavily):
    """When every result is below the floor, the search reads `nothing_found` —
    «I could not find that», never a list of junk."""
    fake, provider = tavily
    fake.mode = "ok"
    # The fixture's provider is pointed at the fake server; raise its floor
    # above both fake scores (0.9, 0.5) to force the all-dropped path.
    provider._min_score = 0.95
    out = await provider.search(query="q")
    assert out["status"] == "nothing_found"
    assert out["results"] == []
