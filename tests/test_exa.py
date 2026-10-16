"""`ExaSearchProvider` — Exa web search AND page extraction, and the fail-soft /
untrusted-wrap discipline it holds identically to Tavily (DEC-157). Runs offline
against a local aiohttp server that behaves like the real Exa `/search` and
`/contents`, INCLUDING their wrong-call shapes: a 401 on a bad key, a 422 on a
bad param, a `{requestId, results:[…]}` search envelope and a `{requestId,
results:[…], statuses:[…]}` contents envelope. Reverting the never-raises, the
untrusted wrap, or the `maxCharacters` request bound turns a test here red.

The Exa-SPECIFIC assertions — the ones a copy of `test_tavily.py` would not
have — are at the bottom: no relevance floor (there is no score to floor), and
the `maxCharacters` ceiling that keeps a large `content_max_chars` from 422ing
the whole call.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.search.exa import ExaSearchProvider


class _Exa:
    """The Exa `/search` AND `/contents` endpoints, behaving like the real host:
    `/search` returns results with title/url/text and NO score field (deprecated
    out of auto search); `/contents` returns `results` alongside a `statuses`
    array whose entry says whether the crawl succeeded. Both share the same
    per-mode failures (401 bad key, 422 bad param)."""

    def __init__(self) -> None:
        # ok | empty | unauth | badparam | notjson | failed | malformed | live_fails | gone
        # `live_fails`: a crawl fails, the cache (`maxAgeHours: -1`) answers.
        self.mode = "ok"
        self.requests: list[dict] = []
        self.contents_requests: list[dict] = []

    def _failure(self) -> web.Response | None:
        if self.mode == "unauth":
            return web.json_response({"error": "Unauthorized"}, status=401)
        if self.mode == "badparam":
            return web.json_response(
                {"error": "maxCharacters must be <= 10000"}, status=422
            )
        if self.mode == "notjson":
            return web.Response(text="<html>gateway</html>", content_type="text/html")
        return None

    def last_search_cap(self) -> int | None:
        if not self.requests:
            return None
        contents = self.requests[-1].get("contents") or {}
        text = contents.get("text") or {}
        cap = text.get("maxCharacters")
        return int(cap) if isinstance(cap, int) else None

    async def search(self, request: web.Request) -> web.Response:
        self.requests.append(await request.json())
        if (failed := self._failure()) is not None:
            return failed
        if self.mode == "empty":
            return web.json_response({"requestId": "r1", "results": []})
        return web.json_response({
            "requestId": "r1",
            "results": [
                {"title": "HKO", "url": "https://hko.gov.hk/x",
                 "id": "https://hko.gov.hk/x", "text": "Very hot, 34C in Yuen Long."},
                    {"title": "OpenRice", "url": "https://openrice.com/y",
                 "id": "https://openrice.com/y",
                 # Already cut to the requested ceiling, like the real host.
                 "text": ("x" * 8000)[:((self.last_search_cap()) or 5000)]},
            ],
            "resolvedSearchType": "neural",
            "costDollars": {"total": 0.007},
        })

    async def contents(self, request: web.Request) -> web.Response:
        self.contents_requests.append(await request.json())
        if (failed := self._failure()) is not None:
            return failed
        live_failed = self.mode == "live_fails" and body_age(self.contents_requests[-1]) != -1
        if self.mode == "gone":
            return web.json_response({
                "requestId": "r2", "results": [],
                "statuses": [{"id": "https://gone.example/x", "status": "error",
                              "source": "crawled",
                              "error": {"tag": "CRAWL_NOT_FOUND", "httpStatusCode": 410}}],
            })
        if self.mode in ("empty", "failed") or live_failed:
            # Exa's own miss: nothing crawled, and the statuses array says why.
            return web.json_response({
                "requestId": "r2",
                "results": [],
                "statuses": [{
                    "id": "https://dead.example/x", "status": "error",
                    "source": "crawled",
                    "error": {"tag": "CRAWL_TIMEOUT"},
                }],
            })
        if self.mode == "malformed":
            # A 200 whose body carries neither the documented `results` list nor
            # a `statuses` array — an API change or a proxy's error page, not
            # Exa's own "could not crawl this page" answer.
            return web.json_response({"requestId": "r2", "answer": "unexpected shape"})
        body = self.contents_requests[-1]
        url = (body.get("urls") or ["https://hko.gov.hk/x"])[0]
        # Behave like the real host: Exa honours `maxCharacters` and returns a
        # page ALREADY CUT to it. A fake that ignored the field would hide the
        # truncation-under-reporting bug.
        cap = (body.get("text") or {}).get("maxCharacters") or 5000
        return web.json_response({
            "requestId": "r2",
            "results": [{"id": url, "url": url, "title": "HKO", "text": ("y" * 8000)[:cap]}],
            "statuses": [{"id": url, "status": "success", "source": "crawled"}],
        })


def body_age(body: dict) -> int | None:
    """The `maxAgeHours` a `/contents` request carried, or None when absent."""
    age = body.get("maxAgeHours")
    return int(age) if isinstance(age, int) else None


@pytest.fixture
async def exa():
    fake = _Exa()
    app = web.Application()
    app.router.add_post("/search", fake.search)
    app.router.add_post("/contents", fake.contents)
    server = TestServer(app)
    await server.start_server()
    provider = ExaSearchProvider(
        api_key="exa-test",
        url=str(server.make_url("/search")),
        contents_url=str(server.make_url("/contents")),
        max_results=5, content_max_chars=1500, extract_max_chars=3000,
        timeout_seconds=5.0,
    )
    yield fake, provider
    await server.close()


# ---- search ---------------------------------------------------------------


async def test_search_returns_wrapped_results_with_plain_urls(exa):
    fake, provider = exa
    out = await provider.search(query="hong kong weather")
    assert out["status"] == "ok"
    assert len(out["results"]) == 2
    first = out["results"][0]
    assert first["url"] == "https://hko.gov.hk/x"           # url rides PLAIN
    assert first["content"].startswith("<fetched_page ")        # page text wrapped
    assert "Very hot" in first["content"]
    assert fake.requests[0]["query"] == "hong kong weather"


async def test_search_sends_the_key_as_a_header_never_in_the_body(exa):
    """The key is `x-api-key`, and a body-borne key would leak into any request
    log the operator or Exa keeps."""
    fake, provider = exa
    await provider.search(query="q")
    body = fake.requests[0]
    assert "apiKey" not in body and "api_key" not in body and "key" not in body


async def test_long_content_is_clamped_and_flagged(exa):
    _fake, provider = exa
    out = await provider.search(query="q")
    long_result = out["results"][1]
    assert long_result["truncated"] is True
    # THE BOUND IS ON THE PAGE TEXT, not on the wrapped string. The block
    # carries its own description, so the wrapper is ~250 characters, not a
    # constant ~30 (`<untrusted source="…" untrusted="true">`); a bound on the
    # whole string would measure the description and call it a clamp.
    #
    # That overhead is real and it is per RESULT: ~250 characters, ~60 tokens,
    # times however many results a search returns. It buys a model that can
    # read what the block is without loading a doctrine file that defines
    # `untrusted`.
    body = long_result["content"].split(">\n", 1)[1].rsplit("\n<", 1)[0]
    assert len(body) <= 1500


async def test_no_results_is_nothing_found_not_unavailable(exa):
    fake, provider = exa
    fake.mode = "empty"
    assert (await provider.search(query="q"))["status"] == "nothing_found"


async def test_a_bad_key_is_unavailable_not_nothing_found(exa):
    fake, provider = exa
    fake.mode = "unauth"
    out = await provider.search(query="q")
    assert out["status"] == "unavailable"   # 401 must not read as "found nothing"


async def test_a_rejected_param_is_unavailable(exa):
    fake, provider = exa
    fake.mode = "badparam"
    assert (await provider.search(query="q"))["status"] == "unavailable"


async def test_a_non_json_body_is_unavailable(exa):
    fake, provider = exa
    fake.mode = "notjson"
    assert (await provider.search(query="q"))["status"] == "unavailable"


async def test_a_dead_host_is_unavailable_never_raises():
    provider = ExaSearchProvider(
        api_key="k", url="http://127.0.0.1:1/dead", timeout_seconds=1.0,
    )
    assert (await provider.search(query="q"))["status"] == "unavailable"


# ---- fetch (/contents) -----------------------------------------------------


async def test_fetch_returns_wrapped_text_for_the_requested_url(exa):
    fake, provider = exa
    out = await provider.fetch(url="https://hko.gov.hk/x")
    assert out["status"] == "ok"
    assert out["url"] == "https://hko.gov.hk/x"             # url rides PLAIN
    assert out["text"].startswith("<fetched_page ")             # page wrapped
    assert fake.contents_requests[0]["urls"] == ["https://hko.gov.hk/x"]
    # Strands-envelope invariant: no bare-string `content` key. A dict with BOTH
    # `status` and `content` passes through `_wrap_tool_result` unwrapped and
    # reads as "empty".
    assert "content" not in out


async def test_long_fetched_text_is_clamped_and_flagged(exa):
    _fake, provider = exa
    out = await provider.fetch(url="https://hko.gov.hk/x")
    assert out["truncated"] is True
    # THE BOUND IS ON THE TEXT, not on the wrapped string. The block carries
    # its own description, so the wrapper is ~250 characters, not a constant
    # ~30 (`<untrusted source="…" untrusted="true">`); a bound on the whole
    # string would measure the description and call it a clamp.
    #
    # That overhead is real and it is per RESULT: ~250 characters, ~60 tokens,
    # times however many results come back. It buys a model that can read what
    # the block is without loading a doctrine file defining `untrusted` — which
    # most of the audiences receiving that word never loaded.
    body = out["text"].split(">\n", 1)[1].rsplit("\n<", 1)[0]
    assert len(body) <= 3000


async def test_a_failed_crawl_is_nothing_found_not_unavailable(exa):
    """A dead/blocked page comes back as an empty `results` plus an error entry
    in `statuses` — «nothing there», not a failure of the call itself."""
    fake, provider = exa
    fake.mode = "failed"
    assert (await provider.fetch(url="https://dead.example/x"))["status"] == "nothing_found"


async def test_a_malformed_contents_envelope_is_unavailable_not_nothing_found(exa):
    """A 200 carrying neither a `results` list nor a `statuses` array is an
    envelope-shape wiring gap, not Exa's own "could not crawl this page" — the
    mechanism the module docstring claims, not merely an empty list."""
    fake, provider = exa
    fake.mode = "malformed"
    assert (await provider.fetch(url="https://hko.gov.hk/x"))["status"] == "unavailable"


async def test_an_empty_results_without_statuses_is_unavailable(exa):
    """The strictness lands exactly where the ambiguity is: an empty `results`
    with no well-formed `statuses` beside it cannot tell a dead link from a
    shape change, and R-27 resolves that as «I could not check».

    This is the falsifiable half of the test above — same empty `results`, and
    only the presence of `statuses` decides `nothing_found` vs `unavailable`."""
    class _NoStatuses(_Exa):
        async def contents(self, request: web.Request) -> web.Response:
            self.contents_requests.append(await request.json())
            return web.json_response({"requestId": "r2", "results": []})

    fake = _NoStatuses()
    app = web.Application()
    app.router.add_post("/contents", fake.contents)
    server = TestServer(app)
    await server.start_server()
    try:
        provider = ExaSearchProvider(
            api_key="k", contents_url=str(server.make_url("/contents")),
            timeout_seconds=5.0,
        )
        assert (await provider.fetch(url="https://x.example/y"))["status"] == "unavailable"
    finally:
        await server.close()


async def test_fetch_a_bad_key_is_unavailable_not_nothing_found(exa):
    fake, provider = exa
    fake.mode = "unauth"
    out = await provider.fetch(url="https://hko.gov.hk/x")
    assert out["status"] == "unavailable"


async def test_fetch_a_non_json_body_is_unavailable(exa):
    fake, provider = exa
    fake.mode = "notjson"
    assert (await provider.fetch(url="https://hko.gov.hk/x"))["status"] == "unavailable"


async def test_fetch_a_dead_host_is_unavailable_never_raises():
    provider = ExaSearchProvider(
        api_key="k", contents_url="http://127.0.0.1:1/dead", timeout_seconds=1.0,
    )
    assert (await provider.fetch(url="https://hko.gov.hk/x"))["status"] == "unavailable"


async def test_search_and_fetch_share_one_client_and_key(exa):
    """Both verbs go through the same `ExaSearchProvider` instance — the same
    reason `web_fetch` needed no new registry wiring on Tavily."""
    fake, provider = exa
    await provider.search(query="q")
    await provider.fetch(url="https://hko.gov.hk/x")
    assert len(fake.requests) == 1
    assert len(fake.contents_requests) == 1


# ---- what is Exa-specific (DEC-157) ---------------------------------------


async def test_no_relevance_floor_is_applied_because_there_is_no_score(exa):
    """Tavily drops results below `min_score`; Exa returns no score at all, so
    the adapter must keep every well-formed result rather than fail closed on a
    field the vendor does not send. Held fail-closed the way Tavily is, this
    fake's two scoreless results would BOTH vanish — which is exactly the
    regression this asserts against."""
    fake, provider = exa
    out = await provider.search(query="q")
    assert out["status"] == "ok"
    assert len(out["results"]) == 2
    assert all("score" not in r for r in fake.requests[0].get("contents", {}))


async def test_max_characters_is_requested_and_floored_into_exas_range(exa):
    """The clamp is sent to Exa so the bytes are never fetched, AND floored into
    Exa's 1-10000 `maxCharacters` range: a `content_max_chars` above the ceiling
    must yield a smaller page, never a 422 that makes the whole search
    `unavailable`."""
    fake, provider = exa
    await provider.search(query="q")
    assert fake.requests[0]["contents"]["text"]["maxCharacters"] == 1500
    await provider.fetch(url="https://hko.gov.hk/x")
    assert fake.contents_requests[0]["text"]["maxCharacters"] == 3000

    provider._content_max_chars = 50_000
    provider._extract_max_chars = 50_000
    await provider.search(query="q")
    await provider.fetch(url="https://hko.gov.hk/x")
    assert fake.requests[1]["contents"]["text"]["maxCharacters"] == 10_000
    assert fake.contents_requests[1]["text"]["maxCharacters"] == 10_000


async def test_the_search_type_is_sent_and_defaults_to_auto(exa):
    """`type` is Exa's search mode and the adapter must send whatever the
    operator configured — the deep* modes are a real cost/latency choice."""
    fake, provider = exa
    await provider.search(query="q")
    assert fake.requests[0]["type"] == "auto"
    provider._search_type = "fast"
    await provider.search(query="q")
    assert fake.requests[1]["type"] == "fast"


async def test_a_page_exa_already_cut_is_reported_as_truncated(exa):
    """Pushing `maxCharacters` to Exa means the page arrives ALREADY cut, the
    local clamp then finds nothing to clamp, and `truncated: False` would tell
    the model it holds a whole page when it holds the first N characters of
    one. Both verbs must read Exa's own cut back."""
    fake, provider = exa
    out = await provider.fetch(url="https://hko.gov.hk/x")
    assert out["status"] == "ok"
    # The fake cut to exactly the requested ceiling, so the LOCAL clamp does
    # not fire — only reading the sent ceiling back catches this.
    assert fake.contents_requests[0]["text"]["maxCharacters"] == 3000
    assert out["truncated"] is True

    searched = await provider.search(query="q")
    long_result = searched["results"][1]
    assert long_result["truncated"] is True


async def test_a_genuinely_short_page_is_not_reported_as_truncated(exa):
    """The positive control the test above needs: `truncated` must not become
    a constant. A page well under the ceiling is complete, and saying otherwise
    would cost a pointless second fetch on every short page."""
    _fake, provider = exa
    out = await provider.search(query="q")
    short = next(r for r in out["results"] if "hko.gov.hk" in r["url"])
    assert short["truncated"] is False


# ---- freshness (maxAgeHours) ------------------------------------------------
#
# Without `maxAgeHours` Exa serves its cache however old, so a stale copy of a
# list can omit items the live page has.


async def test_fetch_bounds_the_cache_age_at_24_hours_by_default(exa):
    fake, provider = exa
    await provider.fetch(url="https://hko.gov.hk/x")
    assert body_age(fake.contents_requests[0]) == 24


async def test_fetch_sends_the_configured_cache_age():
    fake = _Exa()
    app = web.Application()
    app.router.add_post("/contents", fake.contents)
    server = TestServer(app)
    await server.start_server()
    try:
        provider = ExaSearchProvider(
            api_key="k", contents_url=str(server.make_url("/contents")),
            fetch_max_age_hours=0, timeout_seconds=5.0,
        )
        await provider.fetch(url="https://hko.gov.hk/x")
        assert body_age(fake.contents_requests[0]) == 0
    finally:
        await server.close()


async def test_a_failed_fresh_read_falls_back_to_the_saved_copy_and_says_so(exa):
    fake, provider = exa
    fake.mode = "live_fails"
    out = await provider.fetch(url="https://hko.gov.hk/x")
    assert [body_age(b) for b in fake.contents_requests] == [24, -1]
    assert out["status"] == "ok"
    assert out["saved_copy"] is True
    # The reading rule comes before the page text it is about.
    assert list(out)[:2] == ["status", "note"]
    assert "saved copy" in out["note"] and "out of date" in out["note"]
    assert "never that they're wrong" in out["note"]
    assert out["text"].startswith("<fetched_page ")


async def test_a_page_neither_live_nor_saved_is_nothing_found(exa):
    fake, provider = exa
    fake.mode = "failed"
    out = await provider.fetch(url="https://dead.example/x")
    assert out["status"] == "nothing_found"
    assert [body_age(b) for b in fake.contents_requests] == [24, -1]


async def test_a_fresh_read_that_worked_is_not_retried_or_labelled(exa):
    fake, provider = exa
    out = await provider.fetch(url="https://hko.gov.hk/x")
    assert len(fake.contents_requests) == 1
    assert "saved_copy" not in out and "note" not in out


async def test_a_call_failure_is_not_retried_against_the_cache(exa):
    """`unavailable` is the CALL failing (bad key, 422): the cache would fail
    the same way, and a retry would only double the wait."""
    fake, provider = exa
    fake.mode = "unauth"
    assert (await provider.fetch(url="https://hko.gov.hk/x"))["status"] == "unavailable"
    assert len(fake.contents_requests) == 1


async def test_a_page_the_site_says_is_gone_is_not_retried_against_the_cache(exa):
    """A saved copy of a page that answers 410 describes something that no
    longer exists, so the gone answer stands."""
    fake, provider = exa
    fake.mode = "gone"
    assert (await provider.fetch(url="https://gone.example/x"))["status"] == "nothing_found"
    assert len(fake.contents_requests) == 1


async def test_the_live_crawl_gives_up_before_our_client_does(exa):
    """A client timeout is `unavailable` and never reaches the cache, so Exa's
    own crawl limit is sent under `timeout_seconds` (5 s in this fixture)."""
    fake, provider = exa
    await provider.fetch(url="https://hko.gov.hk/x")
    assert fake.contents_requests[0]["livecrawlTimeout"] == 1000
    fake.mode = "live_fails"
    await provider.fetch(url="https://hko.gov.hk/x")
    assert "livecrawlTimeout" not in fake.contents_requests[-1]   # cache only


def test_the_live_crawl_limit_follows_the_client_timeout():
    assert ExaSearchProvider(api_key="k", timeout_seconds=12.0)._livecrawl_timeout_ms() == 8000
    assert ExaSearchProvider(api_key="k", timeout_seconds=500.0)._livecrawl_timeout_ms() == 90000
