"""`OpenGraphPreviewSource` — the sender-side fetch behind a Signal link preview.

Signal link previews are SENDER-generated: the sending client fetches the
target's OpenGraph tags and attaches title/description/image to the message, and
the recipients' clients render what they are given without touching the link.
That is a privacy win for the room and an exposure for Nora, because it makes
her the one who fetches — over her own network, at a URL that ultimately came
out of a model.

**NEVER load-bearing.** Every failure — a bad scheme, a private address, a
timeout, a 404, a non-HTML body, a page with no title, an oversized document —
is `None`, and `None` means the message goes out without a card. A reply that
failed over a preview would be R-5 with extra steps.

THE SSRF GUARD, AND WHY THIS MODULE NEEDS ONE WHEN `web_search`/`web_fetch` DO NOT
-----------------------------------------------------------------------------------
DEC-73 deleted the old `web_fetch` and, with it, "the `is_global`/`getaddrinfo`
precheck, the post-redirect `sourceURL` re-validation, the per-tag
`allowed_urls` fence". The argument was precise and is worth re-reading before
assuming this module reopens the hole: Tavily fetches from ITS OWN servers,
never Nora's, so a model-invented `http://127.0.0.1:18080` reaches nothing of
Nora's — true for `web_search`'s query AND, since DEC-116, for `web_fetch`'s URL
too (`SearchProvider.fetch` hands Tavily a url, but Tavily is still the one
connecting). **That argument does not extend here.** This module is handed a
URL and fetches it ITSELF, over Nora's own network, so the exposure DEC-73
removed is exactly the one this feature reintroduces — and the guard comes back
with it, in the narrowest form that does the job:

1. `http`/`https` only. `file:`, `data:`, `gopher:` and friends are not
   previewable and are not fetched.
2. **Every address the host resolves to must be globally routable.** All of
   them, not the first — a name with one public and one loopback record would
   otherwise pass the check and connect to either. This is what keeps a URL
   pointing at `signal-cli:18080`, `db:5432`, `169.254.169.254` or
   `127.0.0.1:9100` (Nora's own metrics endpoint) from being read out into a
   group chat as a preview card.
3. **Redirects are followed BY HAND, at most twice, re-validating each hop.**
   `allow_redirects=True` would validate the first URL and connect to whatever
   the last one named — the "post-redirect re-validation" DEC-73's list names.
   Two hops is enough for a redirecting link (the case that motivated the
   feature) and short enough that a redirect chain cannot become a fetch loop.
4. A byte ceiling on the read, so a hostile or merely enormous document costs a
   bounded amount of memory and time; OpenGraph tags live in `<head>`.
5. One total timeout across the whole thing, small (seconds), because this sits
   on the reply path with a human waiting.

**The residual risk, stated rather than papered over:** the address check and
the connection are two separate resolutions, so a DNS record that changes
between them (rebinding) is not covered. Closing that means resolving once and
connecting to the literal IP with a `Host:` header, which breaks TLS
certificate validation for HTTPS and is a materially bigger piece of machinery
than the feature is worth. What bounds it is that the OUTPUT is a title and a
description, both identifier-screened at the composer (`nora.agent.outbound`)
before they can reach the room, and the fetch is unauthenticated — Nora sends
no cookie, no bearer token and no header that would make an internal endpoint
answer differently for her than for anyone else.
"""

from __future__ import annotations

import asyncio
import base64
import html
import ipaddress
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import aiohttp

from nora.domain.ports import LinkPreview

log = logging.getLogger("nora.preview")

__all__ = ["OpenGraphPreviewSource"]

# Clamps on what may ride to the group. Signal's own clients truncate a long
# card anyway; the reason to clamp HERE is that these strings are attacker-
# influenced (whoever controls the page controls them), and an unbounded one
# would be an unbounded string in every subsequent audit row and log line.
MAX_TITLE_CHARS = 200
MAX_DESCRIPTION_CHARS = 400

# How much of the document to read. `<head>` is at the top by construction, so
# this is generous rather than tight; the point is that it is FINITE.
DEFAULT_MAX_BYTES = 262_144

# An image big enough to look right in a card and small enough that base64ing
# it into a JSON-RPC body is not a problem. Signal re-encodes it anyway.
DEFAULT_IMAGE_MAX_BYTES = 262_144

# Two hops covers a link that redirects once to the real page. A third
# is a chain, and a chain is not something a preview should be chasing on the
# reply path.
MAX_REDIRECTS = 2

_HTML_TYPES = ("text/html", "application/xhtml+xml")


class _OpenGraph(HTMLParser):
    """`<head>` scraped for the three fields a card needs, and nothing else.

    An HTML PARSER rather than a regex over the document, because the thing
    being parsed is hostile-by-default markup and `<meta ...>` inside a comment,
    a `<script>` string or an attribute value is exactly the case a regex gets
    wrong. `html.parser` is standard library, so this costs no dependency.

    It stops collecting at `</head>` — `_done` is checked by every handler, not
    merely set — which is both a bound on the work and the correct semantics:
    an `og:` tag in the BODY is not one a browser or a Signal client would
    honour either, so reading one would let a page hide the title it gives Nora
    somewhere no other consumer looks.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title: str = ""
        self._in_title = False
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        if tag == "title":
            self._in_title = True
            return
        if tag != "meta":
            return
        values = {k.lower(): (v or "") for k, v in attrs}
        # `property` is OpenGraph's attribute, `name` is Twitter's and the
        # bare-HTML `description`'s. Both are read: a page that carries only
        # `<meta name="description">` still yields a usable card, and refusing
        # it would make the feature look broken on ordinary pages.
        key = (values.get("property") or values.get("name") or "").strip().lower()
        content = values.get("content", "").strip()
        if key and content and key not in self.meta:
            self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self._done = True

    def handle_data(self, data: str) -> None:
        if self._done or not self._in_title:
            return
        # Clamped while accumulating, not only at the end: `_clean` would trim
        # a 4 MB title down to 200 characters perfectly well, having first held
        # all 4 MB in memory on the reply path.
        if len(self.title) < MAX_TITLE_CHARS * 2:
            self.title += data


def _clean(value: str, limit: int) -> str:
    """One meta value as it may appear in a card: unescaped, unwrapped, clamped."""
    text = html.unescape(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit].strip()


def card_from_html(url: str, body: str, *, image_src_out: list[str]) -> LinkPreview | None:
    """The card `body` describes, or `None` if it describes no usable one.

    PUBLIC and PURE, and both of those are about testability rather than reuse.
    Everything this function decides — which of `og:`/`twitter:`/`<title>`
    wins, that a titleless page yields nothing at all, that entities are
    unescaped exactly once, that the strings are clamped — is decided from
    BYTES, with no socket involved. Left inside `_build` it would have been
    reachable only through a fetch, and a fetch in this module is reachable
    only through the SSRF guard, which by construction refuses the loopback
    server a test would have to stand up (`tests/test_preview_source.py`
    states that consequence in full). A pure seam is how those decisions get
    asserted at all.

    `image_src_out` is an out-parameter rather than a second return value
    because the caller needs the raw `og:image` string ONLY to go and fetch it,
    which this function must not do — keeping it out of the return type stops
    a caller from mistaking a src for a `LinkPreview` field it can send.
    """
    parser = _OpenGraph()
    parser.feed(body)
    meta = parser.meta
    title = _clean(
        meta.get("og:title") or meta.get("twitter:title") or parser.title,
        MAX_TITLE_CHARS,
    )
    if not title:
        # `--preview-title` is mandatory (signal-cli(1)). A card with no title
        # is not a degraded card, it is an invalid one — so a page that offers
        # nothing usable yields no preview rather than a blank chip in the group.
        log.info("no usable preview title for %s", url)
        return None
    src = meta.get("og:image") or ""
    if src:
        image_src_out.append(src)
    return LinkPreview(
        url=url,
        title=title,
        description=_clean(
            meta.get("og:description") or meta.get("twitter:description")
            or meta.get("description") or "",
            MAX_DESCRIPTION_CHARS,
        ),
    )


def next_hop(status: int, location: str | None, current: str) -> str | None:
    """The absolute URL a redirect response points at, or `None` if it is not one.

    Separated out — and public — for the same reason `card_from_html` is: the
    redirect rule is security-relevant (each hop is re-validated, which is only
    meaningful if the hop is resolved correctly) and it is decidable from a
    status code and a header, with no server. A `Location` that is relative is
    joined against the URL it came from, which is what makes
    `302 -> /real-page` land on the same host rather than on nothing.
    """
    if status not in (301, 302, 303, 307, 308) or not location:
        return None
    return urljoin(current, location)


async def _is_global_host(host: str, port: int) -> bool:
    """True iff EVERY address `host` resolves to is globally routable.

    Every, not any: a name answering with both a public record and `127.0.0.1`
    would pass an any-check and then connect to whichever the resolver handed
    aiohttp. A literal IP takes the same path — `getaddrinfo` accepts one and
    returns it — so there is no second code path for the `http://10.0.0.1/`
    case to slip through.
    """
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=1,     # SOCK_STREAM; one entry per address, not three
        )
    except OSError as exc:
        log.info("preview host %r does not resolve (%s)", host, type(exc).__name__)
        return False
    if not infos:
        return False
    for info in infos:
        sockaddr = info[4]
        try:
            address = ipaddress.ip_address(str(sockaddr[0]))
        except ValueError:
            return False
        if not address.is_global:
            log.warning("refusing a preview fetch: %r resolves to %s", host, address)
            return False
    return True


class OpenGraphPreviewSource:
    """Satisfies `nora.domain.ports.LinkPreviewSource`. One per process.

    Constructed by the composition root when `[preview].enabled` is true and
    held by the pipeline, which hands it to each turn's `OutboundComposer` —
    the same lifetime as the pipeline. It holds no per-turn state, so one
    instance is safe across concurrent turns.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 3.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        fetch_image: bool = True,
        image_max_bytes: int = DEFAULT_IMAGE_MAX_BYTES,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        #: The WHOLE-card budget, enforced in `fetch` — see the note there
        #: for why the session timeout alone is not that bound.
        self._total_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._fetch_image = fetch_image
        self._image_max_bytes = image_max_bytes

    async def preview_for(self, *, url: str) -> LinkPreview | None:
        """The card for `url`, or `None`. Never raises — see module docstring.

        `LinkPreview.url` is the url as PASSED IN, never the post-redirect one,
        and that is a wire requirement rather than a preference: signal-cli says
        "The same url must also appear in the message body, otherwise the
        preview won't be displayed by the apps", and the body carries what she
        wrote. Following a redirect to its target and reporting the target
        would produce a message carrying a preview payload that no client shows.
        """
        try:
            # ONE deadline for the WHOLE card, not one per request.
            # `aiohttp.ClientTimeout(total=…)` on a SESSION applies per
            # REQUEST, so a card costing four hops — page, redirect, image,
            # its redirect — would spend up to four times `timeout_seconds` on
            # the reply path, on a turn a human is watching (DEC-89).
            # `asyncio.timeout` bounds the whole coroutine. A card that cannot
            # be built inside the budget is simply absent.
            async with asyncio.timeout(self._total_seconds):
                return await self._build(url)
        except asyncio.CancelledError:
            raise                          # shutdown is not a preview failure
        except (TimeoutError, aiohttp.ClientError) as exc:
            log.info("preview unavailable for %s (%s)", url, type(exc).__name__)
            return None
        except Exception:
            log.exception("preview failed for %s", url)
            return None

    async def _build(self, url: str) -> LinkPreview | None:
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            fetched = await self._get(session, url, html_only=True)
            if fetched is None:
                return None
            final_url, body = fetched
            image_src: list[str] = []
            card = card_from_html(url, body, image_src_out=image_src)
            if card is None:
                return None
            image = await self._image(
                session, image_src[0] if image_src else None, base=final_url,
            )
        return LinkPreview(
            url=card.url, title=card.title, description=card.description,
            image_data_uri=image,
        )

    async def _image(
        self, session: aiohttp.ClientSession, src: str | None, *, base: str,
    ) -> str | None:
        """`og:image` as an RFC 2397 data URI, or None.

        A DATA URI and not a path, because signal-cli runs in a different
        container from Nora with no shared volume — see
        `nora.domain.ports.LinkPreview`, which carries the jar reading that
        proves signal-cli accepts one here.

        The image is the most droppable part of the most droppable feature, so
        every branch is `None`: no `og:image`, a relative src that will not
        join, a non-global host, a non-image content type, an oversized body.
        The card still goes out with its title and description.
        """
        if not self._fetch_image or not src:
            return None
        absolute = urljoin(base, html.unescape(src.strip()))
        fetched = await self._get_bytes(session, absolute)
        if fetched is None:
            return None
        content_type, data = fetched
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    async def _get(
        self, session: aiohttp.ClientSession, url: str, *, html_only: bool,
    ) -> tuple[str, str] | None:
        """Fetch `url` as text, following at most `MAX_REDIRECTS` validated hops."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not await _safe(current):
                return None
            async with session.get(
                current, allow_redirects=False, headers=_HEADERS,
            ) as response:
                hop = next_hop(response.status, response.headers.get("Location"), current)
                if hop is not None:
                    # Back round the loop, which re-runs `_safe` on the NEW
                    # url — the post-redirect re-validation DEC-73's deleted
                    # apparatus is named for. `allow_redirects=True` would have
                    # checked the first url and connected to the last.
                    current = hop
                    continue
                if response.status in (301, 302, 303, 307, 308):
                    return None          # a redirect with no Location goes nowhere
                if response.status != 200:
                    log.info("preview fetch got HTTP %s for %s", response.status, current)
                    return None
                content_type = (response.headers.get("Content-Type") or "").lower()
                if html_only and not any(t in content_type for t in _HTML_TYPES):
                    log.info("preview target is not html (%s)", content_type or "unset")
                    return None
                raw = await response.content.read(self._max_bytes)
            # `errors="replace"` rather than a charset negotiation: this is
            # scraping two meta tags out of a head, and a page whose declared
            # charset is wrong (or absent, which is most of them) must yield a
            # slightly mangled title rather than an exception on the reply path.
            return current, raw.decode("utf-8", errors="replace")
        log.info("preview redirect chain too long for %s", url)
        return None

    async def _get_bytes(
        self, session: aiohttp.ClientSession, url: str,
    ) -> tuple[str, bytes] | None:
        if not await _safe(url):
            return None
        async with session.get(url, allow_redirects=False, headers=_HEADERS) as response:
            if response.status != 200:
                return None
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
            if not content_type.lower().startswith("image/"):
                return None
            data = await _read_limited(response.content, self._image_max_bytes)
        if len(data) > self._image_max_bytes:
            log.info("preview image over %d bytes, dropping it", self._image_max_bytes)
            return None
        if not data:
            # An EMPTY 200 body (the bound above is only an upper one). Zero
            # bytes base64-encode to the TRUTHY string "data:image/png;base64,",
            # which reaches the wire and which signal-cli's `DataURI` pattern
            # refuses — it requires `(?<data>.+)` — throwing
            # `IllegalArgumentException` past a catch that only handles
            # `IOException`. That fails the WHOLE send, not the card. Any CDN
            # answering 200 with no body triggers it. The undecorated retry in
            # `nora.platform.signal.send` rescues the message, but costs a
            # wasted round trip, so stop it here.
            log.info("preview image body was empty, dropping it")
            return None
        return content_type, data


async def _read_limited(stream: aiohttp.StreamReader, limit: int) -> bytes:
    """Read a response body up to `limit` bytes, WITHOUT truncating early.

    `StreamReader.read(n)` returns as soon as the FIRST buffered chunk
    arrives — it does not wait for `n` bytes or EOF. A CDN that streams in
    small packets would hand back a truncated body that the `len(data) >
    limit` check below then misses. Loop until EOF or the ceiling so the
    size check is a measurement, not a first-packet guess.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            return b"".join(chunks) + chunk
        chunks.append(chunk)
    return b"".join(chunks)


# No cookies, no auth, no referer — see the module docstring's residual-risk
# note: an unauthenticated fetch is what keeps a rebinding window from being
# able to read anything Nora's credentials would have unlocked. The UA is
# honest rather than a browser impersonation; a site that refuses it simply
# yields no card.
_HEADERS = {
    "User-Agent": "nora-link-preview/1.0",
    "Accept": "text/html,application/xhtml+xml",
}


async def _safe(url: str) -> bool:
    """Scheme + destination check for ONE hop. See the module docstring."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        log.warning("refusing a preview fetch with scheme %r", parts.scheme)
        return False
    host = parts.hostname
    if not host:
        return False
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return await _is_global_host(host, port)
