"""One X link parser, for `x_read`.

The rules follow `nora.domain.instagram_links`:

* **The host is an exact match** on the parsed hostname: `x.com`,
  `twitter.com`, and each with `www.` or `mobile.`. Never a suffix or a
  substring, so `x.com.evil.example` is refused. A port, credentials in the
  netloc, or a scheme other than http(s) refuse the link.
* **Each path segment is checked whole against its own charset**: a username
  is X's own rule (letters, digits and `_`, at most 15), a post id is digits.
  Both are ASCII by construction, so a lookalike character is refused.
* **The query and the fragment are dropped.** Only the validated username or
  post id leaves, so `?s=20&t=...` share junk never reaches the bridge.

A link names one of two things: a post (`/<user>/status/<id>`, also X's
id-only `/i/status/<id>` and `/i/web/status/<id>`, with anything after the id
such as `/photo/1` ignored) or an account (`/<user>`, the user not one of X's
own pages). Everything else is `None`.

Framework-free and standard library only, as everything in `nora.domain` is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import SplitResult, urlsplit

#: What a link can name.
XLinkKind = Literal["post", "profile"]

#: The hosts a link may name, exactly (module docstring).
X_HOSTS = frozenset({
    "x.com", "www.x.com", "mobile.x.com",
    "twitter.com", "www.twitter.com", "mobile.twitter.com",
})

# `fullmatch`, never `match`: a segment that is a valid username followed by
# anything else is not one.
USERNAME_RE = re.compile(r"[A-Za-z0-9_]{1,15}")
POST_ID_RE = re.compile(r"[0-9]{1,25}")

#: First path segments X uses for its own pages. A one-segment path is an
#: account only when its segment is not one of these. Not exhaustive, and it
#: need not be: a word missed here is sent as a username, and X answers that
#: no such account exists or names the account that really holds it.
_RESERVED_FIRST_SEGMENTS = frozenset({
    "account", "communities", "compose", "explore", "hashtag", "home", "i",
    "intent", "jobs", "lists", "login", "logout", "messages", "notifications",
    "premium", "privacy", "search", "settings", "share", "signup", "tos",
})


def is_handle(value: object) -> bool:
    """A string that can stand as an X handle: the username charset, whole."""
    return isinstance(value, str) and USERNAME_RE.fullmatch(value) is not None


@dataclass(frozen=True)
class XLink:
    """A validated X link. `key` is the username for a `profile` and the
    numeric post id for a `post`; it has passed its charset, so it is safe to
    send as it is."""

    kind: XLinkKind
    key: str


def _split(url: object) -> tuple[SplitResult, list[str]] | None:
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    path = parts.path.rstrip("/")
    segments = path[1:].split("/") if path.startswith("/") else []
    return parts, segments


def _origin_ok(parts: SplitResult) -> bool:
    try:
        # `.port` raises on a netloc like `host:abc`: a malformed origin, so
        # no link, never a crash.
        return (
            parts.scheme in ("https", "http")
            and parts.hostname in X_HOSTS
            and parts.port is None
            and parts.username is None
            and parts.password is None
        )
    except ValueError:
        return False


def is_x_host(url: object) -> bool:
    """Whether `url` names one of X's hosts at all, whatever its path. Lets a
    caller tell «an X link I can't read» from «not an X link»."""
    split = _split(url)
    if split is None:
        return False
    try:
        return split[0].hostname in X_HOSTS
    except ValueError:
        return False


def parse_link(url: object) -> XLink | None:
    """`url` → a validated `XLink`, or `None` when anything about it is
    outside the rules in the module docstring."""
    split = _split(url)
    if split is None:
        return None
    parts, segments = split
    if not _origin_ok(parts):
        return None
    # An id-only post link: /i/status/<id> or /i/web/status/<id>.
    if segments[:2] == ["i", "status"] and len(segments) >= 3:
        post_id = segments[2]
    elif segments[:3] == ["i", "web", "status"] and len(segments) >= 4:
        post_id = segments[3]
    elif len(segments) >= 3 and segments[1] == "status":
        if not is_handle(segments[0]) or segments[0].lower() in _RESERVED_FIRST_SEGMENTS:
            return None
        post_id = segments[2]
    elif len(segments) == 1:
        user = segments[0]
        if not is_handle(user) or user.lower() in _RESERVED_FIRST_SEGMENTS:
            return None
        return XLink(kind="profile", key=user)
    else:
        return None
    if POST_ID_RE.fullmatch(post_id) is None:
        return None
    return XLink(kind="post", key=post_id)
