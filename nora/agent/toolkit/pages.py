from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

from nora.agent.toolkit.results import _record
from nora.domain.blocks import wrap_outside_text

_PAGE_SLICE_CHARS = 3000

_PAGE_MATERIALIZE_CHARS = 250_000

_PAGE_BLOB_PUT_GRACE_SECONDS = 3600.0

_ABOUT_CHUNK_CHARS = 1200

_ABOUT_TOP_K = 3


def _page_chunks(text: str, size: int = _ABOUT_CHUNK_CHARS) -> list[str]:
    """`text` split on paragraph boundaries into ~`size`-char chunks; a lone"""
    chunks: list[str] = []
    cur = ""
    for part in text.split("\n\n"):
        if cur and len(cur) + len(part) + 2 > size:
            chunks.append(cur)
            cur = ""
        cur = part if not cur else f"{cur}\n\n{part}"
        while len(cur) > size:
            chunks.append(cur[:size])
            cur = cur[size:]
    if cur:
        chunks.append(cur)
    return chunks


def _about_terms(about: str) -> set[str]:
    """The words `about` can look for: two letters or more."""
    return {t for t in re.findall(r"\w+", about.lower()) if len(t) >= 2}


def _rank_chunks(chunks: list[str], about: str, k: int = _ABOUT_TOP_K) -> list[str]:
    """The `k` chunks with the most `about` term hits, verbatim and in page"""
    terms = _about_terms(about)
    if not terms:
        return []
    scored = sorted(
        ((sum(low.count(t) for t in terms), i) for i, c in enumerate(chunks) if (low := c.lower())),
        key=lambda si: (-si[0], si[1]),
    )
    return [chunks[i] for s, i in scored if s > 0][:k]


def _serve_page(
    *,
    url: str,
    text: str,
    offset: int,
    about: str | None,
    images: list[str],
    truncated: bool,
    saved_copy: bool,
) -> dict:
    """The bounded view `web_fetch` returns over a stored page: one `offset`"""
    out: dict[str, Any] = {"status": "ok", "url": url}
    if saved_copy:
        out["saved_copy"] = True
    if truncated:
        out["truncated"] = True
    if about:
        hits = _rank_chunks(_page_chunks(text), about)
        if not hits:
            note = (
                "None of the page I have matches those words; it may say it "
                "differently. Say you couldn't find it there."
                if _about_terms(about)
                else "That has no word of two letters or more to look for. Ask for "
                "a word or phrase from the page."
            )
            if truncated:
                note += " What I have is cut short, so it may be further on."
            miss: dict[str, Any] = {"status": "nothing_found", "url": url, "note": note}
            if truncated:
                miss["truncated"] = True
            return miss
        out["about"] = about
        out["total"] = len(text)
        out["parts"] = [wrap_outside_text("fetched_page", url, h) for h in hits]
        return out
    start = max(0, offset)
    end = min(start + _PAGE_SLICE_CHARS, len(text))
    out["text"] = wrap_outside_text("fetched_page", url, text[start:end])
    out["offset"] = start
    out["total"] = len(text)
    if end < len(text):
        out["next_offset"] = end
    if images and start == 0:
        out["images"] = images
    return out


def _without_map_coordinates(spec: dict[str, Any]) -> dict[str, Any]:
    """`spec` with every Map's `lat`, `lng` and `route` removed."""
    out = copy.deepcopy(spec)
    stack = [out.get("root")]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("type") == "Map":
            props = node.get("props") or {}
            props.pop("route", None)
            for place in props.get("places") or []:
                if isinstance(place, dict):
                    place.pop("lat", None)
                    place.pop("lng", None)
        stack.extend(node.get("children") or [])
    return out


def _with_scheme(link: str) -> str:
    """A pasted link as the parser reads it: a link given without a scheme"""
    text = link.strip()
    return text if "://" in text else "https://" + text


def _same_link(a: str, b: str, parse: Callable[[str], object | None]) -> bool:
    """Whether two pasted links name the same thing, judged by that network's"""
    parsed_a = parse(_with_scheme(a))
    parsed_b = parse(_with_scheme(b))
    if parsed_a is None or parsed_b is None:
        return a.strip() == b.strip()
    return parsed_a == parsed_b


_URL_AND_LINK_DIFFER = {
    "status": "unavailable",
    "note": "Two different links were given, as link and as url. "
    "Call again with the one link you mean, as link.",
}


def _resolve_url_alias(
    tool: str,
    link: str | None,
    url: str | None,
    parse: Callable[[str], object | None],
    metrics,
) -> tuple[str | None, bool]:
    """`url` IS `link`, under the name a model reaches for first, for every"""
    if not (isinstance(url, str) and url.strip()):
        return link, False
    if isinstance(link, str) and link.strip() and not _same_link(link, url, parse):
        _record(metrics, tool, "unavailable", reason="url_and_link")
        return None, True
    return url, False
