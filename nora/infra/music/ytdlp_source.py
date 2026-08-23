"""`YtDlpSource` — the `TrackSource` port's first implementation
(design/discord-music/01-playback-and-source.md).

Resolves a query or URL to a direct, streamable media URL WITHOUT
downloading (`extract_info(..., download=False)`) — the same distinction
`01-playback-and-source.md` draws between "resolve" and "download". The
returned `url` is handed to `ffmpeg` by the caller; nothing here ever writes
a byte to disk.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol
from urllib.parse import urlsplit

import yt_dlp

from nora.domain.ports import ResolvedTrack

log = logging.getLogger("nora.music")

# The allowlist is enforced HERE, before the extractor is ever called — a
# pasted URL is untrusted input, and this must not become "whatever yt-dlp's
# extractor set happens to accept" (01-playback-and-source.md).
_ALLOWED_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)

# `format=bestaudio/best` is what makes a resolved single-format info dict
# carry a top-level `url` (yt-dlp's own behaviour when exactly one format is
# selected) rather than a `formats` list this adapter would have to rank
# itself. `noplaylist` refuses to expand a playlist URL into every video it
# contains — one `/music play` call resolves to one track, never N.
_YDL_OPTS: dict[str, Any] = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
}


class _Extractor(Protocol):
    def extract_info(self, url: str, *, download: bool) -> Any: ...


def _default_extractor() -> _Extractor:
    # `_YDL_OPTS`'s TypedDict stub demands an exact `_Params` shape; the
    # documented `yt_dlp.YoutubeDL` API is "pass it a dict of option names"
    # (same argument as `nora/infra/vision/client.py`'s own `type: ignore`
    # for a gateway whose contract is the wire JSON, not the SDK's stub).
    return yt_dlp.YoutubeDL(_YDL_OPTS)  # type: ignore[arg-type]


class YtDlpSource:
    """Satisfies `nora.domain.ports.TrackSource`. NEVER raises."""

    def __init__(self, *, extractor: _Extractor | None = None) -> None:
        self._extractor = extractor if extractor is not None else _default_extractor()

    async def resolve(self, *, query_or_url: str) -> ResolvedTrack | None:
        target = _target_for(query_or_url)
        if target is None:
            return None
        try:
            info = await asyncio.to_thread(self._extractor.extract_info, target, download=False)
        except Exception:
            log.warning("yt-dlp could not resolve %r", target, exc_info=True)
            return None
        return _track_from_info(info)


def _target_for(query_or_url: str) -> str | None:
    """A real URL must be YouTube (§ allowlist above); anything else is
    treated as a search query, the same way a person typing into a search
    box does not need to know yt-dlp's `ytsearch1:` syntax exists."""
    parsed = urlsplit(query_or_url)
    if parsed.scheme in ("http", "https"):
        host = (parsed.hostname or "").lower()
        if host not in _ALLOWED_HOSTS:
            return None
        return query_or_url
    return f"ytsearch1:{query_or_url}"


def _track_from_info(raw: dict[str, Any] | None) -> ResolvedTrack | None:
    if raw is None:
        return None
    info: dict[str, Any] = raw
    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        if not entries:
            return None
        info = entries[0]
    stream_url = info.get("url")
    if not stream_url:
        # A live stream still buffering, or an extractor result this
        # adapter's single-format `_YDL_OPTS` didn't resolve to a URL --
        # "could not resolve a playable stream" is a plain, expected outcome
        # here, not an error.
        return None
    return ResolvedTrack(
        title=info.get("title") or info.get("id") or "unknown",
        stream_url=stream_url,
        duration_seconds=info.get("duration"),
        source="youtube",
        source_id=str(info.get("id", "")),
    )
