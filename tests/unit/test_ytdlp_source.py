"""`YtDlpSource` — the `TrackSource` port's first implementation.

The fake extractor below reproduces yt-dlp's own decision shapes rather than
answering every call the same way (CLAUDE.md's fake-realism rule): a bare
query is dispatched as `ytsearch1:<query>` and returns yt-dlp's real
`{"_type": "playlist", "entries": [...]}` wrapper (the SAME wrapper shape a
real playlist page returns, with more than one entry), a resolve failure
RAISES `yt_dlp.utils.DownloadError` (yt-dlp's real exception, not a generic
one), and a resolved-but-unplayable result (a live stream still buffering)
omits `url` the same way yt-dlp's own info dict can.

Two extractors are exercised separately (`extractor` vs `flat_extractor`,
matching `YtDlpSource.__init__`) because a playlist LISTING and a single
resolve are genuinely different yt-dlp calls (`extract_flat=True` vs
not) — a fake that let one instance answer both would hide a regression
back to eagerly resolving every playlist entry's stream URL.
"""

from __future__ import annotations

import threading
from typing import Any

from yt_dlp.utils import DownloadError

from nora.domain.ports import ResolvedTrack
from nora.infra.music.ytdlp_source import (
    _EXTRACTOR_TTL_SECONDS,
    YtDlpSource,
    _default_extractor,
    _default_flat_extractor,
    _RotatingExtractor,
)


class _FakeExtractor:
    def __init__(
        self,
        *,
        by_url: dict[str, dict[str, Any]] | None = None,
        by_search: dict[str, dict[str, Any]] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.by_url = by_url or {}
        self.by_search = by_search or {}
        self.raises = raises
        self.calls: list[str] = []

    def extract_info(self, target: str, *, download: bool) -> dict[str, Any] | None:
        assert download is False
        self.calls.append(target)
        if self.raises is not None:
            raise self.raises
        if target.startswith("ytsearch1:"):
            return self.by_search.get(target[len("ytsearch1:") :])
        return self.by_url.get(target)


_VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_WATCH_URL = _VIDEO_URL  # what a resolved track's `locator` reconstructs to
_PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL12345"


def _info(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "dQw4w9WgXcQ",
        "title": "Never Gonna Give You Up",
        "duration": 213,
        "url": "https://rr---sn-abc123.googlevideo.com/videoplayback?expire=123",
    }
    base.update(overrides)
    return base


def _flat_entry(video_id: str, title: str, **overrides: Any) -> dict[str, Any]:
    """A flat playlist entry has no `url`/stream info at all -- just enough
    to list it (id, title, maybe duration)."""
    base: dict[str, Any] = {"id": video_id, "title": title}
    base.update(overrides)
    return base


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


async def test_resolves_a_youtube_url_to_a_direct_stream() -> None:
    extractor = _FakeExtractor(by_url={_VIDEO_URL: _info()})
    source = YtDlpSource(extractor=extractor)

    tracks = await source.resolve(query_or_url=_VIDEO_URL)

    assert tracks == (
        ResolvedTrack(
            title="Never Gonna Give You Up",
            stream_url="https://rr---sn-abc123.googlevideo.com/videoplayback?expire=123",
            duration_seconds=213,
            source="youtube",
            source_id="dQw4w9WgXcQ",
            locator=_WATCH_URL,
        ),
    )
    assert extractor.calls == [_VIDEO_URL]


async def test_resolves_a_bare_search_query_via_ytsearch1() -> None:
    extractor = _FakeExtractor(
        by_search={"never gonna give you up": {"_type": "playlist", "entries": [_info()]}}
    )
    source = YtDlpSource(extractor=extractor)

    tracks = await source.resolve(query_or_url="never gonna give you up")

    assert len(tracks) == 1
    assert tracks[0].title == "Never Gonna Give You Up"
    # The locator points at the resolved VIDEO, not the search text -- a
    # later re-resolve of "ytsearch1:..." could land on a different top
    # result, so re-resolving from the queue must never go back through it.
    assert tracks[0].locator == _WATCH_URL
    assert extractor.calls == ["ytsearch1:never gonna give you up"]


async def test_refuses_a_non_youtube_url_without_calling_the_extractor() -> None:
    extractor = _FakeExtractor(by_url={"https://evil.example.com/malware": _info()})
    source = YtDlpSource(extractor=extractor)

    tracks = await source.resolve(query_or_url="https://evil.example.com/malware")

    assert tracks == ()
    assert extractor.calls == []


async def test_returns_empty_when_yt_dlp_raises_download_error() -> None:
    extractor = _FakeExtractor(raises=DownloadError("Private video"))
    source = YtDlpSource(extractor=extractor)

    tracks = await source.resolve(query_or_url=_VIDEO_URL)

    assert tracks == ()


async def test_returns_empty_when_resolved_info_has_no_stream_url() -> None:
    live_info = _info()
    del live_info["url"]
    extractor = _FakeExtractor(by_url={_VIDEO_URL: live_info})
    source = YtDlpSource(extractor=extractor)

    tracks = await source.resolve(query_or_url=_VIDEO_URL)

    assert tracks == ()


async def test_a_playlist_page_url_expands_into_every_track_without_resolving_streams() -> None:
    flat_extractor = _FakeExtractor(
        by_url={
            _PLAYLIST_URL: {
                "_type": "playlist",
                "entries": [
                    _flat_entry("a", "Track A"),
                    _flat_entry("b", "Track B"),
                    _flat_entry("c", "Track C"),
                ],
            },
        },
    )
    full_extractor = _FakeExtractor()
    source = YtDlpSource(extractor=full_extractor, flat_extractor=flat_extractor)

    tracks = await source.resolve(query_or_url=_PLAYLIST_URL)

    assert [t.title for t in tracks] == ["Track A", "Track B", "Track C"]
    assert [t.locator for t in tracks] == [_watch_url("a"), _watch_url("b"), _watch_url("c")]
    # No per-track stream resolution happened -- the whole point of the flat
    # listing (module docstring): a `/music play` on a playlist link must
    # not block on N round-trips before it can even reply.
    assert [t.stream_url for t in tracks] == ["", "", ""]
    assert full_extractor.calls == []
    assert flat_extractor.calls == [_PLAYLIST_URL]


async def test_a_playlist_is_capped_so_one_link_cannot_flood_the_queue() -> None:
    entries = [_flat_entry(str(i), f"Track {i}") for i in range(60)]
    flat_extractor = _FakeExtractor(
        by_url={_PLAYLIST_URL: {"_type": "playlist", "entries": entries}}
    )
    source = YtDlpSource(extractor=_FakeExtractor(), flat_extractor=flat_extractor)

    tracks = await source.resolve(query_or_url=_PLAYLIST_URL)

    assert len(tracks) == 50
    assert tracks[0].title == "Track 0"
    assert tracks[-1].title == "Track 49"


async def test_a_video_url_with_an_incidental_list_param_is_never_expanded() -> None:
    """The exact case `_without_playlist_params` exists for: a video shared
    FROM inside a playlist (or an autoplay Mix) carries `list=`/`index=`
    too, and must resolve to that ONE video, not silently queue the whole
    list — least-surprise for a pasted VIDEO link."""
    video_with_list = f"{_VIDEO_URL}&list=PLsomeone-elses-mix&index=7"
    extractor = _FakeExtractor(by_url={_VIDEO_URL: _info()})
    flat_extractor = _FakeExtractor()
    source = YtDlpSource(extractor=extractor, flat_extractor=flat_extractor)

    tracks = await source.resolve(query_or_url=video_with_list)

    assert len(tracks) == 1
    assert tracks[0].title == "Never Gonna Give You Up"
    # The list/index params were stripped before the extractor ever saw it,
    # and this never went through the flat/playlist path at all.
    assert extractor.calls == [_VIDEO_URL]
    assert flat_extractor.calls == []


async def test_a_playlist_page_with_an_unresolvable_entry_skips_only_that_one() -> None:
    entry_without_id = {"title": "Removed video"}
    flat_extractor = _FakeExtractor(
        by_url={
            _PLAYLIST_URL: {
                "_type": "playlist",
                "entries": [
                    _flat_entry("a", "Track A"),
                    entry_without_id,
                    _flat_entry("c", "Track C"),
                ],
            },
        },
    )
    source = YtDlpSource(extractor=_FakeExtractor(), flat_extractor=flat_extractor)

    tracks = await source.resolve(query_or_url=_PLAYLIST_URL)

    assert [t.title for t in tracks] == ["Track A", "Track C"]


async def test_resolving_a_tracks_locator_later_yields_its_playable_stream() -> None:
    """The lazy-resolve contract end to end: a flat playlist entry's
    `locator`, handed back into `resolve()` (what `MusicPlayer._advance`
    does right before playing it), yields the real stream URL."""
    flat_extractor = _FakeExtractor(
        by_url={_PLAYLIST_URL: {"_type": "playlist", "entries": [_flat_entry("a", "Track A")]}},
    )
    full_extractor = _FakeExtractor(
        by_url={_watch_url("a"): _info(id="a", title="Track A", url="https://cdn.example/a")},
    )
    source = YtDlpSource(extractor=full_extractor, flat_extractor=flat_extractor)

    [listed] = await source.resolve(query_or_url=_PLAYLIST_URL)
    [played] = await source.resolve(query_or_url=listed.locator)

    assert played.stream_url == "https://cdn.example/a"
    assert played.title == "Track A"


async def test_related_resolves_a_mix_seeded_from_the_locators_video() -> None:
    seed_id = "dQw4w9WgXcQ"
    mix_url = f"{_watch_url(seed_id)}&list=RD{seed_id}"
    flat_extractor = _FakeExtractor(
        by_url={
            mix_url: {
                "_type": "playlist",
                "entries": [
                    _flat_entry(seed_id, "Never Gonna Give You Up"),  # the Mix's own seed
                    _flat_entry("next1", "Related Track One"),
                    _flat_entry("next2", "Related Track Two"),
                ],
            },
        },
    )
    source = YtDlpSource(extractor=_FakeExtractor(), flat_extractor=flat_extractor)

    tracks = await source.related(locator=_watch_url(seed_id))

    # The seed video itself is excluded -- autoplay must offer something NEW.
    assert [t.title for t in tracks] == ["Related Track One", "Related Track Two"]
    assert [t.locator for t in tracks] == [_watch_url("next1"), _watch_url("next2")]
    assert flat_extractor.calls == [mix_url]


async def test_related_is_empty_when_the_locator_has_no_video_id() -> None:
    source = YtDlpSource(extractor=_FakeExtractor(), flat_extractor=_FakeExtractor())

    tracks = await source.related(locator="not a youtube watch url")

    assert tracks == ()


async def test_related_returns_empty_rather_than_raising_when_yt_dlp_fails() -> None:
    flat_extractor = _FakeExtractor(raises=DownloadError("boom"))
    source = YtDlpSource(extractor=_FakeExtractor(), flat_extractor=flat_extractor)

    tracks = await source.related(locator=_VIDEO_URL)

    assert tracks == ()


async def test_related_with_no_mix_available_is_empty() -> None:
    """yt-dlp resolving the Mix URL to nothing (no entries, or `None`) is an
    ordinary empty outcome, not an error -- same discipline as an
    unresolvable `resolve()` call."""
    seed_id = "dQw4w9WgXcQ"
    mix_url = f"{_watch_url(seed_id)}&list=RD{seed_id}"
    flat_extractor = _FakeExtractor(by_url={mix_url: {"_type": "playlist", "entries": []}})
    source = YtDlpSource(extractor=_FakeExtractor(), flat_extractor=flat_extractor)

    tracks = await source.related(locator=_watch_url(seed_id))

    assert tracks == ()


# --- extractor rotation ----------------------------------------------------
#
# A `YoutubeDL` owns a private cookiejar holding a negotiated YouTube visitor
# session, and `videoplayback` locators are bound to the session that resolved
# them. `YtDlpSource` is built ONCE at composition, so without rotation its
# extractor ages with the process: past some age every locator it produces is
# answered `403 Forbidden` by the CDN and each failed stream advances the
# queue, draining a guild to silence. These pin the rotation — the delegate is
# reused while it is fresh and thrown away once it is not.


class _CountingBuild:
    """Hands out a distinct extractor per call and counts how many it built —
    a new object IS the observable effect of rotation, the same way a new
    `VISITOR_INFO1_LIVE` is the observable effect in production."""

    def __init__(self) -> None:
        self.built = 0

    def __call__(self) -> _FakeExtractor:
        self.built += 1
        return _FakeExtractor()


def test_the_extractor_is_reused_while_it_is_fresh() -> None:
    build = _CountingBuild()
    now = [1_000.0]
    rotating = _RotatingExtractor(build, ttl_seconds=900.0, monotonic=lambda: now[0])

    rotating.extract_info(_VIDEO_URL, download=False)
    now[0] += 899.0
    rotating.extract_info(_VIDEO_URL, download=False)

    assert build.built == 1


def test_an_extractor_older_than_the_ttl_is_thrown_away() -> None:
    build = _CountingBuild()
    now = [1_000.0]
    rotating = _RotatingExtractor(build, ttl_seconds=900.0, monotonic=lambda: now[0])

    rotating.extract_info(_VIDEO_URL, download=False)
    now[0] += 901.0
    rotating.extract_info(_VIDEO_URL, download=False)
    # The shape of the production failure: a process-lifetime instance.
    now[0] += 21 * 60 * 60
    rotating.extract_info(_VIDEO_URL, download=False)

    assert build.built == 3


def test_concurrent_resolves_do_not_each_build_a_replacement() -> None:
    """`extract_info` runs under `asyncio.to_thread`, so the swap is reachable
    from several worker threads at once; without the lock two of them would
    each build a replacement and one would be dropped mid-use."""
    build = _CountingBuild()
    rotating = _RotatingExtractor(build, ttl_seconds=900.0, monotonic=lambda: 1_000.0)
    start = threading.Barrier(8)

    def resolve() -> None:
        start.wait()
        rotating.extract_info(_VIDEO_URL, download=False)

    threads = [threading.Thread(target=resolve) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert build.built == 1


def test_the_default_extractors_rotate() -> None:
    """Pins the WIRING, not just the class: rotation that `_default_extractor`
    does not actually use repairs nothing, and the tests above would stay
    green even if the default did not rotate."""
    assert isinstance(_default_extractor(), _RotatingExtractor)
    assert isinstance(_default_flat_extractor(), _RotatingExtractor)


def test_the_ttl_stays_inside_the_shortest_youtube_session_cookie() -> None:
    """YouTube's `GPS` session expires at now + 1800s. A TTL at or past that boundary
    lets the session the locators are bound to expire under a reused extractor, which is the
    failure this module rotates to avoid — so the bound is an independent
    number, not this constant restated."""
    assert _EXTRACTOR_TTL_SECONDS < 1_800.0
