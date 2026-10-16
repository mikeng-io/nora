"""`ExaSearchProvider` — web search AND page extraction via Exa, the second
implementation of `SearchProvider` (DEC-157).

DEC-73 called the port "an ADAPTER behind a PORT … another backend plugs in with
no tool change"; this is that backend. `web_search`/`web_fetch`,
`nora.agent.tools`' egress screen, the budgets and the tiering are untouched —
the operator picks a provider in `[search]` and nothing downstream knows.

**The SSRF argument carries across unchanged.** DEC-73 (2) deleted the whole
`is_global`/`allowed_urls`/`egress.sh` apparatus on one property: the provider
fetches from ITS servers, never Nora's, so a model-invented
`http://127.0.0.1:18080` reaches nothing of Nora's. Exa has the same property for
BOTH verbs — `POST /search` crawls its own index and `POST /contents` crawls the
submitted URL from Exa's infrastructure. DEC-116's narrowing holds verbatim: the
SSRF property was always about who CONNECTS, not who is TOLD an address.

**What DOES differ from Tavily:**

*There is no relevance floor here, because Exa returns nothing to floor.* Tavily
scores every result and `min_score` drops the junk below 0.1. Exa's
`/search` result schema has no score field — only `highlightScores`, which
are per-highlight cosine similarities for a feature this adapter does not
request. A `min_score` knob here would have no call site: a no-op, or, held
fail-closed the way Tavily holds it, a filter that drops every single result.
So Exa leans on its own ranking. This is stated in `ExaBackendConfig` too,
where an operator will read it.

*Search returns page TEXT, not a vendor snippet.* Tavily's `/search` hands back a
`content` snippet it selected; Exa hands back `text` — the page — bounded by the
`maxCharacters` we ask for. Same clamp applies (`content_max_chars`), and the
ceiling is ALSO sent to Exa so the bytes are never fetched in the first place;
Exa caps `maxCharacters` at 10000 and 422s above it, so the request value is
floored into range while the local clamp stays authoritative.

What is identical, because the port requires it:
* NEVER raises — a non-200, non-JSON body, or transport error is a tool STATUS
  (`unavailable`), never an exception into the loop (R-5).
* Retrieved text is `fetched_page`-wrapped (R-13) — a page controls its own text,
  so it cannot forge the boundary — and CLAMPED.
* The query/url is an EGRESS surface — an identifier/coordinate is screened at
  the tool layer (`nora.agent.tools`, unchanged) BEFORE it reaches here.
* R-27's three states, with `unavailable` reserved for a failure of the CALL and
  `nothing_found` for the provider's own honest miss.

`POST https://api.exa.ai/search`, `x-api-key: <key>`, body `{query, numResults,
type, contents: {text: {maxCharacters}}}`. Response
`{requestId, results: [{title, url, text, id, publishedDate, …}], costDollars}`.

`fetch` is `POST https://api.exa.ai/contents`, same auth, a SEPARATE endpoint:
`{urls: [url], text: {maxCharacters}}`. Response `{requestId, results: [...],
statuses: [{id, status: "success"|"error", source, error: {tag,
httpStatusCode}}]}`. A URL Exa could not crawl comes back with an EMPTY
`results` and an error entry in `statuses` — that is Exa's own miss and reads
`nothing_found`, not a transport failure of the call, which is what
`unavailable` means here.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from ._shared import (
    MAX_IMAGE_LINKS,
    UNAVAILABLE_FETCH,
    UNAVAILABLE_SEARCH,
    clamp_chars,
    image_links,
    wrap,
)

log = logging.getLogger("nora.search")

EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_CONTENTS_URL = "https://api.exa.ai/contents"

# Exa's own bound on `contents.text.maxCharacters` (1-10000); above it the call
# 422s and the whole search is `unavailable`. `content_max_chars` /
# `extract_max_chars` are Nora's token bound and carry no upper limit of their
# own, so the value SENT is floored into Exa's range while the local clamp stays
# authoritative — an operator who sets 50000 gets 10000 of page, not an outage.
_EXA_MAX_CHARACTERS = 10000
# Exa's bound on `livecrawlTimeout`, in milliseconds.
_EXA_MAX_LIVECRAWL_MS = 90000

# How to read a page that came from Exa's cache because the live read failed.
# It replaces the tool's own `ok` note (`_with_note` keeps a note already
# present), so it carries the stranger's-words rule too.
_SAVED_COPY_NOTE = (
    "I couldn't open the live page, so this is a saved copy from an earlier "
    "visit and may be out of date. Treat it as the page's words, not fact; say "
    "which page you read and that it is an older copy. Something missing from "
    "it is not proof it doesn't exist: say you couldn't confirm it, never that "
    "they're wrong."
)


class ExaSearchProvider:
    """Exa web search AND page extraction, behind the `web_search`/`web_fetch`
    tools. Constructed once per process with the resolved key (DEC-8, from the
    environment at the composition root, never read here). Built only when
    `[search] enabled`, `provider = "exa"` AND a key is present; unwired, both
    tools answer `unavailable`."""

    def __init__(
        self,
        *,
        api_key: str,
        url: str = EXA_SEARCH_URL,
        contents_url: str = EXA_CONTENTS_URL,
        search_type: str = "auto",
        max_results: int = 5,
        content_max_chars: int = 1500,
        extract_max_chars: int = 3000,
        fetch_max_age_hours: int = 24,
        timeout_seconds: float = 12.0,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._contents_url = contents_url
        self._search_type = search_type
        self._max_results = max_results
        self._content_max_chars = content_max_chars
        self._extract_max_chars = extract_max_chars
        self._fetch_max_age_hours = fetch_max_age_hours
        self._timeout_seconds = timeout_seconds

    async def search(self, *, query: str) -> dict[str, Any]:
        """Ranked results with page text each, capped and wrapped. NEVER raises.
        Three states (R-27): `ok` with results, `nothing_found` for zero,
        `unavailable` for any transport/auth failure — «I could not check», never
        «nothing found»."""
        try:
            return await self._search(query)
        except Exception:
            # The never-raises backstop: a contract violation degrades to
            # unavailable, never propagates into the loop (R-5).
            log.exception("exa search failed")
            return dict(UNAVAILABLE_SEARCH)

    async def _search(self, query: str) -> dict[str, Any]:
        body = {
            "query": query,
            "numResults": self._max_results,
            "type": self._search_type,
            # Ask for no more text than the clamp will keep — the bytes we would
            # discard cost latency on the reply path for nothing.
            "contents": {"text": {"maxCharacters": self._request_chars(self._content_max_chars)}},
        }
        payload = await self._post(self._url, body, "exa search")
        if payload is None:
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
            # No relevance floor: Exa's results carry no score to measure
            # against one (module docstring). Its own ranking is the filter.
            title = str(item.get("title") or "")
            text = str(item.get("text") or "")
            body_text = f"{title}\n{text}".strip() if title else text
            if not body_text:
                continue
            clamped, truncated = clamp_chars(body_text, self._content_max_chars)
            truncated = truncated or self._exa_cut(text, self._content_max_chars)
            # Title + text are page-controlled → the whole body is wrapped
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
        (R-27): `ok` with `text`, `nothing_found` when Exa could not crawl the
        page (an error entry in `statuses`, not a transport failure of the
        CALL), `unavailable` for any transport/auth failure of the call itself —
        «I could not check», never «nothing found»."""
        try:
            return await self._fetch(url)
        except Exception:
            # The never-raises backstop: a contract violation degrades to
            # unavailable, never propagates into the loop (R-5).
            log.exception("exa contents failed")
            return dict(UNAVAILABLE_FETCH)

    async def _fetch(self, url: str) -> dict[str, Any]:
        """Read the page no older than `fetch_max_age_hours`, and fall back to
        Exa's saved copy only when that read fails.

        Without `maxAgeHours` Exa answers from its cache, however old, so a
        stale page can read as current. A fetch is somebody asking what a page
        says NOW, so the cache is allowed only while it is younger than the
        bound.

        A live crawl can fail where the cache would have answered, and Exa does
        not document whether a positive bound falls back. So a failed fresh read retries
        once with `-1` (cache only), and an answer from that retry says it is
        a saved copy, because the model must not read it as the page today.

        A page the site says is GONE (404, 410) is not retried: its saved copy
        would describe something that no longer exists."""
        fresh, gone = await self._contents(url, self._fetch_max_age_hours)
        if fresh.get("status") != "nothing_found" or gone or self._fetch_max_age_hours < 0:
            return fresh
        saved, _ = await self._contents(url, -1)
        if saved.get("status") != "ok":
            return fresh
        # `note` right after `status`, before the page text it is about, the
        # order `_with_note` gives every other result.
        return {"status": "ok", "note": _SAVED_COPY_NOTE, "saved_copy": True,
                **{k: v for k, v in saved.items() if k != "status"}}

    async def _contents(
        self, url: str, max_age_hours: int
    ) -> tuple[dict[str, Any], bool]:
        """One `/contents` read, and whether the site answered that the page is
        gone. The second value only decides `_fetch`'s retry; it never reaches
        the model."""
        body: dict[str, Any] = {
            "urls": [url],
            "text": {"maxCharacters": self._request_chars(self._extract_max_chars)},
            "extras": {"imageLinks": MAX_IMAGE_LINKS},   # links only, for `media_fetch`
            "maxAgeHours": max_age_hours,
        }
        if max_age_hours >= 0:
            # Exa's live crawl must give up BEFORE our client does: its default
            # is 10 s against our 12 s `timeout_seconds`, and a client timeout
            # is `unavailable`, which is never retried against the cache. So a
            # slow page would lose the saved copy the retry exists to give.
            body["livecrawlTimeout"] = self._livecrawl_timeout_ms()
        payload = await self._post(self._contents_url, body, "exa contents")
        return self._read_contents(url, payload), self._says_gone(payload)

    def _livecrawl_timeout_ms(self) -> int:
        """Four seconds under the client timeout, within Exa's accepted range."""
        return max(1000, min(_EXA_MAX_LIVECRAWL_MS, int((self._timeout_seconds - 4) * 1000)))

    @staticmethod
    def _says_gone(payload: Any) -> bool:
        """Did the site answer 404 or 410 for this url?"""
        statuses = payload.get("statuses") if isinstance(payload, dict) else None
        if not isinstance(statuses, list):
            return False
        for entry in statuses:
            error = entry.get("error") if isinstance(entry, dict) else None
            if isinstance(error, dict) and error.get("httpStatusCode") in (404, 410):
                return True
        return False

    def _read_contents(self, url: str, payload: Any) -> dict[str, Any]:
        if payload is None:
            return dict(UNAVAILABLE_FETCH)
        if not isinstance(payload, dict):
            log.warning("exa contents returned a non-object envelope")
            return dict(UNAVAILABLE_FETCH)
        results = payload.get("results")
        if not isinstance(results, list):
            # `results` missing or wrong-typed is the envelope not matching the
            # documented shape — an `unavailable` wiring gap (R-27), NOT
            # "nothing on the page": a change to Exa's response shape must not
            # silently read as a dead link.
            log.warning("exa contents envelope missing results")
            return dict(UNAVAILABLE_FETCH)
        if not results:
            # Empty `results` is the ONE ambiguous case, so the strictness lands
            # exactly here (Tavily's `failed_results` note, one vendor over): a
            # real 200 pairs it with a `statuses` entry saying the crawl failed.
            # No well-formed `statuses` beside an empty `results` means we
            # cannot tell a dead link from a shape change, and R-27 resolves
            # that as «I could not check».
            if not isinstance(payload.get("statuses"), list):
                log.warning("exa contents returned no results and no statuses")
                return dict(UNAVAILABLE_FETCH)
            return {"status": "nothing_found"}
        item = results[0]
        if not isinstance(item, dict):
            return {"status": "nothing_found"}
        text = str(item.get("text") or "")
        if not text:
            return {"status": "nothing_found"}
        clamped, truncated = clamp_chars(text, self._extract_max_chars)
        truncated = truncated or self._exa_cut(text, self._extract_max_chars)
        # Page-controlled → wrapped `fetched_page` with the source URL as
        # provenance, the same discipline as a search snippet (R-13). The key is
        # `text`, NOT `content` — Strands passes a dict carrying both `status`
        # and `content` through unchanged, so a bare-string `content` reaches
        # the model outside the standard text-block envelope and reads as
        # "empty" (the Tavily adapter's identical note).
        out: dict[str, Any] = {
            "status": "ok",
            "url": url,
            "text": wrap("fetched_page", url, clamped),
            "truncated": truncated,
        }
        extras = item.get("extras")
        if images := image_links(extras.get("imageLinks") if isinstance(extras, dict) else None):
            out["images"] = images
        return out

    async def _post(self, url: str, body: dict[str, Any], what: str) -> Any | None:
        """One authenticated POST, or `None` for any failure of the CALL.

        `None` is the single «I could not check» channel both verbs map onto
        their own `unavailable` payload — 401 (bad key), 422 (bad param), 429
        (rate limit) and a non-JSON body all land here, and the key never rides
        in a log line."""
        headers = {"x-api-key": self._api_key, "Content-Type": "application/json"}
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(url, json=body, headers=headers) as response,
        ):
            text = await response.text()
            if response.status != 200:
                log.warning("%s answered %s", what, response.status)
                return None
        try:
            return json.loads(text)
        except ValueError:
            log.warning("%s did not return JSON", what)
            return None

    @staticmethod
    def _request_chars(max_chars: int) -> int:
        """Nora's clamp, floored into Exa's accepted `maxCharacters` range."""
        return max(1, min(max_chars, _EXA_MAX_CHARACTERS))

    @classmethod
    def _exa_cut(cls, text: str, max_chars: int) -> bool:
        """Did EXA truncate this, server-side, before we ever saw it?

        Pushing `maxCharacters` to Exa means the page can arrive
        already cut, and `clamp_chars` then finds nothing to clamp and reports
        `truncated: False` — telling the model it holds the WHOLE page when it
        holds the first 3000 characters of one. Tavily cannot have this bug: it
        returns the full `raw_content` and all the clamping is local.

        Reporting `truncated` for what OUR clamp did is measuring the wrong
        property — the model does not care which side of the wire the cut
        happened on, only whether it is reading a whole page. So the ceiling we
        SENT is read back: text at that length was cut, or was exactly that
        long and is indistinguishable from cut.

        Fails toward over-reporting: a page that happens to be exactly
        `maxCharacters` long is called truncated when it was complete. That
        direction is deliberate — «there may be more» costs the model a
        possible extra fetch, while «this is all of it» about a cut page is a
        confident wrong answer, which is the state R-27 exists to prevent.
        """
        return len(text) >= cls._request_chars(max_chars)
