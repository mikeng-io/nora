"""Bounded Giphy lookup and download, with text metadata for selection."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import aiohttp

from nora.domain.media import GifCandidate
from nora.domain.ports import OutboundAttachment


class GiphyClient:
    def __init__(
        self, *, api_key: str, timeout_seconds: float = 10.0, max_bytes: int = 2_000_000
    ) -> None:
        self._api_key, self._timeout, self._max_bytes = api_key, timeout_seconds, max_bytes

    @staticmethod
    def _clean(value: object, *, limit: int) -> str:
        return " ".join("".join(ch for ch in str(value or "") if ch >= " ").split())[:limit]

    @staticmethod
    def _candidate(gif: dict[str, Any]) -> GifCandidate | None:
        if gif.get("rating") not in {"g", "pg"} or not isinstance(gif.get("id"), str):
            return None
        tags = tuple(
            GiphyClient._clean(tag, limit=48)
            for key in ("tags", "featured_tags", "user_tags")
            for tag in (gif.get(key) or [])
            if isinstance(tag, str)
        )[:12]
        candidate = GifCandidate(
            "giphy",
            gif["id"],
            GiphyClient._clean(gif.get("title"), limit=240),
            GiphyClient._clean(gif.get("alt_text"), limit=480),
            tags,
            str(gif["rating"]),
        )
        return candidate if candidate.described else None

    async def search(self, *, query: str) -> tuple[GifCandidate, ...]:
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    "https://api.giphy.com/v1/gifs/search",
                    params={"api_key": self._api_key, "q": query, "limit": 8, "rating": "pg"},
                ) as response,
            ):
                body = await response.json() if response.status == 200 else {}
            return tuple(
                c
                for item in body.get("data", [])
                if isinstance(item, dict) and (c := self._candidate(item)) is not None
            )
        except (aiohttp.ClientError, TimeoutError, TypeError, ValueError):
            return ()

    async def fetch(self, *, candidate: GifCandidate) -> OutboundAttachment | None:
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"https://api.giphy.com/v1/gifs/{candidate.id}",
                    params={"api_key": self._api_key, "rating": "pg"},
                ) as response:
                    body = await response.json() if response.status == 200 else {}
                gif = body.get("data") if isinstance(body, dict) else None
                if not isinstance(gif, dict) or self._candidate(gif) != candidate:
                    return None
                url = ((gif.get("images") or {}).get("downsized") or {}).get("url")
                if not isinstance(url, str):
                    return None
                host = urlparse(url).hostname
                if host is None:
                    return None
                is_giphy_media = host == "media.giphy.com" or (
                    host.startswith("media") and host.endswith(".giphy.com")
                )
                if not is_giphy_media:
                    return None
                async with session.get(url, allow_redirects=False) as media:
                    content_length = int(media.headers.get("Content-Length", 0) or 0)
                    if media.status != 200 or content_length > self._max_bytes:
                        return None
                    chunks, size = [], 0
                    async for chunk in media.content.iter_chunked(65_536):
                        size += len(chunk)
                        if size > self._max_bytes:
                            return None
                        chunks.append(chunk)
                    data = b"".join(chunks)
            if not data.startswith((b"GIF87a", b"GIF89a")):
                return None
            return OutboundAttachment(data, "image/gif", "reaction.gif")
        except (aiohttp.ClientError, TimeoutError, TypeError, ValueError):
            return None
