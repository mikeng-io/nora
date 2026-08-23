"""`YtDlpSource` — the `TrackSource` port's first implementation
(design/discord-music/01-playback-and-source.md).

The fake extractor below reproduces yt-dlp's own decision shapes rather than
answering every call the same way (CLAUDE.md's fake-realism rule): a bare
query is dispatched as `ytsearch1:<query>` and returns yt-dlp's real
`{"_type": "playlist", "entries": [...]}` wrapper, a resolve failure RAISES
`yt_dlp.utils.DownloadError` (yt-dlp's real exception, not a generic one),
and a resolved-but-unplayable result (a live stream still buffering) omits
`url` the same way yt-dlp's own info dict can.
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

    track = await source.resolve(query_or_url=_VIDEO_URL)

    assert track == ResolvedTrack(
        title="Never Gonna Give You Up",
        stream_url="https://rr---sn-abc123.googlevideo.com/videoplayback?expire=123",
        duration_seconds=213,
        source="youtube",
        source_id="dQw4w9WgXcQ",
    )
    assert extractor.calls == [_VIDEO_URL]


async def test_resolves_a_bare_search_query_via_ytsearch1() -> None:
    extractor = _FakeExtractor(
        by_search={"never gonna give you up": {"_type": "playlist", "entries": [_info()]}}
    )
    source = YtDlpSource(extractor=extractor)

    track = await source.resolve(query_or_url="never gonna give you up")

    assert track is not None
    assert track.title == "Never Gonna Give You Up"
    assert extractor.calls == ["ytsearch1:never gonna give you up"]


async def test_refuses_a_non_youtube_url_without_calling_the_extractor() -> None:
    extractor = _FakeExtractor(by_url={"https://evil.example.com/malware": _info()})
    source = YtDlpSource(extractor=extractor)

    track = await source.resolve(query_or_url="https://evil.example.com/malware")

    assert track is None
    assert extractor.calls == []


async def test_returns_none_when_yt_dlp_raises_download_error() -> None:
    extractor = _FakeExtractor(raises=DownloadError("Private video"))
    source = YtDlpSource(extractor=extractor)

    track = await source.resolve(query_or_url=_VIDEO_URL)

    assert track is None


async def test_returns_none_when_resolved_info_has_no_stream_url() -> None:
    live_info = _info()
    del live_info["url"]
    extractor = _FakeExtractor(by_url={_VIDEO_URL: live_info})
    source = YtDlpSource(extractor=extractor)

    track = await source.resolve(query_or_url=_VIDEO_URL)

    assert track is None
