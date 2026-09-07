"""`preprocess_media` — the ffmpeg transport layer (DEC-56 extension).

Pins the fail-soft contract: bytes in, smaller model-ready bytes out for
image/video, ORIGINAL bytes back on any failure, never a raise (R-5). The
runner seam (`runner=`) lets each failure mode be exercised without invoking
real ffmpeg; the happy-path tests run REAL ffmpeg (the container ships it),
so a regression like "forgot to pipe stdin" is caught, not shipped.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from nora.infra.vision.preprocess import (
    VideoCut,
    cut_video,
    download_media,
    preprocess_media,
    probe_duration,
)

# NOTE: no `pytest_plugins = ("aiohttp.pytest_plugin",)` here — the env's
# pytest-aiohttp already provides `aiohttp_server`. Registering aiohttp's own
# plugin from a test module re-registers same-named fixtures SESSION-WIDE
# (module-level pytest_plugins is global once collected), and its `loop`/
# client fixtures are incompatible with pytest-asyncio auto mode — every
# tests/web/ module collected after this one then failed (measured
# 2026-09-02: 62/62 in test_api.py red, green again with the line removed).

# A 1x1 red PNG, decoded ASCII — tiny enough for quick real-ffmpeg runs.
RED_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


async def test_non_media_passthrough():
    assert await preprocess_media(b"<html>hi</html>", "text/html") == b"<html>hi</html>"
    assert await preprocess_media(b"\x00audio", "audio/mp3") == b"\x00audio"


async def test_empty_passthrough():
    assert await preprocess_media(b"", "image/png") == b""


async def test_failed_runner_returns_original():
    async def bad_runner(args, t_seconds, data):
        return None, b"", b""
    data = b"original-bytes"
    out = await preprocess_media(data, "image/png", runner=bad_runner, timeout_seconds=1.0)
    assert out == data


async def test_nonzero_exit_returns_original():
    async def failing_runner(args, t_seconds, data):
        return 1, b"", b"stderr noise"
    data = b"original-bytes"
    out = await preprocess_media(data, "video/mp4", runner=failing_runner, timeout_seconds=1.0)
    assert out == data


async def test_empty_output_returns_original():
    """A 'successful' ffmpeg that emitted nothing is a failure, not compressed
    media — an empty JPEG would be a lie to the model."""
    async def empty_runner(args, t_seconds, data):
        return 0, b"", b""
    data = b"original-bytes"
    out = await preprocess_media(data, "image/jpeg", runner=empty_runner, timeout_seconds=1.0)
    assert out == data


@pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None,
    reason="real ffmpeg not installed in this environment",
)
async def test_real_image_shrinks_to_jpeg():
    # Build a 1600x1600 PNG (2.56 MP — above the 1600px cap's target is moot,
    # but a plausible large still) via ffmpeg lavfi so the happy path is real.
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=2560x1440:rate=1",
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _err = await proc.communicate()
    assert proc.returncode == 0 and out, "could not synthesize a real PNG"

    result = await preprocess_media(out, "image/png")
    assert len(result) < len(out)
    assert result[:3] == b"\xff\xd8\xff"  # JPEG magic
    assert result[-2:] == b"\xff\xd9"     # JPEG EOI marker — a complete file


# ---- download_media (the generic URL surface's network seam) --------------
# Real aiohttp against a local server, with the SSRF screen injectable so
# 127.0.0.1 is reachable in tests (the real screen refuses it, and that
# refusal is itself pinned below via the preview-safety unit in
# `test_preview_safe`).


@pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None,
    reason="real ffmpeg not installed in this environment",
)
async def test_download_happy_path_returns_bytes_and_content_type(aiohttp_server):
    from aiohttp import web

    async def handler(request):
        return web.Response(body=b"jpeg-bytes", content_type="image/jpeg")

    app = web.Application()
    app.router.add_get("/img", handler)
    server = await aiohttp_server(app)

    async def allow(_url: str) -> bool:
        return True

    data, ctype = await download_media(
        f"http://127.0.0.1:{server.port}/img", safe=allow,
    )
    assert data == b"jpeg-bytes"
    assert ctype == "image/jpeg"


async def test_download_follows_relative_redirect(aiohttp_server):
    from aiohttp import web

    async def handler(request):
        return web.Response(body=b"target-bytes", content_type="image/png")

    async def redirect(request):
        return web.HTTPFound("/real.png")

    app = web.Application()
    app.router.add_get("/go", redirect)
    app.router.add_get("/real.png", handler)
    server = await aiohttp_server(app)

    async def allow(_url: str) -> bool:
        return True

    data, ctype = await download_media(
        f"http://127.0.0.1:{server.port}/go", safe=allow,
    )
    assert data == b"target-bytes"
    assert ctype == "image/png"


async def test_download_refuses_a_hop_the_screen_rejects(aiohttp_server):
    """Each redirect hop is re-validated: a fetch that STARTS at an allowed
    host but lands on a screen-refused target comes back empty — the
    external-audit SSRF regression pin (2026-08-31)."""
    from aiohttp import web

    async def redirect(request):
        return web.HTTPFound("http://169.254.169.254/latest/meta-data/")

    app = web.Application()
    app.router.add_get("/meta", redirect)
    server = await aiohttp_server(app)

    allowed = {"http://169.254.169.254/latest/meta-data/"}

    async def screen(url: str) -> bool:
        return url not in allowed

    data, ctype = await download_media(
        f"http://127.0.0.1:{server.port}/meta", safe=screen,
    )
    assert data == b""
    assert ctype == ""


async def test_download_over_ceiling_returns_empty(aiohttp_server):
    from aiohttp import web

    async def handler(request):
        return web.Response(body=b"x" * 4096, content_type="image/png")

    app = web.Application()
    app.router.add_get("/big", handler)
    server = await aiohttp_server(app)

    async def allow(_url: str) -> bool:
        return True

    data, ctype = await download_media(
        f"http://127.0.0.1:{server.port}/big",
        max_bytes=1024, safe=allow,
    )
    assert data == b""
    assert ctype == ""


async def test_download_timeout_returns_empty(aiohttp_server):
    from aiohttp import web

    async def handler(request):
        await asyncio.sleep(5)
        return web.Response(body=b"late", content_type="image/png")

    app = web.Application()
    app.router.add_get("/slow", handler)
    server = await aiohttp_server(app)

    async def allow(_url: str) -> bool:
        return True

    data, ctype = await download_media(
        f"http://127.0.0.1:{server.port}/slow",
        timeout_seconds=0.3, safe=allow,
    )
    assert data == b""
    assert ctype == ""


# ---- the chunked video read (issue #117) -----------------------------------
#
# The bug this closes was never "a clip is bounded" — a bounded read is a
# legitimate policy. It was that NOTHING recorded the cut: no metric, no log
# line, and nothing in the description admitting the clip was longer than what
# was read. A ten-minute video produced a description of its first two minutes,
# phrased as a description of the video. So every test here checks what the cut
# SAYS as well as what it did.


def _runner(*, duration: str | None, chunk: bytes = b"chunk"):
    """An ffmpeg/ffprobe stand-in that records every argv it was handed.

    Recording is the point: `[a for a in calls if "-ss" in a]` is what proves
    the windows are seeked rather than decoded from zero, and a double that
    only returned bytes would make that assertion unwritable.
    """
    calls: list[list[str]] = []

    async def run(args, timeout_seconds, data):
        calls.append(args)
        if args[0] == "ffprobe":
            if duration is None:
                return 1, b"", b"no such file"
            return 0, duration.encode(), b""
        return 0, chunk, b""

    run.calls = calls          # type: ignore[attr-defined]
    return run


async def test_a_short_clip_is_read_whole_and_says_so():
    run = _runner(duration="45.0")

    cut = await cut_video(b"video", chunk_seconds=90, max_chunks=10, runner=run)

    assert len(cut.chunks) == 1
    assert cut.complete is True
    assert cut.capped is False
    assert cut.note() == "", "a whole read has nothing to admit"


async def test_a_long_clip_is_read_in_order_and_seeked_per_window():
    """`-ss` BEFORE `-i` is the difference between a chunk of a ten-minute clip
    costing a second and costing ten — it makes ffmpeg seek the input rather
    than decode and discard everything ahead of the window."""
    run = _runner(duration="300.0")

    cut = await cut_video(b"video", chunk_seconds=90, max_chunks=10, runner=run)

    assert len(cut.chunks) == 4          # 90 + 90 + 90 + 30
    assert cut.complete is True
    starts = [a[a.index("-ss") + 1] for a in run.calls if "-ss" in a]
    assert starts == ["0.000", "90.000", "180.000", "270.000"]
    for args in run.calls:
        if "-ss" in args:
            assert args.index("-ss") < args.index("-i"), "seek the INPUT"


async def test_the_chunks_stay_silent():
    """`-an` strips the audio track, exactly as the single-view path did before
    this change. Issue #117 explicitly does NOT change that: extracting the
    audio and running STT over it was proposed and rejected as scope."""
    run = _runner(duration="100.0")

    await cut_video(b"video", chunk_seconds=90, max_chunks=10, runner=run)

    for args in run.calls:
        if args[0] == "ffmpeg":
            assert "-an" in args


async def test_the_cap_binding_is_recorded_and_said():
    """**The actual fix.** Chunking without a REPORTED ceiling would only move
    the same silent lie to a larger number — so when the ceiling binds, the
    clip's length, how much was read, and the fact that the rest was not, are
    all on the cut, and `note()` is a sentence the answer can carry."""
    run = _runner(duration="1200.0")     # 20 minutes

    cut = await cut_video(b"video", chunk_seconds=90, max_chunks=3, runner=run)

    assert len(cut.chunks) == 3
    assert cut.capped is True
    assert cut.complete is False
    note = cut.note()
    assert "20 minutes" in note
    assert "have not seen the rest" in note


async def test_an_unmeasurable_clip_is_read_once_and_admits_it_does_not_know():
    """ffprobe could not read the duration. UNMEASURABLE is not SHORT: the read
    falls back to one view — the exact pre-#117 behaviour — and `complete` is
    False, so nothing downstream may claim it saw the whole thing. Degrading to
    the old SHAPE is right; degrading to the old SILENCE is what this issue
    exists to end."""
    run = _runner(duration=None)

    cut = await cut_video(b"video", chunk_seconds=90, max_chunks=10, runner=run)

    assert len(cut.chunks) == 1
    assert cut.duration_seconds is None
    assert cut.complete is False
    assert cut.capped is False, "the ceiling did not bind; the clock is unknown"
    assert "may not have watched all of it" in cut.note()


async def test_one_refused_window_shortens_the_read_rather_than_failing_it():
    """Failing the whole read over one bad window would throw away fourteen
    good minutes. The covered time shrinks with it, so what the caller may
    claim to have seen stays honest."""
    calls: list[list[str]] = []

    async def run(args, timeout_seconds, data):
        calls.append(args)
        if args[0] == "ffprobe":
            return 0, b"270.0", b""
        if args[args.index("-ss") + 1] == "90.000":
            return 1, b"", b"broken frame"
        return 0, b"chunk", b""

    cut = await cut_video(b"video", chunk_seconds=90, max_chunks=10, runner=run)

    assert len(cut.chunks) == 2
    assert cut.covered_seconds == 180.0
    assert cut.complete is False


async def test_an_empty_clip_is_not_a_read_at_all():
    cut = await cut_video(b"", runner=_runner(duration="10.0"))
    assert cut == VideoCut(
        chunks=(), duration_seconds=None, covered_seconds=0.0, capped=False,
        reason="empty",
    )


async def test_probe_duration_answers_none_rather_than_zero_when_it_cannot_read():
    """`None` is a real answer and not a zero: «I could not measure it» is what
    makes `cut_video` fall back to one view and SAY it does not know, rather
    than asserting a duration it does not have (R-27)."""
    assert await probe_duration(b"video", runner=_runner(duration=None)) is None
    assert await probe_duration(b"video", runner=_runner(duration="nonsense")) is None
    assert await probe_duration(b"video", runner=_runner(duration="0")) is None
    assert await probe_duration(b"video", runner=_runner(duration="12.5")) == 12.5
