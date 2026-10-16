"""One HTTP round trip to Life360 — as a TYPED result, never a raised exception.

Unlike `nora.infra.routes.http`, which collapses every failure to `None`, this
helper must PRESERVE the two distinctions the collector's auth logic and canary
depend on:

- the STATUS code, because a `403` is Life360's re-auth / ban signal and must be
  told apart from every other non-200 (a 403 loop is the ban risk);
- whether the body was JSON, because an app-update challenge PAGE arrives as a
  200 (or 4xx) carrying HTML, and «the response was not JSON» is exactly the
  canary that catches a required UA/host/client-token having changed under us.

So `request` returns `HttpResult(ok, status, json, text)` and NEVER raises — a
transport failure is `ok=False, status=0`, distinct from an HTTP error which is
`ok=True, status=4xx/5xx`. The caller decides what each means; this layer only
refuses to turn any of them into an exception inside the daemon loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("nora.presence.life360")

# Life360 sits behind Cloudflare, which fingerprints the HTTP client and serves a
# bot-challenge (HTML 403) to `aiohttp` — the same recipe (host, credential, UA)
# that `aiohttp` gets 403'd on returns HTTP 200 through the stdlib `urllib`.
# So THIS one transport uses urllib rather than the aiohttp every other outbound
# helper here uses. It is a blocking call, run in the default executor so the
# collector's event loop is never stalled — the collector makes a handful of
# requests a minute, so a threadpool hop per call costs nothing.

# A bounded slice of any body is kept for logging and for the canary's
# non-JSON detection; a challenge page can be large and none of it is trusted.
_MAX_BODY = 2000


@dataclass(frozen=True)
class HttpResult:
    """The outcome of one request. `json` is the decoded body when it parsed,
    else None; `text` is the raw body (bounded) regardless, so the canary can
    see a challenge page and a log line can name what arrived."""

    ok: bool          # a response was received (transport succeeded)
    status: int       # HTTP status, or 0 when transport failed
    json: Any | None  # decoded JSON, or None when the body was not JSON
    text: str         # raw body, bounded — never trusted, only inspected

    @property
    def is_json(self) -> bool:
        return self.json is not None


async def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    data: dict[str, str] | None = None,
    timeout_seconds: float,
) -> HttpResult:
    """`method` `url` with `headers`, optional form `data`. NEVER raises.

    Returns a typed `HttpResult`. Runs the blocking `urllib` call (see the
    module docstring for why urllib, not aiohttp) in the default executor so the
    collector's event loop is never blocked.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _blocking_request, method, url, headers, data, timeout_seconds
    )


def _blocking_request(
    method: str,
    url: str,
    headers: dict[str, str],
    data: dict[str, str] | None,
    timeout_seconds: float,
) -> HttpResult:
    """One synchronous urllib round trip → `HttpResult`. NEVER raises: an HTTP
    error (incl. 4xx/5xx) is `ok=True` with the status; a transport failure is
    `ok=False, status=0` — the same two-way distinction the caller relies on."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        # Guards against a file:/custom scheme reaching urlopen (S310). The URLs
        # are module constants today, but this keeps that a fact, not a hope.
        log.warning("life360 refusing non-http(s) url: %s", url)
        return HttpResult(ok=False, status=0, json=None, text="")
    body_bytes = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body_bytes, method=method)  # noqa: S310 — scheme checked above
    for name, value in headers.items():
        req.add_header(name, value)
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310 — scheme checked above
                status = response.status
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as http_error:
            # A response WAS received, it was just a 4xx/5xx (Cloudflare's HTML
            # 403 lands here) — keep the status and body so the canary can see it.
            status = http_error.code
            raw = http_error.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        # Broad and never re-raised: this runs inside the collector loop, and a
        # transport failure must degrade to a value the loop classifies, not take
        # the daemon down. urllib raises URLError, socket.timeout, ssl.SSLError.
        log.warning("life360 %s %s unreachable: %s", method, url, exc)
        return HttpResult(ok=False, status=0, json=None, text="")
    body: Any | None
    try:
        # Parse the FULL body. `_MAX_BODY` bounds only the copy kept for the log
        # line and the canary's non-JSON detection — a real members response is
        # well over 2 KB, so parsing a truncated slice would shred it and misread
        # it as the challenge PAGE.
        body = json.loads(raw)
    except ValueError:
        body = None
    return HttpResult(ok=True, status=status, json=body, text=raw[:_MAX_BODY])
