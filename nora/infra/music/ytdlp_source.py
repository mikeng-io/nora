"""`YtDlpSource` — the `TrackSource` port's yt-dlp implementation.

Resolves a query or URL to one or more direct, streamable media URLs
WITHOUT downloading (`extract_info(..., download=False)`). The returned `url`s
are handed to `ffmpeg` by the caller;
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
(`MusicPlayer._advance`). This is why
`ResolvedTrack.locator` exists: it is always a re-derivable `query_or_url`
for that specific track, so a later `resolve()` call gets the actual
stream URL back.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol
from urllib.parse import SplitResult, parse_qs, urlencode, urlsplit, urlunsplit

import yt_dlp

from nora.domain.ports import ResolvedTrack

log = logging.getLogger("nora.music")

# The allowlist is enforced HERE, before the extractor is ever called — a
# pasted URL is untrusted input, and this must not become "whatever yt-dlp's
# extractor set happens to accept".
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
    # `Task.cancel()` on the coroutine awaiting an extraction does NOT stop the
    # underlying thread (`to_thread` has no forced interrupt), so a stale
    # prewarm/retry keeps a thread-pool slot pinned until the request itself
    # gives up. Without a bound, enough of these pile up to starve the default
    # executor and silently stall every later autoplay attempt.
    "socket_timeout": 20,
}


class _Extractor(Protocol):
    def extract_info(self, url: str, *, download: bool) -> Any: ...


# How long one `YoutubeDL` may be reused before it is thrown away and rebuilt.
#
# A `YoutubeDL` owns a private cookiejar holding a negotiated YouTube visitor
# session (`VISITOR_INFO1_LIVE`, `YSC`, `GPS`), and `videoplayback` locators are
# bound to the session that resolved them. A long-lived instance's session rots:
# its locators then draw `HTTP error 403` from the googlevideo CDN, ffmpeg exits
# 1, and a failed stream advances the queue (`nora/app/voice_events.py`).
# Rebuilding negotiates a fresh session.
#
# The shortest session cookie (`GPS`) lives 30 minutes; 15 leaves margin inside
# that and costs at most one extra session negotiation per quarter hour in which
# music is played. This is not the player-JS cache: the image ships no JS runtime
# for yt-dlp to use, so that cache stays empty.
_EXTRACTOR_TTL_SECONDS = 900.0


class _RotatingExtractor:
    """A `_Extractor` that throws its delegate away once it is older than
    `ttl_seconds`, so a process-lifetime source can never serve locators from
    a stale YouTube visitor session (`_EXTRACTOR_TTL_SECONDS`).

    Only the DEFAULT extractors rotate. An injected one (tests, or a caller
    with its own lifecycle) is held as-is by `YtDlpSource.__init__` and never
    rebuilt — rotation is this module's own repair, not a contract it imposes
    on whatever it is handed."""

    def __init__(
        self,
        build: Callable[[], _Extractor],
        *,
        ttl_seconds: float = _EXTRACTOR_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._build = build
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        # `extract_info` runs under `asyncio.to_thread`, so the swap is
        # reachable from several worker threads at once: without the lock two
        # concurrent resolves could each build a replacement and one would be
        # dropped on the floor mid-use. Building a `YoutubeDL` touches no
        # network, so the lock is held only across the decision.
        self._lock = threading.Lock()
        self._delegate: _Extractor | None = None
        self._built_at = 0.0

    def _current(self) -> _Extractor:
        with self._lock:
            now = self._monotonic()
            if self._delegate is None or now - self._built_at >= self._ttl_seconds:
                # The retired instance is dropped, never `close()`d: a resolve
                # already in flight on a worker thread still holds it, and
                # closing its opener underneath that call would turn a routine
                # rotation into the very failure this exists to prevent.
                self._delegate = self._build()
                self._built_at = now
            return self._delegate

    def invalidate(self) -> None:
        """Drop the delegate NOW, so the next call builds a fresh one rather
        than waiting out the remaining TTL. Called when a locator this
        extractor produced turned out to be unplayable: the TTL bounds how
        long a session may rot unnoticed, but once something has actually
        failed there is no reason to keep resolving through it."""
        with self._lock:
            self._delegate = None

    def extract_info(self, url: str, *, download: bool) -> Any:
        return self._current().extract_info(url, download=download)


def _default_extractor() -> _Extractor:
    # `_YDL_OPTS`'s TypedDict stub demands an exact `_Params` shape; the
    # documented `yt_dlp.YoutubeDL` API is "pass it a dict of option names"
    # (same argument as `nora/infra/vision/client.py`'s own `type: ignore`
    # for a gateway whose contract is the wire JSON, not the SDK's stub).
    return _RotatingExtractor(lambda: yt_dlp.YoutubeDL(_YDL_OPTS))  # type: ignore[arg-type]


def _default_flat_extractor() -> _Extractor:
    return _RotatingExtractor(lambda: yt_dlp.YoutubeDL(_FLAT_YDL_OPTS))  # type: ignore[arg-type]


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

    async def refresh_session(self) -> None:
        """Discard the negotiated YouTube visitor session (`TrackSource.
        refresh_session`) by dropping both rotating extractors, so the next
        resolve signs its locators with a new one.

        An INJECTED extractor is deliberately untouched: it owns its own
        lifecycle (`_RotatingExtractor`'s docstring), and this adapter has
        no business reaching into something it was handed. That makes this a
        no-op under test, which is correct — a fake extractor has no session
        to rot."""
        for extractor in (self._extractor, self._flat_extractor):
            if isinstance(extractor, _RotatingExtractor):
                extractor.invalidate()


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
    """A real URL must be YouTube (`_ALLOWED_HOSTS`); anything else is
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
