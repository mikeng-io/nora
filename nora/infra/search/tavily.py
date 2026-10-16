"""`TavilySearchProvider` — web search AND page extraction via Tavily, behind the
`web_search`/`web_fetch` tools.

Tavily is an agent-first search API that returns ranked results WITH a relevant
content snippet per result in a SINGLE call, so «search then fetch» collapses
into one verb for the search case (the two-verb shape let a turn spend its whole
token budget fetching pages and never answer).

`web_fetch` (DEC-116) covers the case a snippet doesn't: somebody pastes a link
and wants what's actually on the page. `POST /extract` is a SEPARATE Tavily
endpoint that, like `/search`, fetches from TAVILY'S servers, never Nora's.

**No SSRF surface.** Tavily fetches from ITS servers, never Nora's, for BOTH
`/search` and `/extract`, so a model-invented `http://127.0.0.1:18080` cannot
reach anything of Nora's, whichever verb hands it over. No `is_global`/
`getaddrinfo` precheck, post-redirect re-validation or per-tag `allowed_urls`
fence is needed here.

What survives is the discipline every grounding client holds:
* NEVER raises — a non-200, non-JSON body, or transport error is a tool STATUS
  (`unavailable`), never an exception into the loop (R-5).
* Retrieved text is `fetched_page`-wrapped (R-13) — a page controls its own snippet,
  so it cannot forge the boundary — and CLAMPED, because a tool result is re-sent
  on every subsequent model call (the token lesson: bound what accumulates).
* The query is an EGRESS surface — an identifier/coordinate is screened at the
  tool layer (`nora.agent.tools`, unchanged) BEFORE it reaches here.

`POST https://api.tavily.com/search`, `Authorization: Bearer tvly-…`. `country` is
deliberately NOT sent — Hong Kong is not in Tavily's country list and sending it
422s the whole call; the keyless default already
returns HK results.

`fetch` is `POST https://api.tavily.com/extract`, the same auth, a SEPARATE
endpoint from `/search` — `urls: [url]`, `extract_depth`, `format: "markdown"`.
Response envelope is `{results: [{url, raw_content}], failed_results: [...]}`,
distinct from `/search`'s `{results: [{url, title, content}]}`; a URL Tavily
could not reach lands in `failed_results`, not `results`, and reads as
`nothing_found` — it is not a transport failure of the CALL, which is what
`unavailable` means here. `raw_content` is CLAMPED the same way a search
snippet is (`extract_max_chars`) — a full
page is far bigger than a snippet, and this is the field most likely to blow it.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

import aiohttp

from ._shared import (
    UNAVAILABLE_FETCH,
    UNAVAILABLE_SEARCH,
    clamp_chars,
    image_links,
    wrap,
)

log = logging.getLogger("nora.search")

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


class TavilySearchProvider:
    """Tavily web search AND page extraction, behind the `web_search`/`web_fetch`
    tools. Constructed once per process with the resolved key (DEC-8, from the
    environment at the composition root, never read here). Built only when
    `[search] enabled` AND a key is present; unwired, both tools answer
    `unavailable`."""

    def __init__(
        self,
        *,
        api_key: str,
        url: str = TAVILY_SEARCH_URL,
        max_results: int = 5,
        search_depth: str = "basic",
        content_max_chars: int = 1500,
        timeout_seconds: float = 12.0,
        extract_url: str = TAVILY_EXTRACT_URL,
        extract_depth: str = "basic",
        extract_max_chars: int = 3000,
        min_score: float = 0.1,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._max_results = max_results
        self._search_depth = search_depth
        self._content_max_chars = content_max_chars
        self._timeout_seconds = timeout_seconds
        self._extract_url = extract_url
        self._extract_depth = extract_depth
        self._extract_max_chars = extract_max_chars
        self._min_score = min_score

    async def search(self, *, query: str) -> dict[str, Any]:
        """Ranked results with a content snippet each, capped and wrapped. NEVER
        raises. Three states (R-27): `ok` with results, `nothing_found` for zero,
        `unavailable` for any transport/auth failure — «I could not check», never
        «nothing found»."""
        try:
            return await self._search(query)
        except Exception:
            # The never-raises backstop: a contract violation degrades to
            # unavailable, never propagates into the loop (R-5).
            log.exception("tavily search failed")
            return dict(UNAVAILABLE_SEARCH)

    async def _search(self, query: str) -> dict[str, Any]:
        body = {
            "query": query,
            "max_results": self._max_results,
            "search_depth": self._search_depth,
            "include_answer": False,          # Nora authors the reply, not Tavily
            "include_raw_content": False,     # the snippet is enough; full pages bloat
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(self._url, json=body, headers=headers) as response,
        ):
            text = await response.text()
            if response.status != 200:
                # 401 (bad key), 422 (bad param), 429 (rate limit) all land here —
                # the key never rides in a log line.
                log.warning("tavily answered %s", response.status)
                return dict(UNAVAILABLE_SEARCH)
        try:
            payload: Any = json.loads(text)
        except ValueError:
            log.warning("tavily did not return JSON")
            return dict(UNAVAILABLE_SEARCH)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return {"status": "nothing_found", "results": []}
        wrapped: list[dict[str, Any]] = []
        for item in results[: self._max_results]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            if not url:
                continue
            # Relevance floor: Tavily's own `score` separates signal from
            # noise — opaque-ID and current-state queries return 0.02-0.05
            # junk while real answers sit above 0.15. A
            # below-floor result is dropped, not wrapped: five junk links are
            # worse for the model than an honest «nothing found».
            #
            # FAIL CLOSED: a missing or
            # malformed score (null, string, bool, NaN) cannot be measured
            # against the floor, so it is dropped too — a response-shape
            # change must not silently admit unmeasured content. `NaN`
            # needs the explicit check: `NaN < x` is False, so it would
            # otherwise bypass the floor.
            score = item.get("score")
            if self._min_score > 0.0:
                if not isinstance(score, (int, float)) or math.isnan(score):
                    continue
                if score < self._min_score:
                    continue
            title = str(item.get("title") or "")
            content = str(item.get("content") or "")
            body_text = f"{title}\n{content}".strip() if title else content
            clamped, truncated = clamp_chars(body_text, self._content_max_chars)
            # Title + snippet are page-controlled → the whole body is wrapped
            # `fetched_page` with the source URL as provenance. The URL rides PLAIN
            # beside it so the model can cite it in the reply.
            wrapped.append({
                "url": url,
                "content": wrap("fetched_page", url, clamped),
                "truncated": truncated,
            })
        if not wrapped:
            return {"status": "nothing_found", "results": []}
        return {"status": "ok", "results": wrapped}

    async def fetch(self, *, url: str) -> dict[str, Any]:
        """One page's content, capped and wrapped. NEVER raises. Three states
        (R-27): `ok` with `text`, `nothing_found` when Tavily could not read
        the page (it landed in `failed_results`, not a transport failure of the
        CALL), `unavailable` for any transport/auth failure of the call itself —
        «I could not check», never «nothing found»."""
        try:
            return await self._fetch(url)
        except Exception:
            # The never-raises backstop: a contract violation degrades to
            # unavailable, never propagates into the loop (R-5).
            log.exception("tavily extract failed")
            return dict(UNAVAILABLE_FETCH)

    async def _fetch(self, url: str) -> dict[str, Any]:
        body = {
            "urls": [url],
            "extract_depth": self._extract_depth,
            "format": "markdown",
            "include_images": True,           # links only, for `media_fetch`
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(self._extract_url, json=body, headers=headers) as response,
        ):
            text = await response.text()
            if response.status != 200:
                # 401 (bad key), 422 (bad param), 429 (rate limit) all land here —
                # the key never rides in a log line.
                log.warning("tavily extract answered %s", response.status)
                return dict(UNAVAILABLE_FETCH)
        try:
            payload: Any = json.loads(text)
        except ValueError:
            log.warning("tavily extract did not return JSON")
            return dict(UNAVAILABLE_FETCH)
        # A real `/extract` 200 always carries BOTH `results` and
        # `failed_results` as lists — the submitted url lands in exactly one.
        # Either key missing or wrong-typed is the envelope not matching the
        # documented shape, an `unavailable` wiring gap (R-27), NOT "nothing on
        # the page": a change to Tavily's response shape must not silently read
        # as a dead link.
        if not isinstance(payload, dict):
            log.warning("tavily extract returned a non-object envelope")
            return dict(UNAVAILABLE_FETCH)
        results = payload.get("results")
        failed = payload.get("failed_results")
        if not isinstance(results, list) or not isinstance(failed, list):
            log.warning("tavily extract envelope missing results/failed_results")
            return dict(UNAVAILABLE_FETCH)
        if not results:
            # The url landed in `failed_results` (a dead/blocked page) or in
            # neither list — both are Tavily's own miss, not a call failure.
            return {"status": "nothing_found"}
        item = results[0]
        if not isinstance(item, dict):
            return {"status": "nothing_found"}
        raw_content = str(item.get("raw_content") or "")
        if not raw_content:
            return {"status": "nothing_found"}
        clamped, truncated = clamp_chars(raw_content, self._extract_max_chars)
        # Page-controlled → wrapped `fetched_page` with the source URL as
        # provenance, the same discipline as a search snippet (R-13). The key
        # is `text`, NOT `content` — see the bridge client's identical note:
        # Strands passes a dict with both `status` and `content` through
        # unchanged, so a bare-string `content` reaches the model outside the
        # standard text-block envelope and reads as "empty". `web_search`
        # works precisely because its dict has no `content` key.
        out: dict[str, Any] = {
            "status": "ok",
            "url": url,
            "text": wrap("fetched_page", url, clamped),
            "truncated": truncated,
        }
        if images := image_links(item.get("images")):
            out["images"] = images
        return out
