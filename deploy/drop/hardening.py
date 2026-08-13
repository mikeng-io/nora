"""ASGI middleware for `drop`'s PUBLIC listener only (§6 of
`docs/superpowers/specs/2026-08-12-nora-drop-design.md`; the internal
listener, §5a, already has no path to the internet, so nothing here applies
to it — see `server.py`'s module docstring for the split). Two jobs, one
middleware, both stated as an explicit operator requirement ("make sure
[drop] is NOT index[ed] by any search engine", "need to reject EVERY bot"):

1. Reject a request whose User-Agent matches `bots.is_bot_user_agent` with a
   403, BEFORE it reaches any route handler — no redirect lookup, no
   artifact fetch, no store I/O of any kind for a rejected request. `/robots.txt`
   is exempt: a bot has to be ABLE to fetch that file to have any chance of
   respecting the `Disallow: /` it contains (task requirement 1's own
   framing) — exempting every OTHER path would defeat requirement 3 instead.
2. Stamp `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet` on every
   response this listener sends, including the 403 from (1) and Starlette's
   own no-route-matched 404 — the actual instruction to a COMPLIANT search
   engine that already found this URL some other way (a forwarded link, a
   referrer) not to index or archive it, independent of whether it ever
   checks robots.txt at all.

Applied as middleware (`Starlette(..., middleware=[Middleware(
PublicListenerHardening)])`) rather than hand-added per route, on purpose —
the task's own reasoning: a per-handler header is exactly the kind of thing
that gets missed the next time a route is added.

Written as a plain ASGI middleware (`__init__(self, app)` /
`__call__(self, scope, receive, send)`), not `starlette.middleware.base.
BaseHTTPMiddleware`. `BaseHTTPMiddleware` builds a full `Request`/response
round trip through `call_next` for every request; the header-stamping half
of this only needs to see the outgoing ASGI messages, and the reject half
only needs to see the incoming `scope` before the wrapped app runs at all —
both are the direct fit for a thin `send`-wrapping ASGI middleware, which is
also the shape Starlette's own docs recommend for header injection.
"""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bots import is_bot_user_agent

# What actually tells a compliant search engine not to index/archive a page
# it already found (task requirement 2) -- unlike robots.txt (requirement 1),
# a crawler doesn't need to check this before deciding whether to fetch; it's
# read off the response itself once fetched.
X_ROBOTS_TAG_VALUE = "noindex, nofollow, noarchive, nosnippet"

# robots.txt must stay reachable to a bot even though every OTHER path on
# this listener rejects known bot UAs -- see module docstring point 1.
_ROBOTS_TXT_PATH = "/robots.txt"

_REJECTION_BODY = (
    "403 Forbidden: this listener does not serve automated clients. "
    "See /robots.txt."
)


class PublicListenerHardening:
    """Wraps `server.public_app` only. See module docstring."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_robots_tag(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("X-Robots-Tag", X_ROBOTS_TAG_VALUE)
            await send(message)

        if scope["path"] != _ROBOTS_TXT_PATH:
            user_agent = Headers(scope=scope).get("user-agent")
            if is_bot_user_agent(user_agent):
                response = PlainTextResponse(_REJECTION_BODY, status_code=403)
                await response(scope, receive, send_with_robots_tag)
                return

        await self._app(scope, receive, send_with_robots_tag)


__all__ = ["X_ROBOTS_TAG_VALUE", "PublicListenerHardening"]
