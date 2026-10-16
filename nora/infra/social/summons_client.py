"""`InstagramSummonsClient` — the public summons' own client on the
instagram-bridge: the post and comments reads
the summons needs, and the ONE public write, a reply under a summons comment.

It has NO mentions read: the notification feed never carries a comment
mention of her. A summons arrives as an SSE `summons` event (`nora.platform.instagram.summons`).

**Not a method on `InstagramSocialClient`, on purpose.** That client is the
model's read side and R-86's test pins it to GET-only; the Threads design
keeps the read port and the posting port apart,
because a posting capability that rides on a read client is one refactor away
from being reachable by a read tool. This client is held by the summons
occasion alone and by nothing a model can call.

**Every failure is read by the envelope's `code`, never by the status alone.**
The bridge answers 409 for two different things (`needs_human`, the session is
checkpointed, and `already_replied`, this summons had its one attempt), 403
for three and 429 for two (`deploy/instagram-bridge/internal/transport/http/
errors.go`'s refusal table). A status-only reading would retry a checkpoint or
drop a duplicate on the floor. When there is no envelope, the code says so:
`timeout`, `unreachable`, `bad_body` or `http_<status>`.

NEVER raises. Every answer is a `BridgeResult`: a value with `code == "ok"`,
or no value and a code saying why.

**Nothing identifying is logged**: not a shortcode, a comment id, an igid, a
username, a caption or a comment's text. Log lines carry the route's shape,
the status and the code.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar
from urllib.parse import urlsplit

import aiohttp

from ...domain.instagram_links import SHORTCODE_RE, is_handle
from ...domain.summons import Comment, PostItem, SummonsPost
from ..trace_propagation import propagation_headers

log = logging.getLogger("nora.infra.social.summons")

T = TypeVar("T")

#: A decimal Instagram id (a comment, a media pk, an igid), the bridge's own
#: `decimalPattern`. Anything else is dropped at the edge, so no id-shaped
#: string from the wire can address a different route.
_DECIMAL_RE = re.compile(r"[0-9]{1,25}")

#: The hosts an item is served from (`instagram_client._CDN_HOST_SUFFIXES`): a url
#: is handed to the model and to `media_fetch`, so an item on any other host is dropped
#: rather than rendered.
_CDN_HOST_SUFFIXES: tuple[str, ...] = (".fbcdn.net", ".cdninstagram.com")

_KINDS = frozenset({"image", "video"})
_MAX_ITEMS = 20
_MAX_TEXT = 2200


@dataclass(frozen=True)
class BridgeResult(Generic[T]):
    """One bridge answer. `code == "ok"` exactly when `value` is set."""

    value: T | None
    code: str
    status: int | None = None

    @property
    def ok(self) -> bool:
        return self.code == "ok"


class InstagramSummonsClient:
    """The instagram-bridge's summons routes. NEVER raises."""

    def __init__(self, *, base_url: str, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    # ---- reads -------------------------------------------------------------

    async def post(self, shortcode: str) -> BridgeResult[SummonsPost]:
        """`GET /posts/{shortcode}` → the post, items on an allowed CDN host
        only. A post with no usable item is `bad_body`: she does not answer
        about a post she cannot see."""
        if not SHORTCODE_RE.fullmatch(shortcode):
            return BridgeResult(None, "invalid_id")
        answer = await self._call("GET", f"/posts/{shortcode}")
        if not answer.ok:
            return BridgeResult(None, answer.code, answer.status)
        post = _post(answer.value)
        if post is None:
            return BridgeResult(None, "bad_body", answer.status)
        return BridgeResult(post, "ok", answer.status)

    async def comments(
        self, shortcode: str, *, reply_to: str | None = None,
    ) -> BridgeResult[tuple[Comment, ...]]:
        """`GET /posts/{shortcode}/comments[?reply_to=]` → one page of
        comments (the bridge offers no cursor), each read field by field."""
        if not SHORTCODE_RE.fullmatch(shortcode):
            return BridgeResult(None, "invalid_id")
        path = f"/posts/{shortcode}/comments"
        if reply_to is not None:
            if not _DECIMAL_RE.fullmatch(reply_to):
                return BridgeResult(None, "invalid_id")
            path += f"?reply_to={reply_to}"
        answer = await self._call("GET", path)
        if not answer.ok:
            return BridgeResult(None, answer.code, answer.status)
        body = answer.value
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            return BridgeResult(None, "bad_body", answer.status)
        out = [c for c in (_comment(row) for row in items) if c is not None]
        return BridgeResult(tuple(out), "ok", answer.status)

    # ---- the one write -----------------------------------------------------

    async def post_comment(
        self, shortcode: str, *, text: str, reply_to_comment_id: str,
    ) -> BridgeResult[str | None]:
        """`POST /posts/{shortcode}/comments` → 201 and the new comment's id
        (`value`, or `None` if the bridge answered 201 without a readable one:
        it is still published), or the refusal's `code`.

        No retry here, ever: the bridge claims the reply before its upstream
        call, and a second POST is either `already_replied` or, worse, a
        second public comment."""
        if not SHORTCODE_RE.fullmatch(shortcode) or not _DECIMAL_RE.fullmatch(
            reply_to_comment_id,
        ):
            return BridgeResult(None, "invalid_id")
        answer = await self._call(
            "POST", f"/posts/{shortcode}/comments",
            payload={"text": text, "reply_to_comment_id": reply_to_comment_id},
            success=201,
        )
        if not answer.ok:
            return BridgeResult(None, answer.code, answer.status)
        body = answer.value
        new_id = body.get("id") if isinstance(body, dict) else None
        return BridgeResult(
            new_id if isinstance(new_id, str) and _DECIMAL_RE.fullmatch(new_id) else None,
            "ok", answer.status,
        )

    # ---- the wire ----------------------------------------------------------

    async def _call(
        self, method: str, path: str, *, payload: dict[str, Any] | None = None,
        success: int = 200,
    ) -> BridgeResult[Any]:
        """One request → the parsed body on `success`, or a code. The route's
        SHAPE is logged, never its path: the path carries a shortcode."""
        route = _route_shape(method, path)
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.request(
                    method, f"{self._base_url}{path}", json=payload,
                    headers=propagation_headers(),
                ) as response,
            ):
                status = response.status
                text = await response.text()
        except TimeoutError:
            log.warning("instagram bridge timed out on %s", route)
            return BridgeResult(None, "timeout")
        except Exception:  # noqa: BLE001 — never raises; the code says why
            log.warning("instagram bridge unreachable on %s", route)
            return BridgeResult(None, "unreachable")
        try:
            body: Any = json.loads(text)
        except ValueError:
            body = None
        if status == success:
            if body is None:
                log.warning("instagram bridge answered %s on %s without JSON", status, route)
                return BridgeResult(None, "bad_body", status)
            return BridgeResult(body, "ok", status)
        code = _envelope_code(body) or f"http_{status}"
        log.warning("instagram bridge answered %s %s on %s", status, code, route)
        return BridgeResult(None, code, status)


def _route_shape(method: str, path: str) -> str:
    """`GET /posts/{shortcode}/comments` rather than the path itself."""
    bare = path.split("?", 1)[0]
    if bare.startswith("/posts/"):
        tail = bare.split("/", 3)
        bare = "/posts/{shortcode}" + ("/" + tail[3] if len(tail) > 3 else "")
    return f"{method} {bare}"


def _envelope_code(body: Any) -> str | None:
    """The one failure envelope's `code` (errors.go), or `None`."""
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code else None


def _at(value: Any) -> datetime | None:
    """An RFC 3339 time, or `None`: omitted means unknown, never an epoch."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _obj(value: Any) -> dict[str, Any]:
    """A JSON object, or `{}` for anything else: a nested field that is not an
    object reads as empty, never as a crash."""
    return value if isinstance(value, dict) else {}


def _decimal(value: Any) -> str | None:
    return value if isinstance(value, str) and _DECIMAL_RE.fullmatch(value) else None


def _handle(value: Any) -> str | None:
    return value if isinstance(value, str) and is_handle(value) else None


def _is_cdn_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("https://"):
        return False
    host = (urlsplit(value).hostname or "").lower()
    return any(host.endswith(suffix) for suffix in _CDN_HOST_SUFFIXES)


def _post(body: Any) -> SummonsPost | None:
    if not isinstance(body, dict):
        return None
    raw = body.get("items")
    items: list[PostItem] = []
    more = False
    for row in raw if isinstance(raw, list) else ():
        if not isinstance(row, dict) or not _is_cdn_url(row.get("url")):
            continue
        if len(items) >= _MAX_ITEMS:
            more = True
            break
        kind = row.get("kind")
        duration = row.get("duration_seconds")
        items.append(PostItem(
            kind=kind if kind in _KINDS else "media",
            url=row["url"],
            duration_seconds=float(duration) if (
                not isinstance(duration, bool) and isinstance(duration, (int, float))
                and duration >= 0
            ) else None,
        ))
    if not items:
        return None
    caption = body.get("caption")
    taken_at = body.get("taken_at")
    return SummonsPost(
        author=_handle(body.get("author")),
        caption=" ".join(caption.split()) if isinstance(caption, str) and caption.strip() else None,
        taken_at=taken_at if isinstance(taken_at, int) and not isinstance(taken_at, bool)
        and taken_at > 0 else None,
        items=tuple(items), more=more,
    )


def _comment(row: Any) -> Comment | None:
    if not isinstance(row, dict):
        return None
    comment_id = _decimal(row.get("id"))
    text = row.get("text")
    if comment_id is None or not isinstance(text, str):
        return None
    author = _obj(row.get("author"))
    return Comment(
        id=comment_id, parent_id=_decimal(row.get("parent_id")),
        author=_handle(author.get("username")),
        text=text[:_MAX_TEXT], at=_at(row.get("at")),
        # Kept to find the summoner's own comment and nothing else; an igid
        # that is not decimal is "" and so matches nobody (fail closed).
        author_igid=_decimal(author.get("igid")) or "",
    )
