"""`YtDlpSource` — the `TrackSource` port's first implementation
(design/discord-music/01-playback-and-source.md).

The fake extractor below reproduces yt-dlp's own decision shapes rather than
answering every call the same way (CLAUDE.md's fake-realism rule): a bare
query is dispatched as `ytsearch1:<query>` and returns yt-dlp's real
`{"_type": "playlist", "entries": [...]}` wrapper (the SAME wrapper shape a
real playlist page returns, with more than one entry), a resolve failure
RAISES `yt_dlp.utils.DownloadError` (yt-dlp's real exception, not a generic
one), and a resolved-but-unplayable result (a live stream still buffering)
omits `url` the same way yt-dlp's own info dict can.
"""

from __future__ import annotations

from typing import Any

from yt_dlp.utils import DownloadError

from nora.domain.ports import ResolvedTrack
from nora.infra.music.ytdlp_source import YtDlpSource


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


async def test_a_playlist_page_url_expands_into_every_track() -> None:
    extractor = _FakeExtractor(
        by_url={
            _PLAYLIST_URL: {
                "_type": "playlist",
                "entries": [
                    _info(id="a", title="Track A", url="https://cdn.example/a"),
                    _info(id="b", title="Track B", url="https://cdn.example/b"),
                    _info(id="c", title="Track C", url="https://cdn.example/c"),
                ],
            },
        },
    )
    source = YtDlpSource(extractor=extractor)

    tracks = await source.resolve(query_or_url=_PLAYLIST_URL)

    assert [t.title for t in tracks] == ["Track A", "Track B", "Track C"]
    assert extractor.calls == [_PLAYLIST_URL]


async def test_a_playlist_is_capped_so_one_link_cannot_flood_the_queue() -> None:
    entries = [
        _info(id=str(i), title=f"Track {i}", url=f"https://cdn.example/{i}") for i in range(60)
    ]
    extractor = _FakeExtractor(by_url={_PLAYLIST_URL: {"_type": "playlist", "entries": entries}})
    source = YtDlpSource(extractor=extractor)

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
    source = YtDlpSource(extractor=extractor)

    tracks = await source.resolve(query_or_url=video_with_list)

    assert len(tracks) == 1
    assert tracks[0].title == "Never Gonna Give You Up"
    # The list/index params were stripped before the extractor ever saw it.
    assert extractor.calls == [_VIDEO_URL]


async def test_a_playlist_page_with_an_unresolvable_entry_skips_only_that_one() -> None:
    live_entry = _info(id="live", title="Still buffering")
    del live_entry["url"]
    extractor = _FakeExtractor(
        by_url={
            _PLAYLIST_URL: {
                "_type": "playlist",
                "entries": [
                    _info(id="a", title="Track A", url="https://cdn.example/a"),
                    live_entry,
                    _info(id="c", title="Track C", url="https://cdn.example/c"),
                ],
            },
        },
    )
    source = YtDlpSource(extractor=extractor)

    tracks = await source.resolve(query_or_url=_PLAYLIST_URL)

    assert [t.title for t in tracks] == ["Track A", "Track C"]
