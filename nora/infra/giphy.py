"""Small, bounded Giphy lookup used only by the GIF reply tool."""

from __future__ import annotations

import aiohttp

from nora.domain.ports import OutboundAttachment


class GiphyClient:
    def __init__(self, *, api_key: str, timeout_seconds: float = 10.0, max_bytes: int = 2_000_000) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes

    async def select(self, *, query: str) -> OutboundAttachment | None:
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    "https://api.giphy.com/v1/gifs/search",
                    params={"api_key": self._api_key, "q": query, "limit": 10, "rating": "pg"},
                ) as response:
                    body = await response.json() if response.status == 200 else {}
                for gif in body.get("data", []):
                    if not isinstance(gif, dict) or gif.get("rating") not in {"g", "pg"}:
                        continue
                    rendition = (gif.get("images") or {}).get("downsized") or {}
                    url = rendition.get("url")
                    if not isinstance(url, str) or not url.startswith("https://"):
                        continue
                    async with session.get(url, allow_redirects=False) as media:
                        data = await media.read()
                        if media.status != 200 or len(data) > self._max_bytes or not data.startswith((b"GIF87a", b"GIF89a")):
                            continue
                    return OutboundAttachment(data, "image/gif", "reaction.gif")
        except Exception:
            return None
        return None
