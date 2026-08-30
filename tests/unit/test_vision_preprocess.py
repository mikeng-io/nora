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

from nora.infra.vision.preprocess import download_media, preprocess_media

pytest_plugins = ("aiohttp.pytest_plugin",)

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
