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

**A playlist listing is flat, not fully resolved.** Expanding a 50-track
playlist by fully resolving every entry's playable stream URL upfront
(one `extract_info` per video) makes a single `/music play` call block on
up to 50 network round-trips before it can even reply. Instead, a
playlist target is extracted with `extract_flat=True` — title and video
ID per entry, no per-track stream lookup — and each entry's real stream
URL is resolved lazily, on demand, right before it plays
(`MusicPlayer._advance`, `03-state-and-persistence.md`). This is why
`ResolvedTrack.locator` exists: it is always a re-derivable `query_or_url`
for that specific track, so a later `resolve()` call gets the actual
stream URL back.
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
    "socket_timeout": 20,
}

# A playlist LISTING only needs title + video ID per entry — `extract_flat`
# skips the per-video format lookup entirely, which is what keeps expanding
# a 50-track playlist from blocking a single `/music play` call on up to 50
# network round-trips (module docstring). No `format` option: flat entries
# never carry a `formats` list to select from.
_FLAT_YDL_OPTS: dict[str, Any] = {
    "extract_flat": True,
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    # Diagnosed 2026-09-02: a shared `YoutubeDL` instance under concurrent
    # `asyncio.to_thread` load measured 20-31s for one Mix extraction (vs.
    # single digits idle) -- and `Task.cancel()` on the coroutine awaiting it
    # does NOT stop the underlying thread (`to_thread` has no forced
    # interrupt), so a stale prewarm/retry keeps a thread-pool slot pinned
    # until the request itself gives up. Without a bound, enough of these
    # pile up over a long session to starve the default executor, silently
    # stalling every later autoplay attempt with nothing to catch or log.
    "socket_timeout": 20,
}


class _Extractor(Protocol):
    def extract_info(self, url: str, *, download: bool) -> Any: ...


def _default_extractor() -> _Extractor:
    # `_YDL_OPTS`'s TypedDict stub demands an exact `_Params` shape; the
    # documented `yt_dlp.YoutubeDL` API is "pass it a dict of option names"
    # (same argument as `nora/infra/vision/client.py`'s own `type: ignore`
    # for a gateway whose contract is the wire JSON, not the SDK's stub).
    return yt_dlp.YoutubeDL(_YDL_OPTS)  # type: ignore[arg-type]


def _default_flat_extractor() -> _Extractor:
    return yt_dlp.YoutubeDL(_FLAT_YDL_OPTS)  # type: ignore[arg-type]


class YtDlpSource:
    """Satisfies `nora.domain.ports.TrackSource`. NEVER raises."""

    def __init__(
        self, *, extractor: _Extractor | None = None, flat_extractor: _Extractor | None = None,
    ) -> None:
        self._extractor = extractor if extractor is not None else _default_extractor()
        self._flat_extractor = (
            flat_extractor if flat_extractor is not None else _default_flat_extractor()
        )

    async def resolve(self, *, query_or_url: str) -> Sequence[ResolvedTrack]:
        target = _target_for(query_or_url)
        if target is None:
            return ()
        is_playlist = _is_playlist_page(urlsplit(target))
        extractor = self._flat_extractor if is_playlist else self._extractor
        try:
            info = await asyncio.to_thread(extractor.extract_info, target, download=False)
        except Exception:
            log.warning("yt-dlp could not resolve %r", target, exc_info=True)
            return ()
        if is_playlist:
            return _tracks_from_flat_playlist(info)
        track = _track_from_resolved_info(_unwrap_search_result(info))
        return (track,) if track is not None else ()

    async def related(self, *, locator: str) -> Sequence[ResolvedTrack]:
        video_id = _video_id_from_locator(locator)
        if video_id is None:
            return ()
        # YouTube's own "Mix" — a `list=RD<id>` on that video's own watch URL
        # is the well-known trick for the SAME autoplay queue YouTube's own
        # UI offers, seeded from this one track. Flat, like an ordinary
        # playlist listing (module docstring) — an autoplay pick that turns
        # out unresolvable by play time is exactly `_advance`'s existing
        # "skip to the next queued track" case, not worth resolving streams
        # for upfront.
        target = f"{_watch_url(video_id)}&list=RD{video_id}"
        try:
            info = await asyncio.to_thread(
                self._flat_extractor.extract_info, target, download=False,
            )
        except Exception:
            log.warning("yt-dlp could not resolve related tracks for %r", locator, exc_info=True)
            return ()
        tracks = _tracks_from_flat_playlist(info)
        # A Mix always lists its own seed video first -- autoplay must offer
        # something NEW, never re-queue the track that just finished.
        return tuple(t for t in tracks if t.source_id != video_id)


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


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _video_id_from_locator(locator: str) -> str | None:
    """`ResolvedTrack.locator` for `source="youtube"` is always exactly
    `_watch_url(video_id)` (`_track_from_flat_entry`/`_track_from_resolved_
    info`, both above) — never a search string or a stripped-but-still-
    query-bearing URL — so the `v` param round-trips cleanly back to it."""
    values = parse_qs(urlsplit(locator).query).get("v")
    return values[0] if values else None


def _unwrap_search_result(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """`ytsearch1:...` comes back wrapped in the SAME `{"_type": "playlist",
    "entries": [...]}` shape a real playlist page does, even for a single
    result — yt-dlp treats every search as a mini-playlist. This is
    distinct from `_is_playlist_page`'s TARGET-shape check above (a search
    query is never treated as a playlist link to expand); it only unwraps
    this one already-known-single-result shape so `_track_from_resolved_
    info` sees the same plain info dict a direct video URL resolves to."""
    if raw is not None and raw.get("_type") == "playlist":
        entries = raw.get("entries") or []
        return entries[0] if entries else None
    return raw


def _tracks_from_flat_playlist(raw: dict[str, Any] | None) -> Sequence[ResolvedTrack]:
    if raw is None:
        return ()
    entries = raw.get("entries") or []
    tracks = []
    for entry in entries[:_MAX_PLAYLIST_TRACKS]:
        track = _track_from_flat_entry(entry)
        if track is not None:
            tracks.append(track)
    return tuple(tracks)


def _track_from_flat_entry(entry: dict[str, Any] | None) -> ResolvedTrack | None:
    """A flat playlist entry carries an ID and a title only — no per-track
    stream lookup happened (module docstring). `stream_url` stays empty;
    `locator` is what `MusicPlayer._advance` later hands back to `resolve()`
    to get the real stream URL, right before this track plays."""
    if entry is None:
        return None
    video_id = entry.get("id")
    if not video_id:
        return None
    video_id = str(video_id)
    return ResolvedTrack(
        title=entry.get("title") or video_id,
        stream_url="",
        duration_seconds=entry.get("duration"),
        source="youtube",
        source_id=video_id,
        locator=_watch_url(video_id),
    )


def _track_from_resolved_info(info: dict[str, Any] | None) -> ResolvedTrack | None:
    if info is None:
        return None
    stream_url = info.get("url")
    if not stream_url:
        # A live stream still buffering, or an extractor result this
        # adapter's single-format `_YDL_OPTS` didn't resolve to a URL --
        # "could not resolve a playable stream" is a plain, expected outcome
        # here, not an error.
        return None
    video_id = str(info.get("id") or "")
    # The resolved video's own ID, never the original `target` -- a search
    # query re-resolved later (`ytsearch1:...`) could land on a DIFFERENT
    # top result; the video this call actually found is what `locator` must
    # keep pointing at.
    locator = _watch_url(video_id) if video_id else stream_url
    return ResolvedTrack(
        title=info.get("title") or info.get("id") or "unknown",
        stream_url=stream_url,
        duration_seconds=info.get("duration"),
        source="youtube",
        source_id=video_id,
        locator=locator,
    )
