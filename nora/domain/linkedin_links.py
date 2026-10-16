"""One LinkedIn link parser, for `linkedin_read`.

The rules follow `nora.domain.instagram_links` and `nora.domain.x_links`:

* **The host is an exact match** on the parsed hostname: `linkedin.com`, or
  one subdomain of it that is `www` or a two-letter country code
  (`hk.linkedin.com`). Never a suffix or a substring match on the whole host,
  so `linkedin.com.evil.example` is refused. A port, credentials in the
  netloc, or a scheme other than http(s) refuse the link.
* **Each path segment is checked whole against its own charset**, ASCII
  only: a person's public id is letters, digits and `-` (3 to 100), a
  company's slug the same (1 to 100). An id LinkedIn percent-encodes (a
  non-Latin name) is refused, not decoded.
* **The query and the fragment are dropped.** Only the validated id or slug
  leaves.

A link names a person (`/in/<id>`, with anything after it such as
`/recent-activity/all/` ignored), a company (`/company/<slug>`, likewise), or
one post (`/posts/...`, `/feed/update/...`, `/pulse/...`). A post is
recognized only so the tool can say it cannot be read from its link: no
backend reads one LinkedIn post by its URL. Everything else is `None`.

Framework-free and standard library only, as everything in `nora.domain` is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import SplitResult, urlsplit

#: What a link can name.
LinkedInLinkKind = Literal["person", "company", "post"]

# Never a leading `-`: the id or slug becomes a command-line word on the host,
# where a leading dash would read as a flag.
PERSON_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{2,99}")
COMPANY_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}")
_COUNTRY_SUBDOMAIN_RE = re.compile(r"[a-z]{2}")

#: First path segments that name one post (or article). Recognized, never read.
_POST_FIRST_SEGMENTS = frozenset({"posts", "feed", "pulse"})


def is_handle(value: object) -> bool:
    """A string that can stand as a LinkedIn person's public id."""
    return isinstance(value, str) and PERSON_ID_RE.fullmatch(value) is not None


def _host_ok(host: str | None) -> bool:
    if host is None:
        return False
    if host == "linkedin.com":
        return True
    sub, dot, rest = host.partition(".")
    return bool(dot) and rest == "linkedin.com" and (
        sub == "www" or _COUNTRY_SUBDOMAIN_RE.fullmatch(sub) is not None
    )


@dataclass(frozen=True)
class LinkedInLink:
    """A validated LinkedIn link. `key` is the person's public id for a
    `person`, the slug for a `company`, and empty for a `post` (which is never
    read)."""

    kind: LinkedInLinkKind
    key: str = ""


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
        return (
            parts.scheme in ("https", "http")
            and _host_ok(parts.hostname)
            and parts.port is None
            and parts.username is None
            and parts.password is None
        )
    except ValueError:
        return False


def is_linkedin_host(url: object) -> bool:
    """Whether `url` names a LinkedIn host at all, whatever its path."""
    split = _split(url)
    if split is None:
        return False
    try:
        return _host_ok(split[0].hostname)
    except ValueError:
        return False


def parse_link(url: object) -> LinkedInLink | None:
    """`url` → a validated `LinkedInLink`, or `None` when anything about it is
    outside the rules in the module docstring."""
    split = _split(url)
    if split is None:
        return None
    parts, segments = split
    if not _origin_ok(parts) or not segments or not segments[0]:
        return None
    first = segments[0]
    if first == "in" and len(segments) >= 2:
        return LinkedInLink("person", segments[1]) if is_handle(segments[1]) else None
    if first == "company" and len(segments) >= 2:
        slug = segments[1]
        return LinkedInLink("company", slug) if COMPANY_SLUG_RE.fullmatch(slug) else None
    if first in _POST_FIRST_SEGMENTS and len(segments) >= 2:
        return LinkedInLink("post")
    return None
