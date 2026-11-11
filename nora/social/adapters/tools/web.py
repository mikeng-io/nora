from __future__ import annotations

import logging
import urllib.parse
from types import SimpleNamespace
from typing import Any

from nora.agent.security import looks_like_coordinate as _looks_like_coordinate
from nora.agent.security import scan_identifiers
from nora.agent.toolkit.files import _file_handle_pointer
from nora.agent.toolkit.pages import (
    _PAGE_BLOB_PUT_GRACE_SECONDS,
    _PAGE_MATERIALIZE_CHARS,
    _serve_page,
)
from nora.agent.toolkit.results import _record, _with_note
from nora.agent.toolkit.rooms import _count_known_names
from nora.domain.external_files import (
    quoted_handle_code,
)
from nora.domain.fetched_page import FETCHED_PAGE_TTL, FetchedPage
from nora.social.adapters.tools.shared import _FETCH_NOTES, _FETCH_OFF, _SEARCH_NOTES, _SEARCH_OFF

log = logging.getLogger("nora.tools")


def _make_web_search(deps):
    content_checks = deps.content_checks
    metrics = deps.metrics
    search_source = deps.search_source
    _search_person_names = deps._search_person_names

    async def web_search(query: str) -> dict:
        """Look something up on the web. Call this when the answer is not in the
        conversation and no other tool covers it. Returns ranked links with a
        snippet each.

        Other tools come first: `transit`, `route`, `weather`, memory for a
        person you know, `place_info` for a shop's hours or rating, and
        `parking_status` or `find_parking` for car park vacancy.

        Never search a post's ID (a tweet or video id, a status number): the
        engine cannot resolve one. Read the post with `x_read`, or ask for
        the link.

        Returns `status`: ok, nothing_found or unavailable, with a `note` on how
        to report it.

        Args:
            query: What to search for, in plain words.
        """
        # The query is an EGRESS surface: it leaves the host to Tavily. An IDENTIFIER
        # must never be searched — it is unsafe at every exit — so it is blocked BEFORE
        # the call, and `nothing_found` rather than a fourth state: from where the model
        # sits «I have nothing» is the honest surface, and the string is not echoed
        # back. Revert this and a phone number reaches Tavily — the `calls == []` probe
        # is exactly that.
        #
        # `scan_identifiers`, NOT `scan_reply`: this tool filters on the floor because an
        # identifier is unsafe at every exit (see `egress.py`). A MODEL NAME in a query
        # is fine (「搵首 haiku」 must work).
        if scan_identifiers(query, content_checks=content_checks) is not None:
            _record(metrics, "web_search", "nothing_found", reason="identifier")
            return {
                "status": "nothing_found",
                "note": "I won't put something that looks like a phone number or ID "
                "into a web search. Ask in plain words.",
            }
        # A raw coordinate must not egress either: `scan_identifiers` does NOT catch a
        # bare «22.31,114.16», so a `where_is` fallback coordinate the model just
        # received could otherwise ride a search query to Tavily. The R-28 barrier
        # screens `route`; the web query is the other free-text egress and needs the
        # same screen. `nothing_found`, and the coordinate is not echoed back.
        if content_checks and _looks_like_coordinate(query):
            _record(metrics, "web_search", "nothing_found", reason="coordinate")
            return {
                "status": "nothing_found",
                "note": "I won't search a raw coordinate. Say the place by name.",
            }
        # A person's alias is COUNTED, not blocked: the shape of real queries is not
        # yet known, and a block would fail closed on legitimate use. The query
        # proceeds; only the counter moves.
        aliases = _count_known_names(query, _search_person_names)
        for _ in range(aliases):
            metrics.inc("nora_search_alias_total")
        if search_source is None:
            # No client wired — `[search]` is off (it ships inert) or the
            # services are unreachable. `unavailable`, never a silent empty (R-5).
            _record(metrics, "web_search", "unavailable")
            return dict(_SEARCH_OFF)
        try:
            out = await search_source.search(query=query)
        except Exception:
            # The provider is documented never to raise; this is the belt for
            # those braces, landing on `unavailable`.
            log.exception("web_search failed")
            out = dict(_SEARCH_OFF)
        _record(
            metrics,
            "web_search",
            out["status"],
            results=len(out.get("results") or ()),
            truncated=sum(
                1 for r in (out.get("results") or ()) if isinstance(r, dict) and r.get("truncated")
            ),
        )
        return _with_note(out, _SEARCH_NOTES)

    return web_search


def _make_web_fetch(deps):
    async def web_fetch(url: str, offset: int = 0, about: str | None = None) -> dict:
        """Open a web page and read what is on it. Call this when somebody
        shares a LINK and asks about it. Returns the first part.

        Also when a `web_search` snippet is not enough; with no link in hand,
        use `web_search`. `offset` reads further and `about` returns the parts
        that mention something.

        A fetched page is kept for the conversation: a later call on the same
        link reads the same copy, so reading on does not fetch again.

        Returns `status`: ok, nothing_found or unavailable, with a `note` on how
        to report it.

        Args:
            url: The link to open, exactly as given (must start with http:// or https://).
            offset: Read from this many characters into the page; a result's
                `next_offset` is where to continue.
            about: Text to look for on the page, so a long page does not have
                to be read end to end.
        """
        c = SimpleNamespace(
            url=url,
            offset=offset,
            about=about,
            blob_store=deps.blob_store,
            content_checks=deps.content_checks,
            conversation_id=deps.conversation_id,
            fetch_source=deps.fetch_source,
            fetched_pages=deps.fetched_pages,
            metrics=deps.metrics,
            search_source=deps.search_source,
            now_fn=deps.now_fn,
        )
        k_, _, v_ = await _web_fetch__p1(c)
        if k_:
            return v_
        k_, _, v_ = await _web_fetch__p2(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return web_fetch


async def _web_fetch__p1(c):
    c.file_code = quoted_handle_code(c.url)
    if c.file_code is not None:
        _record(c.metrics, "web_fetch", "nothing_found", reason="file_handle")
        return (True, None, _with_note(_file_handle_pointer(c.file_code), _FETCH_NOTES))
    if not c.url.startswith(("http://", "https://")):
        _record(c.metrics, "web_fetch", "nothing_found", reason="not_a_url")
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": ("That doesn't look like a link I can open. Ask them for the actual URL."),
            },
        )
    c.decoded_url = urllib.parse.unquote(c.url)
    if scan_identifiers(c.decoded_url, content_checks=c.content_checks) is not None:
        _record(c.metrics, "web_fetch", "nothing_found", reason="identifier")
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": (
                    "That link has what looks like a phone number or ID in it, so I won't open it."
                ),
            },
        )
    if c.content_checks and _looks_like_coordinate(c.decoded_url):
        _record(c.metrics, "web_fetch", "nothing_found", reason="coordinate")
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": (
                    "That link has what looks like a raw coordinate in it, so I won't open it."
                ),
            },
        )
    c.fetch_client = c.fetch_source if c.fetch_source is not None else c.search_source
    if c.fetch_client is None:
        _record(c.metrics, "web_fetch", "unavailable")
        return (True, None, dict(_FETCH_OFF))
    c.text: str | None = None
    c.provenance: dict[str, Any] = {}
    c.cached = False
    c.can_store = c.fetched_pages is not None and c.blob_store is not None
    if c.can_store:
        c.row = await c.fetched_pages.get(
            conversation_id=c.conversation_id, url=c.url, now=c.now_fn()
        )
        if c.row is not None:
            c.data = await c.blob_store.get(key=c.row.blob_key)
            if c.data is not None:
                c.text = c.data.decode("utf-8", errors="replace")
                c.provenance = c.row.provenance or {}
                c.cached = True
    if c.text is None:
        try:
            c.out = await c.fetch_client.fetch(url=c.url, max_chars=_PAGE_MATERIALIZE_CHARS)
        except Exception:
            log.exception("web_fetch failed")
            c.out = dict(_FETCH_OFF)
        if c.out.get("status") != "ok":
            _record(c.metrics, "web_fetch", c.out.get("status", "unavailable"))
            return (True, None, _with_note(dict(c.out), _FETCH_NOTES))
        c.text = str(c.out.get("text") or "")
        c.provenance = {
            "saved_copy": bool(c.out.get("saved_copy")),
            "truncated": bool(c.out.get("truncated")),
            "images": list(c.out.get("images") or []),
        }
        if c.can_store:
            c.key = await c.blob_store.put(data=c.text.encode("utf-8"))
            for _w1 in await c.fetched_pages.purge_expired(now=c.now_fn()):
                c.stale = _w1
                try:
                    await c.blob_store.delete(
                        key=c.stale, idle_seconds=_PAGE_BLOB_PUT_GRACE_SECONDS
                    )
                except Exception:
                    log.exception("web_fetch: the blob store refused a deletion")
            await c.fetched_pages.put(
                page=FetchedPage(
                    conversation_id=c.conversation_id,
                    url=c.url,
                    blob_key=c.key,
                    fetched_at=c.now_fn(),
                    expires_at=c.now_fn() + FETCHED_PAGE_TTL,
                    provenance=dict(c.provenance),
                )
            )
    return (False, None, None)


async def _web_fetch__p2(c):
    c.served = _serve_page(
        url=c.url,
        text=c.text,
        offset=c.offset,
        about=c.about,
        images=c.provenance.get("images") or [],
        truncated=bool(c.provenance.get("truncated")),
        saved_copy=bool(c.provenance.get("saved_copy")),
    )
    _record(
        c.metrics,
        "web_fetch",
        c.served["status"],
        cached=c.cached,
        about=bool(c.about),
        truncated=bool(c.provenance.get("truncated")),
        saved_copy=bool(c.provenance.get("saved_copy")),
    )
    return (True, None, _with_note(dict(c.served), _FETCH_NOTES))
    return (False, None, None)
