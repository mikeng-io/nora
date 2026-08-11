"""Bounded Giphy lookup and download, with text metadata for selection."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from nora.domain.ports import OutboundAttachment


@dataclass(frozen=True)
class GifCandidate:
    id: str
    title: str
    alt_text: str
    tags: tuple[str, ...]
    rating: str

    @property
    def described(self) -> bool:
        return bool(self.title or self.alt_text or self.tags)


class GiphyClient:
    def __init__(self, *, api_key: str, timeout_seconds: float = 10.0, max_bytes: int = 2_000_000) -> None:
        self._api_key, self._timeout, self._max_bytes = api_key, timeout_seconds, max_bytes

    @staticmethod
    def _candidate(gif: dict) -> GifCandidate | None:
        if gif.get("rating") not in {"g", "pg"} or not isinstance(gif.get("id"), str):
            return None
        tags = tuple(str(tag) for key in ("tags", "featured_tags", "user_tags")
                     for tag in (gif.get(key) or []) if isinstance(tag, str))
        candidate = GifCandidate(gif["id"], str(gif.get("title") or ""),
                                 str(gif.get("alt_text") or ""), tags, str(gif["rating"]))
        return candidate if candidate.described else None

    async def search(self, *, query: str) -> tuple[GifCandidate, ...]:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._timeout)) as session:
                async with session.get("https://api.giphy.com/v1/gifs/search", params={
                    "api_key": self._api_key, "q": query, "limit": 8, "rating": "pg",
                }) as response:
                    body = await response.json() if response.status == 200 else {}
            return tuple(c for item in body.get("data", []) if isinstance(item, dict)
                         if (c := self._candidate(item)) is not None)
        except Exception:
            return ()

    async def fetch(self, *, candidate_id: str) -> OutboundAttachment | None:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._timeout)) as session:
                async with session.get(f"https://api.giphy.com/v1/gifs/{candidate_id}", params={"api_key": self._api_key, "rating": "pg"}) as response:
                    body = await response.json() if response.status == 200 else {}
                gif = body.get("data") if isinstance(body, dict) else None
                if not isinstance(gif, dict) or self._candidate(gif) is None:
                    return None
                url = ((gif.get("images") or {}).get("downsized") or {}).get("url")
                if not isinstance(url, str) or not url.startswith("https://"):
                    return None
                async with session.get(url, allow_redirects=False) as media:
                    data = await media.read()
            if len(data) > self._max_bytes or not data.startswith((b"GIF87a", b"GIF89a")):
                return None
            return OutboundAttachment(data, "image/gif", "reaction.gif")
        except Exception:
            return None
