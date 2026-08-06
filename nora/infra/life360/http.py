"""One HTTP round trip to Life360 — as a TYPED result, never a raised exception.

Unlike `nora.infra.routes.http`, which collapses every failure to `None`, this
helper must PRESERVE the two distinctions the collector's auth logic and canary
depend on (design/19 §4):

- the STATUS code, because a `403` is Life360's re-auth / ban signal and must be
  told apart from every other non-200 (the study named the 403-loop as the ban
  risk);
- whether the body was JSON, because an app-update challenge PAGE arrives as a
  200 (or 4xx) carrying HTML, and «the response was not JSON» is exactly the
  canary that catches a required UA/host/client-token having changed under us.

So `request` returns `HttpResult(ok, status, json, text)` and NEVER raises — a
transport failure is `ok=False, status=0`, distinct from an HTTP error which is
`ok=True, status=4xx/5xx`. The caller decides what each means; this layer only
refuses to turn any of them into an exception inside the daemon loop.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger("nora.presence.life360")

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

    Returns a typed `HttpResult`. A session per call, like every other outbound
    helper in this codebase (`nora.infra.routes.http`, the transit catalogue):
    the collector makes a handful of requests per minute to one host, and a
    pooled session would be state to own for no measured gain.
    """
    try:
        timeout_config = aiohttp.ClientTimeout(total=timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout_config) as session,
            session.request(method, url, headers=headers, data=data) as response,
        ):
            text = (await response.text())[:_MAX_BODY]
            body: Any | None
            try:
                body = json.loads(text)
            except ValueError:
                body = None
            return HttpResult(ok=True, status=response.status, json=body, text=text)
    except Exception as exc:  # noqa: BLE001
        # Broad and never re-raised, for the reason every outbound helper here
        # gives: this runs inside the collector loop, and a transport exception
        # must degrade to a value the loop can classify, not take the daemon
        # down. aiohttp alone raises ClientError, TimeoutError, SSLError and
        # UnicodeDecodeError from here.
        log.warning("life360 %s %s unreachable: %s", method, url, exc)
        return HttpResult(ok=False, status=0, json=None, text="")
