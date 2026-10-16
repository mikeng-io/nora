"""`OpenCliBridgeClient` — the X tools' read path to X, over the
host-side bridge (DEC-151).

Nora's container cannot run `opencli` (it exists only on the host), so this
client POSTs `{platform, what, target, limit}` to the bridge's `/read` and
maps the envelope back onto R-27's three states. The bridge re-enforces the
read-only allowlist host-side; this client adds NOTHING to that boundary and
cannot widen it — there is no parameter here that names a command.

The discipline every grounding client holds, unchanged from Tavily's:
* NEVER raises — a non-200, a non-JSON body, an envelope whose `status` is not
  one of the three states, or a transport error is a tool STATUS
  (`unavailable`), never an exception into the loop (R-5). An envelope that
  does not match the documented shape is a wiring gap — `unavailable`, NOT
  `nothing_found`, exactly as `TavilySearchProvider.fetch` treats a malformed
  `/extract` envelope.
* Retrieved text is `social_post`-wrapped (R-13) with the source URL as
  provenance and CLAMPED (`content_max_chars`), because a tool result is
  re-sent on every subsequent model call.
* `target` was already identifier/coordinate-screened at the tool layer
  (`nora.agent.tools`) before it reached here.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp

from ...domain.blocks import wrap_outside_text
from ...domain.ports import SocialProvider

# One copy of every non-read payload — and of the text clamp — shared with the
# router and the Instagram client; see `results.py` for why neither the wording
# nor the bound may be local to one implementation.
from .results import NOTHING_FOUND as _NOTHING_FOUND
from .results import UNAVAILABLE as _UNAVAILABLE
from .results import clamp_chars as _clamp_chars

log = logging.getLogger("nora.social")


class OpenCliBridgeClient(SocialProvider):
    """READ-ONLY X reads via the host bridge's `POST /read`.
    Constructed once per process at the composition root, only when
    `[social] enabled` (there is no credential — `enabled` is the whole gate);
    unwired, `x_read` and `x_search` answer `unavailable`."""

    def __init__(
        self,
        *,
        bridge_url: str = "http://127.0.0.1:19826",
        timeout_seconds: float = 90.0,
        content_max_chars: int = 4000,
    ) -> None:
        self._bridge_url = bridge_url
        self._timeout_seconds = timeout_seconds
        self._content_max_chars = content_max_chars

    async def read(
        self, *, platform: str, what: str, target: str, limit: int,
    ) -> dict[str, Any]:
        """One read. NEVER raises. Three states (R-27): `ok` with wrapped,
        clamped `text` (and `url` when the bridge named one),
        `nothing_found` when the read ran and found nothing, `unavailable`
        for any failure of the call itself — «I could not check», never
        «nothing found»."""
        try:
            return await self._read(platform=platform, what=what, target=target, limit=limit)
        except Exception:
            # The never-raises backstop: a contract violation degrades to
            # unavailable, never propagates into the loop (R-5).
            log.exception("social bridge read failed")
            return dict(_UNAVAILABLE)

    async def _read(self, *, platform: str, what: str, target: str, limit: int) -> dict[str, Any]:
        body = {"platform": platform, "what": what, "target": target, "limit": limit}
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(f"{self._bridge_url}/read", json=body) as response,
        ):
            text = await response.text()
            if response.status != 200:
                # A 400 is the bridge REFUSING the pair (its allowlist is
                # narrower than the tool's, or drift between the two) — a
                # wiring gap, not «nothing there». 5xx the same.
                log.warning("social bridge answered %s", response.status)
                return dict(_UNAVAILABLE)
        try:
            payload: Any = json.loads(text)
        except ValueError:
            log.warning("social bridge did not return JSON")
            return dict(_UNAVAILABLE)
        if not isinstance(payload, dict):
            log.warning("social bridge returned a non-object envelope")
            return dict(_UNAVAILABLE)
        status = payload.get("status")
        if status not in ("ok", "nothing_found", "unavailable"):
            # An unknown status is the envelope not matching the documented
            # shape — unavailable (R-27), never passed through to the model.
            log.warning("social bridge returned an unknown status: %r", status)
            return dict(_UNAVAILABLE)
        if status != "ok":
            # The bridge's OWN `note` is dropped rather than forwarded. It is
            # an unbounded, unescaped string from another process landing in
            # the slot that tells the model what it may SAY. What the miss means
            # is a property of the STATE, so the sentence is ours
            # (`results.py`), and neither state loses any information: there
            # is exactly one thing to say for each.
            return dict(_NOTHING_FOUND if status == "nothing_found" else _UNAVAILABLE)
        if "data" not in payload:
            log.warning("social bridge ok envelope carries no data")
            return dict(_UNAVAILABLE)
        content = _render_text(payload["data"])
        if not content.strip():
            # An `ok` whose data renders to nothing: an empty list, or items
            # carrying none of the keys the renderer reads. «Found nothing»,
            # not an empty read handed to the model to describe.
            # The bridge maps an empty result itself; this covers what it
            # cannot see, items it passes that hold nothing readable.
            return dict(_NOTHING_FOUND)
        clamped, truncated = _clamp_chars(content, self._content_max_chars)
        url = payload.get("source_url")
        url = str(url) if url else None
        # The content is stranger-written (R-13): wrapped `social_post` with the
        # source as provenance. The URL rides PLAIN beside it so the model can
        # cite it in the reply — the same split as a Tavily result.
        #
        # The key is `text`, NOT `content`: Strands' `_wrap_tool_result`
        # (decorator.py:673) passes a dict with both `status` AND `content`
        # through UNCHANGED, so a bare-string `content` reaches the model
        # outside the standard `[{"text": …}]` block — and the model reads that
        # as "empty" (trace 67afb6920005b741b7e314561facc002). Returning the
        # text under a non-`content` key makes Strands wrap this dict the same
        # way it wraps `web_search`'s — the envelope the model demonstrably
        # reads. Same invariant guards.py:513 documents: native tools return a
        # plain dict with no `content` key.
        out: dict[str, Any] = {
            "status": "ok",
            "text": wrap_outside_text(
                "social_post", url or f"{platform} {what}", clamped,
            ),
            "truncated": truncated,
        }
        if url is not None:
            out["url"] = url
        return out


def _render_text(data: Any) -> str:
    """opencli's JSON → compact readable text (what the model reads natively).

    The bridge returns opencli's raw parser output — for `thread` a list of
    post dicts (first = the post, rest of the thread), for `profile` a
    single-item list, for `tweets`/`search` a list. json.dumps-ing that to the
    model buries it under two layers of escaping, and a raw char clamp tears
    mid-record, so the model reads "empty". This renders the same data as the
    few lines a person would actually read; the char clamp below then cuts at
    an item boundary rather than mid-JSON.
    """
    items = data if isinstance(data, list) else [data]
    parts = []
    for item in items:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        parts.append(_render_item(item))
    return "\n\n".join(p for p in parts if p)


def _text(value: Any) -> str:
    """A field as one stripped string, or "" for anything that is not a
    non-empty string (LinkedIn leaves an absent section as "")."""
    return value.strip() if isinstance(value, str) else ""


def _render_item(item: dict[str, Any]) -> str:
    """One opencli item → readable lines, unknown keys ignored gracefully.

    Both post items (`author`, `text`, `likes`, …) and profile items
    (`screen_name`, `bio`, `followers`, …) reduce to the same shape: who ·
    what they say · the numbers · when · the URL. Keys present in one kind are
    simply absent in the other, so a single tolerant renderer serves all of
    `what` rather than four kind-specific formatters drifting apart.
    """
    handle = item.get("author") or item.get("screen_name")
    name = item.get("name")
    if handle and name and name != handle:
        head = f"{handle} ({name})"
    elif handle:
        head = str(handle)
    elif name:
        head = str(name)
    else:
        head = ""
    if item.get("verified"):
        head = f"{head} ✓" if head else "verified"
    # LinkedIn: a role row is «title at company, dates»; a profile or a
    # search hit carries a `headline`. Plain strings only: a key LinkedIn
    # leaves empty renders nothing.
    role = _text(item.get("title"))
    if role and _text(item.get("company")):
        role = f"{role} at {_text(item.get('company'))}"
    if role and _text(item.get("date_range")):
        role = f"{role}, {_text(item.get('date_range'))}"
    headline = _text(item.get("headline"))
    body = (
        _text(item.get("text")) or _text(item.get("bio")) or _text(item.get("body"))
        or _text(item.get("about")) or _text(item.get("description"))
    )
    # Labelled one-line facts (a company page's card, a profile's sections).
    facts = [
        f"{label}: {_text(item.get(key))}"
        for label, key in (
            ("industry", "industry"), ("size", "size"),
            ("headquarters", "headquarters"), ("founded", "founded"),
            ("website", "website"), ("specialties", "specialties"),
            ("experience", "experience"), ("education", "education"),
            ("featured", "featured"),
        )
        if _text(item.get(key))
    ]
    stats = [
        f"{item[key]} {label}"
        for label, key in (
            ("likes", "likes"), ("retweets", "retweets"), ("replies", "replies"),
            ("views", "views"), ("followers", "followers"),
            ("following", "following"), ("tweets", "tweets"),
            ("reactions", "reactions"), ("comments", "comments"),
            ("reposts", "reposts"),
        )
        if isinstance(item.get(key), int) and not isinstance(item.get(key), bool)
    ]
    # OpenCLI reports 0 impressions on any post that is not the viewer's own,
    # so a zero says nothing and is dropped.
    if isinstance(item.get("impressions"), int) and item["impressions"] > 0:
        stats.append(f"{item['impressions']} impressions")
    if item.get("location"):
        stats.append(f"📍 {item['location']}")
    if item.get("created_at"):
        stats.append(str(item["created_at"]))
    if _text(item.get("posted_at")):
        stats.append(_text(item.get("posted_at")))
    lines = [line for line in (head, role, headline, body, *facts) if line]
    if stats:
        lines.append(" · ".join(stats))
    # `profile_url` is a LinkedIn search hit's whole point: which profile to
    # read next. `raw_text` is never read: it repeats every field unbounded.
    # A role row (`profile-experience`) repeats its profile's link on every
    # row, so it is not rendered there.
    for key in ("url",) if role else ("url", "profile_url"):
        if _text(item.get(key)):
            lines.append(_text(item.get(key)))
            break
    media_urls = item.get("media_urls") or []
    if isinstance(media_urls, str):
        # LinkedIn returns one string, not a list.
        media_urls = [u for u in re.split(r"[\s,]+", media_urls) if u.startswith("http")]
    posters = item.get("media_posters") or []
    if isinstance(media_urls, list) and media_urls:
        # Rendered as `[image https://…]` / `[video https://…]` — the same
        # marker shape media_fetch's own docstring teaches, so the model can
        # pass a returned media link straight to `media_fetch(url=…)` (the
        # generic cross-tool read path, DEC-56). A video
        # additionally carries `media_posters` (its preview frame) when
        # opencli returned one; a non-empty poster list tells us which kind a
        # URL is, so the row carries the right label.
        is_video = bool(posters) and isinstance(posters, list)
        for mu in media_urls[:3]:
            lines.append(f"[video {mu}]" if is_video else f"[image {mu}]")
        if is_video and posters:
            lines.append(f"[poster {posters[0]}]")
    qt = item.get("quoted_tweet")
    if isinstance(qt, dict) and qt.get("text"):
        # The quoted post is often the substance (a claim the author is
        # replying to), so one compact line of it rides along.
        qt_text = str(qt.get("text"))
        qt_head = f"quoted @{qt.get('author')}" if qt.get("author") else "quoted"
        lines.append(f"[{qt_head}: {qt_text[:200]}]")
    return "\n".join(lines)
