"""What every `SearchProvider` adapter holds identically, so a backend swap is a
wiring change and not a behaviour change (DEC-157).

Two adapters live behind one port (`TavilySearchProvider`, `ExaSearchProvider`)
and a third would make three. What belongs here is only what MUST NOT differ
between them:

* **The `unavailable` copy.** These strings are model-facing instruction — they
  are what stops Nora answering as if she had looked something up (R-5/R-27).
  If they drifted per backend, «I couldn't check» would mean a different thing
  depending on which vendor the operator picked, which is exactly the property
  the port exists to deny.
* **The clamp.** A tool result is re-sent on every subsequent model call, so
  what accumulates must be bounded. The
  bound is configured on `[search]` itself rather than per backend for the same
  reason, and the function that applies it lives once.

Deliberately NOT here: the envelope parsing, the auth header, the request body.
Those are each vendor's own shape and pretending otherwise is how an adapter
grows a branch per provider inside a "shared" helper.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ...domain.blocks import wrap_outside_text

__all__ = [
    "MAX_IMAGE_LINKS", "UNAVAILABLE_FETCH", "UNAVAILABLE_SEARCH", "clamp_chars",
    "image_links", "wrap",
]

# Re-exported so an adapter imports its wrapper from the search package.
# Wrapping one piece of outside text as a block is `nora.domain.blocks`'
# subject. `name` is its first positional argument and has no default — see
# there for why.
wrap = wrap_outside_text


def clamp_chars(text: str, max_chars: int) -> tuple[str, bool]:
    """Clamp `text` to `max_chars`, stating whether it was truncated."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


#: How many picture links one fetched page hands back. A result is re-sent on
#: every later model call, so the list is bounded like the text beside it.
MAX_IMAGE_LINKS = 8
_MAX_LINK_CHARS = 2000
# The raster types the portal can show and Nora keeps. A vendor's `images` list
# also carries links to PAGES (a hotel listing, a related post), and `media_fetch`
# would spend a download on each to answer `not_an_image`; a link is kept only when
# it names a picture: an extension of those types in its path, Next.js's `/_next/image`
# proxy, a resize parameter in its query, or a host that serves pictures. Nothing is
# fetched here, and a wrong guess costs one download that answers `not_an_image`.
_PICTURE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
# Many image CDNs serve a picture from an extensionless path and say so in the
# query (`?w=800`, `?fm=jpg`, `=w800-h600` on a Google host is the exception below)
# or by the host's job. A page link carries none of these.
_RESIZE_PARAMS = frozenset({"w", "h", "width", "height", "fm", "format", "auto", "q"})
_IMAGE_HOST_LABELS = frozenset({"images", "image", "img", "photos", "i"})
_IMAGE_HOST_SUFFIXES = (
    ".googleusercontent.com", ".cloudinary.com", ".imgix.net", "miro.medium.com",
)


def _names_a_picture(link: str) -> bool:
    parts = urlsplit(link)
    path = parts.path.lower()
    host = (parts.hostname or "").lower()
    if path.endswith(_PICTURE_EXTENSIONS) or "/_next/image" in path:
        return True
    if _RESIZE_PARAMS & {k.lower() for k, _ in parse_qsl(parts.query)}:
        return True
    return host.split(".")[0] in _IMAGE_HOST_LABELS or host.endswith(_IMAGE_HOST_SUFFIXES)


def image_links(raw: Any, limit: int = MAX_IMAGE_LINKS) -> list[str]:
    """The http(s) links in a vendor's `images`/`imageLinks` list that name a
    picture, in order, each once. Anything else (a non-list, a `data:` URI, a
    relative or absurdly long link, a non-string, a link to a page or to a type
    the portal cannot show) is dropped, never raised on: a link is a candidate
    for `media_fetch`, which downloads under its own guard."""
    if not isinstance(raw, list):
        return []
    seen: dict[str, None] = {}
    for item in raw:
        if isinstance(item, dict):
            item = item.get("url")
        if not isinstance(item, str):
            continue
        link = item.strip()
        if (
            link.startswith(("http://", "https://"))
            and len(link) <= _MAX_LINK_CHARS
            and _names_a_picture(link)
        ):
            seen.setdefault(link)
        if len(seen) >= limit:
            break
    return list(seen)


UNAVAILABLE_SEARCH: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't run that search just now. Say so, and don't answer as "
            "if you'd looked it up.",
}

UNAVAILABLE_FETCH: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't open that link just now. Say so, and don't answer as "
            "if you'd read it.",
}
