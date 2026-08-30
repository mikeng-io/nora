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
import hashlib

import pytest

from nora.infra.vision.preprocess import preprocess_media

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
    async def bad_runner(args, timeout, data):
        return None, b"", b""
    data = b"original-bytes"
    out = await preprocess_media(data, "image/png", runner=bad_runner, timeout_seconds=1.0)
    assert out == data


async def test_nonzero_exit_returns_original():
    async def failing_runner(args, timeout, data):
        return 1, b"", b"stderr noise"
    data = b"original-bytes"
    out = await preprocess_media(data, "video/mp4", runner=failing_runner, timeout_seconds=1.0)
    assert out == data


async def test_empty_output_returns_original():
    """A 'successful' ffmpeg that emitted nothing is a failure, not compressed
    media — an empty JPEG would be a lie to the model."""
    async def empty_runner(args, timeout, data):
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
    await asyncio.to_thread(_write, "/tmp/preprocess_out.jpg", result)


def _write(path: str, data: bytes) -> None:
    import os
    with open(path, "wb") as f:
        f.write(data)
    if os.path.exists(path) and not data:
        os.unlink(path)