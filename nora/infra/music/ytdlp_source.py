"""`YtDlpSource` — the `TrackSource` port's first implementation
(design/discord-music/01-playback-and-source.md).

Resolves a query or URL to one or more direct, streamable media URLs
WITHOUT downloading (`extract_info(..., download=False)`) — the same
distinction `01-playback-and-source.md` draws between "resolve" and
"download". The returned `url`s are handed to `ffmpeg` by the caller;
nothing here ever writes a byte to disk.

**Playlist expansion is deliberately narrow, not "any URL yt-dlp would
expand".** A `/playlist?list=...` link (what YouTube's own UI hands you
from a playlist's page) expands into every video it contains, capped at
`_MAX_PLAYLIST_TRACKS`. A `/watch?v=...&list=...` link (an ordinary video
that happens to carry an incidental `list=`/`index=` — sharing a video
FROM inside a playlist, or YouTube's autoplay "Mix" queue) resolves to
that ONE video only, by construction: `_target_for` strips those params
before the URL ever reaches yt-dlp, so there is nothing left for it to
expand. Least-surprise: pasting a video link never silently queues fifty
unrelated tracks.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any, Protocol
from urllib.parse import SplitResult, parse_qs, urlencode, urlsplit, urlunsplit

import yt_dlp

from nora.domain.ports import ResolvedTrack

log = logging.getLogger("nora.music")

# The allowlist is enforced HERE, before the extractor is ever called — a
# pasted URL is untrusted input, and this must not become "whatever yt-dlp's
# extractor set happens to accept" (01-playback-and-source.md).
_ALLOWED_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)

# A link shared out of a 500-track "Mix" or someone's personal playlist must
# not flood a guild's queue from one /music play call.
_MAX_PLAYLIST_TRACKS = 50

# `format=bestaudio/best` is what makes a resolved single-format info dict
# carry a top-level `url` (yt-dlp's own behaviour when exactly one format is
# selected) rather than a `formats` list this adapter would have to rank
# itself. No `noplaylist` here — playlist expansion is gated by URL SHAPE
# instead (`_target_for`), not by refusing it outright.
_YDL_OPTS: dict[str, Any] = {
    "format": "bestaudio/best",
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

    async def resolve(self, *, query_or_url: str) -> Sequence[ResolvedTrack]:
        target = _target_for(query_or_url)
        if target is None:
            return ()
        try:
            info = await asyncio.to_thread(self._extractor.extract_info, target, download=False)
        except Exception:
            log.warning("yt-dlp could not resolve %r", target, exc_info=True)
            return ()
        return _tracks_from_info(info)


def _is_playlist_page(parsed: SplitResult) -> bool:
    """True only for `/playlist?list=...` — the link YouTube's own UI gives
    you FROM a playlist's page, never a video link that merely carries an
    incidental `list=`."""
    return parsed.path.rstrip("/").endswith("/playlist") and "list" in parse_qs(parsed.query)


def _without_playlist_params(url: str) -> str:
    """Strips `list`/`index`/`start_radio` so a shared VIDEO link — even one
    copied from inside a playlist or an autoplay Mix — resolves to that one
    video, never silently expands."""
    parsed = urlsplit(url)
    kept = {
        k: v for k, v in parse_qs(parsed.query).items()
        if k not in ("list", "index", "start_radio")
    }
    query = urlencode(kept, doseq=True)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _target_for(query_or_url: str) -> str | None:
    """A real URL must be YouTube (§ allowlist above); anything else is
    treated as a search query, the same way a person typing into a search
    box does not need to know yt-dlp's `ytsearch1:` syntax exists."""
    parsed = urlsplit(query_or_url)
    if parsed.scheme in ("http", "https"):
        host = (parsed.hostname or "").lower()
        if host not in _ALLOWED_HOSTS:
            return None
        if _is_playlist_page(parsed):
            return query_or_url
        return _without_playlist_params(query_or_url)
    return f"ytsearch1:{query_or_url}"


def _tracks_from_info(raw: dict[str, Any] | None) -> Sequence[ResolvedTrack]:
    if raw is None:
        return ()
    if raw.get("_type") == "playlist":
        entries = raw.get("entries") or []
        tracks = []
        for entry in entries[:_MAX_PLAYLIST_TRACKS]:
            track = _track_from_single_info(entry)
            if track is not None:
                tracks.append(track)
        return tuple(tracks)
    track = _track_from_single_info(raw)
    return (track,) if track is not None else ()


def _track_from_single_info(info: dict[str, Any] | None) -> ResolvedTrack | None:
    if info is None:
        return None
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
