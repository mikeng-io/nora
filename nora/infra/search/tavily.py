"""`TavilySearchProvider` — web search via Tavily, behind the one `web_search` tool.

The grounding rebuild of 2026-08-08 (DEC-73, operator): SearXNG + Firecrawl are
retired and `web_fetch` with them. Tavily is an agent-first search API that
returns ranked results WITH a relevant content snippet per result in a SINGLE
call, so «search then fetch» collapses into one verb — which is what dissolves
the runaway-rounds failure the two-verb shape produced (a weather tag spent its
whole token budget fetching pages and never answered, Langfuse 2026-08-08).

**No SSRF surface, so none of the old machinery.** The old `fetch` was an SSRF
risk because a self-hosted Firecrawl fetched a MODEL-CHOSEN URL against Nora's own
network; the `is_global`/`getaddrinfo` precheck, the post-redirect re-validation,
and the per-tag `allowed_urls` fence all existed for that. Tavily fetches from ITS
servers, never Nora's, and Nora never hands it a URL — only a query — so a
model-invented `http://127.0.0.1:18080` cannot reach anything of Nora's. The whole
apparatus (and `deploy/egress.sh`) is gone.

What survives is the discipline every grounding client holds:
* NEVER raises — a non-200, non-JSON body, or transport error is a tool STATUS
  (`unavailable`), never an exception into the loop (R-5).
* Retrieved text is `untrusted`-wrapped (R-13) — a page controls its own snippet,
  so it cannot forge the boundary — and CLAMPED, because a tool result is re-sent
  on every subsequent model call (the token lesson: bound what accumulates).
* The query is an EGRESS surface — an identifier/coordinate is screened at the
  tool layer (`nora.agent.tools`, unchanged) BEFORE it reaches here.

`POST https://api.tavily.com/search`, `Authorization: Bearer tvly-…`. `country` is
deliberately NOT sent — Hong Kong is not in Tavily's country list and sending it
422s the whole call (verified live 2026-08-08); the keyless default already
returns HK results.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from ..transit.arrivals import _wrap

log = logging.getLogger("nora.search")

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class TavilySearchProvider:
    """Tavily web search behind the `web_search` tool. Constructed once per
    process with the resolved key (DEC-8, from the environment at the composition
    root, never read here). Built only when `[search] enabled` AND a key is
    present; unwired, `web_search` answers `unavailable`."""

    def __init__(
        self,
        *,
        api_key: str,
        url: str = TAVILY_SEARCH_URL,
        max_results: int = 5,
        search_depth: str = "basic",
        content_max_chars: int = 1500,
        timeout_seconds: float = 12.0,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._max_results = max_results
        self._search_depth = search_depth
        self._content_max_chars = content_max_chars
        self._timeout_seconds = timeout_seconds

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
            return dict(_UNAVAILABLE_SEARCH)

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
                return dict(_UNAVAILABLE_SEARCH)
        try:
            payload: Any = json.loads(text)
        except ValueError:
            log.warning("tavily did not return JSON")
            return dict(_UNAVAILABLE_SEARCH)
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
            title = str(item.get("title") or "")
            content = str(item.get("content") or "")
            body_text = f"{title}\n{content}".strip() if title else content
            clamped, truncated = _clamp_chars(body_text, self._content_max_chars)
            # Title + snippet are page-controlled → the whole body is wrapped
            # `untrusted` with the source URL as provenance. The URL rides PLAIN
            # beside it so the model can cite it in the reply.
            wrapped.append({"url": url, "content": _wrap(url, clamped), "truncated": truncated})
        if not wrapped:
            return {"status": "nothing_found", "results": []}
        return {"status": "ok", "results": wrapped}


def _clamp_chars(text: str, max_chars: int) -> tuple[str, bool]:
    """Clamp `text` to `max_chars`, stating whether it was truncated."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


_UNAVAILABLE_SEARCH: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't run that search just now. Say so — don't answer as if "
            "you'd looked it up.",
}
