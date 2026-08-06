"""`SearchClient` — the one public entry point behind the `search` and `fetch` tools.

The fourth grounding capability, and the only one whose INBOUND content is
untrusted and whose OUTBOUND call is an SSRF surface (design/14 §2.1/§3,
design/22). Two verbs behind one client because they are one capability, gated
by one `[search]` config section and one process-level collaborator:

* `search(query)` — SearXNG `GET /search?format=json`. Titles, snippets and URLs
  only, HARD-CAPPED (result count + per-snippet byte clamp, design/14 §4) and
  every snippet wrapped `untrusted` (§the wrap below).
* `fetch(url)` — Firecrawl `POST /{version}/scrape`, `formats:["markdown"]`. The
  markdown is wrapped `untrusted` and clamped to `fetch_max_chars`.

**The SSRF control is NOT here.** design/22 §0 is explicit: Firecrawl follows
redirects and re-resolves DNS itself, so a URL that passes any pre-call check but
`302`s to `127.0.0.1:18080`, or DNS-rebinds, is fetched by Firecrawl against
Nora's own internals — Nora never sees the redirect target. The real control is
Firecrawl's network-isolated egress (a deployment change, the operator ship
gate). What `fetch` does here is DEFENCE IN DEPTH: reject a non-http(s) scheme
and any host that is not `is_global`, resolved through `getaddrinfo` so octal/
decimal/hex/IPv4-mapped encodings and DNS names all normalise before the check.
The per-tag `allowed_urls` fence — «only a URL a `search` returned this turn» —
lives one layer up in `nora.agent.tools`, because that is where per-tag state is.

**Nothing here raises.** Every transport failure lands as one of the tool states
(`ok` / `nothing_found` / `unavailable`, plus `refused` for `fetch`), never as an
exception into the agent loop where it would cost the whole reply (R-5). This is
the discipline `nora.infra.transit` and `nora.infra.routes` both hold.

The URLs, version and caps are constructor parameters — they are `[search]`
config (design/22 §4), passed in by the composition root, and a test points them
at a server that behaves like the real SearXNG/Firecrawl. `resolver` is the one
collaborator (the RouteClient/PlaceResolver precedent): a fake resolver lets a
test prove the `is_global` gate without real DNS.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from typing import Any, Protocol
from urllib.parse import urlsplit

import aiohttp

from ..transit.arrivals import _wrap

log = logging.getLogger("nora.search")


class HostResolver(Protocol):
    """Resolves a hostname to its IP address strings, or an empty set on failure.

    The one collaborator `SearchClient` takes, so the `is_global` gate can be
    exercised without touching real DNS. The production implementation
    (`_SystemResolver`) is `socket.getaddrinfo`, which is what normalises the
    numeric encodings and does the DNS lookup the gate depends on.
    """

    async def addresses(self, host: str) -> frozenset[str]: ...


class _SystemResolver:
    """`socket.getaddrinfo`, returning every resolved address or an empty set.

    `getaddrinfo` is deliberately the resolver: it normalises `2130706433`,
    `0x7f000001` and `0177.0.0.1` all to `127.0.0.1`, maps a DNS name to whatever
    it actually points at (so a rebind name resolving to loopback is caught), and
    returns IPv4-mapped IPv6 in the form `ipaddress` can unwrap. A hand-rolled
    parser would miss one of those encodings — which is exactly the v1 mistake
    design/22 §0 records.
    """

    async def addresses(self, host: str) -> frozenset[str]:
        # Off the event loop: a synchronous getaddrinfo here would block every
        # other tag's turn while a slow or hostile DNS name resolves (Kimi review
        # 2026-08-07). The fetch path is already async, so this costs nothing.
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None)
        except (OSError, UnicodeError):
            return frozenset()
        return frozenset(str(info[4][0]) for info in infos)


async def _all_global(host: str, resolver: HostResolver) -> bool:
    """True only if EVERY address `host` resolves to is `is_global`.

    An allow-check, not a denylist (design/22 §0): `ip.is_global` is False for
    private, loopback, link-local, reserved, CGNAT and unspecified ranges, so the
    three stdlib calls here cannot miss a range the way an enumerated list can.
    A host that resolves to nothing is rejected — an unresolvable name is not a
    public destination. An IPv4-mapped IPv6 address is unwrapped to the mapped
    IPv4 before the check, because `::ffff:127.0.0.1` is loopback in disguise.
    """
    addresses = await resolver.addresses(host)
    if not addresses:
        return False
    for address in addresses:
        try:
            ip: Any = ipaddress.ip_address(address.split("%")[0])
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            return False
    return True


def _clamp_bytes(text: str, max_bytes: int) -> tuple[str, bool]:
    """Clamp `text` to `max_bytes` UTF-8 bytes without splitting a codepoint.

    Returns the (possibly truncated) text and whether it was truncated — the
    truncation is STATED in the return value (design/14 §4), never silent.
    """
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    return raw[:max_bytes].decode("utf-8", "ignore"), True


def _clamp_chars(text: str, max_chars: int) -> tuple[str, bool]:
    """Clamp `text` to `max_chars` characters, stating whether it was truncated."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


class SearchClient:
    """SearXNG search + Firecrawl fetch, behind the two web tools.

    Constructed once per process and held by `nora.agent.registry.ToolRegistry`
    — it owns the two HTTP identities. Only built when `[search] enabled`; while
    off the registry holds no client and both tools answer `unavailable`.
    """

    def __init__(
        self,
        *,
        searxng_url: str,
        firecrawl_url: str,
        firecrawl_api_version: str = "v1",
        search_max_results: int = 6,
        snippet_max_bytes: int = 500,
        fetch_max_chars: int = 10000,
        timeout_seconds: float = 15.0,
        resolver: HostResolver | None = None,
    ) -> None:
        self._searxng_url = searxng_url.rstrip("/")
        self._firecrawl_url = firecrawl_url.rstrip("/")
        self._api_version = firecrawl_api_version.strip("/")
        self._search_max_results = search_max_results
        self._snippet_max_bytes = snippet_max_bytes
        self._fetch_max_chars = fetch_max_chars
        self._timeout_seconds = timeout_seconds
        self._resolver: HostResolver = resolver if resolver is not None else _SystemResolver()

    # ---- search -------------------------------------------------------------

    async def search(self, query: str) -> dict[str, Any]:
        """Titles, snippets and URLs for `query`, capped and wrapped. NEVER raises.

        Three states (R-27): `ok` with results, `nothing_found` for zero results,
        `unavailable` for any transport failure — including SearXNG's 403 when
        `format=json` is disabled, which must read as «I could not check», never
        as «nothing found» (design/22 §4: the 403-forever silent shape).
        """
        try:
            return await self._search(query)
        except Exception:
            # The never-raises backstop. `_search`'s collaborators all turn
            # failure into a value; this catches only a contract violation and
            # must degrade to `unavailable`, never propagate into the loop (R-5).
            log.exception("search failed")
            return _UNAVAILABLE_SEARCH

    async def _search(self, query: str) -> dict[str, Any]:
        params = {"q": query, "format": "json"}
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(f"{self._searxng_url}/search", params=params) as response,
        ):
            text = await response.text()
            if response.status != 200:
                # A disabled `format=json` answers 403 here — `unavailable`, and
                # the ship-time check (design/22 §4) is what confirms a live
                # search returns `ok`, not merely that the port is open.
                log.warning("search %s answered %s", self._searxng_url, response.status)
                return _UNAVAILABLE_SEARCH
        try:
            payload = json.loads(text)
        except ValueError:
            log.warning("search %s did not return JSON", self._searxng_url)
            return _UNAVAILABLE_SEARCH
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return {"status": "nothing_found", "results": []}
        wrapped: list[dict[str, Any]] = []
        for item in results[: self._search_max_results]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            if not url:
                continue
            title = str(item.get("title") or "")
            content = str(item.get("content") or "")
            body = f"{title}\n{content}".strip() if title else content
            clamped, truncated = _clamp_bytes(body, self._snippet_max_bytes)
            # Both the title and the snippet are page-controlled, so the whole
            # body is wrapped `untrusted` with the source URL as provenance
            # (design/14 §2.1). The URL itself rides PLAIN beside it: the model
            # must pass it verbatim to `fetch`, and it is the exact string the
            # per-tag `allowed_urls` fence keys on.
            wrapped.append({"url": url, "content": _wrap(url, clamped), "truncated": truncated})
        if not wrapped:
            return {"status": "nothing_found", "results": []}
        return {"status": "ok", "results": wrapped}

    # ---- fetch --------------------------------------------------------------

    async def fetch(self, url: str) -> dict[str, Any]:
        """The markdown of `url`, wrapped and clamped. NEVER raises.

        States: `ok` with content, `nothing_found` for an empty page,
        `unavailable` for a transport failure, `refused` for a non-http(s) scheme
        or a host that is not `is_global` (the defence-in-depth precheck — NOT the
        SSRF guarantee, which is Firecrawl's egress isolation, design/22 §0).

        This deliberately does NOT `scan_identifiers` the page (unlike `about`):
        a public web page is not Nora's people-data, stripping a whole page is
        impractical, and the identifier boundary for anything the model then
        tries to RELAY is `scan_reply` at `Deliverer` (design/22 §3, finding 5).
        """
        try:
            return await self._fetch(url)
        except Exception:
            log.exception("fetch failed")
            return _UNAVAILABLE_FETCH

    async def _fetch(self, url: str) -> dict[str, Any]:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return _REFUSED_FETCH
        # Defence in depth (design/22 §0). Revert this and a search result that
        # resolves to loopback/RFC1918 — numeric encoding or rebind DNS name —
        # reaches Firecrawl; the `firecrawl.calls == []` probe is exactly that.
        if not await _all_global(parsed.hostname, self._resolver):
            return _REFUSED_FETCH
        # PINNED to the deployed instance (design/22 §2): verified `v1` on the
        # running `firecrawl-api` container. `POST /v1/scrape`, `formats:
        # ["markdown"]`, envelope `{success, data:{markdown, metadata:{url,
        # sourceURL}}}` — the fake mirrors this shape, including the wrong shape
        # for a failed call.
        body = {"url": url, "formats": ["markdown"]}
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        endpoint = f"{self._firecrawl_url}/{self._api_version}/scrape"
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(endpoint, json=body) as response,
        ):
            text = await response.text()
            if response.status != 200:
                log.warning("fetch %s answered %s", endpoint, response.status)
                return _UNAVAILABLE_FETCH
        try:
            payload = json.loads(text)
        except ValueError:
            log.warning("fetch %s did not return JSON", endpoint)
            return _UNAVAILABLE_FETCH
        if not isinstance(payload, dict) or not payload.get("success"):
            # A failure envelope, or the wrong shape entirely — «I could not
            # check», never a silent empty (R-5).
            return _UNAVAILABLE_FETCH
        data = payload.get("data")
        markdown = data.get("markdown") if isinstance(data, dict) else None
        if not isinstance(markdown, str) or not markdown.strip():
            return {"status": "nothing_found", "url": url}
        clamped, truncated = _clamp_chars(markdown, self._fetch_max_chars)
        return {
            "status": "ok",
            "url": url,
            "content": _wrap(url, clamped),
            "truncated": truncated,
        }


# The three fixed refusal/unavailable payloads, so a status can never reach one
# call site with a note and another without. Notes tell the model what state it
# is in without reflecting any model-supplied string back at it.
_UNAVAILABLE_SEARCH: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't run that search just now. Say so — don't answer as if "
            "you'd looked it up.",
}
_UNAVAILABLE_FETCH: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't open that page just now. Say so — don't answer as if "
            "you'd read it.",
}
_REFUSED_FETCH: dict[str, Any] = {
    "status": "refused",
    "note": "I can't open that address. Only pages from a search this turn, and "
            "not anything pointing back at a private host.",
}
