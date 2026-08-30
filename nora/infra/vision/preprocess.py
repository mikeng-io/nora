"""Preprocess media bytes before they reach a vision model (DEC-56 READ half).

A blob's bytes are custody: the encrypted original is what the store holds and
what an audit reads, and it is NEVER rewritten. This module sits between the
decrypt and the vision call — the transport layer — producing a smaller,
model-ready view of the SAME media so the vision request carries fewer bytes
(and fewer tokens) without touching the stored original.

Why this exists: Signal/Discord routinely deliver 2K/4K stills and large
clips. Sending those straight to a vision model is waste — the model cannot
"see" more of a picture than the pixels it is given, and a 4K still costs
many times the tokens of a 1600px one for the same answer. FFmpeg is not an
extra dependency: the container and host already ship `/usr/bin/ffmpeg`
(verified 2026-08-31), so this is a subprocess wrapper, not a pip install.

The layer is a pure view-function: bytes in, bytes out. It does not decide
policy (the per-modality `max_bytes` gates in `[media.describe.*]` still run
afterwards on the RESULT), it never raises into the loop, and on any failure
it returns the input unchanged — a preprocess miss degrades to the exact
behaviour of the build before this module existed, never to `unavailable`.
That is deliberate: preprocess is a token-saving optimisation, and R-5 says a
quiet "couldn't optimise" beats a fabricated failure. The byte gates are
unchanged, so an unshrinkable input still hits `too_large` exactly as before.

The failsafe asymmetry is load-bearing: we FORGIVE a failed image/video call
(the original bytes are valid media), but we never let a "successful" call
emit something that is no longer the media the user asked about. If ffmpeg
says it succeeded but the output is empty, that IS a failure signal and we
fall back — an empty JPEG is not "compressed media", it is a lie.

CLAUDE.md test-seam policy: the subprocess runner is injectable (`runner`
parameter), like `bridge_server._spawn_opencli`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Awaitable
from typing import Any

log = logging.getLogger("nora.vision.preprocess")

# Where media-request bytes are reduced to, before the model sees them.
# Deliberately modest: vision models are trained on ~1MP inputs; anything
# beyond that is pure token spend for no comprehension gain.
IMAGE_MAX_DIMENSION = 1600          # longest edge, px
IMAGE_JPEG_QUALITY = 85             # q85 vs q100: ~4x smaller, no visible diff
VIDEO_MAX_DIMENSION = 720           # longest edge, px
VIDEO_FPS_CAP = 24                  # ~1.3x token cut vs 30fps, no motion lost
VIDEO_MAX_SECONDS = 120             # cap a long clip the model would sample anyway
VIDEO_CRF = 26                      # x264 CRF 26 ≈ visually lossless for sampling

# How long one ffmpeg run may take. A 2K clip scaled to 720p is seconds;
# anything slower is a wedge, and `unavailable` honesty beats a hung turn.
PREPROCESS_TIMEOUT_SECONDS = 30.0

Runner = Callable[[list[str], float, bytes], Awaitable[tuple[int | None, bytes, bytes]]]


async def _run(args: list[str], timeout_seconds: float, data: bytes) -> tuple[int | None, bytes, bytes]:
    """One ffmpeg invocation as an argv vector. NEVER a shell. Never raises:
    a missing binary or a timeout is `(None, b"", b"")`. The media bytes are
    piped to ffmpeg's stdin (`-i pipe:0`), never written to a temp file.
    Image inputs take this path; video inputs can not (a container whose moov
    atom sits at EOF needs a seekable input, which a pipe is not), so video
    goes through `_run_video` with a scratch file instead.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        log.exception("could not spawn ffmpeg")
        return None, b"", b""
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input=data), timeout=timeout_seconds
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("ffmpeg timed out after %.0fs: %s", timeout_seconds, args[:2])
        return None, b"", b""
    return proc.returncode, out, err


async def _run_video(args: list[str], timeout_seconds: float, data: bytes) -> tuple[int | None, bytes, bytes]:
    """Video variant of `_run`: the input bytes land in a scratch file because
    a video container is not seekable over a pipe. The scratch file is removed
    in all paths. The OUTPUT still rides the pipe (fragmented mp4 is written
    sequentially), so only the input needed the file."""
    import tempfile

    scratch: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".input", delete=False) as f:
            f.write(data)
            scratch = f.name
        proc = await asyncio.create_subprocess_exec(
            *[scratch if a == "pipe:0" else a for a in args],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        log.exception("could not spawn ffmpeg (video)")
        if scratch is not None:
            await asyncio.to_thread(os.unlink, scratch)
        return None, b"", b""
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("ffmpeg timed out after %.0fs: %s", timeout_seconds, args[:2])
        out, err = b"", b""
    if scratch is not None:
        await asyncio.to_thread(os.unlink, scratch)
    return proc.returncode, out, err


def _image_args(media_type: str, timeout_seconds: float) -> list[str]:
    """Scale + re-encode a still image to JPEG at IMAGE_MAX_DIMENSION."""
    # `-vf scale='min(1600,iw)':-2` shrinks only when wider than the cap;
    # small sources pass through at native size (no needless re-encode loss).
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-vf", f"scale='min({IMAGE_MAX_DIMENSION},iw)':-2",
        "-q:v", str(IMAGE_JPEG_QUALITY),
        "-frames:v", "1",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "pipe:1",
    ]


def _video_args(timeout_seconds: float) -> list[str]:
    """Scale + re-encode a clip to ≤720p, ≤24fps, ≤120s, H.264 CRF 26."""
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-vf", (
            f"scale='min({VIDEO_MAX_DIMENSION},iw)':-2,"
            f"fps={VIDEO_FPS_CAP}"
        ),
        "-t", str(VIDEO_MAX_SECONDS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(VIDEO_CRF),
        "-an",
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov",
        "pipe:1",
    ]


async def preprocess_media(
    data: bytes,
    media_type: str,
    *,
    runner: Runner | None = None,
    timeout_seconds: float | None = None,
) -> bytes:
    """Return a model-ready view of `data`, or `data` unchanged on any failure.

    Image inputs are scaled to IMAGE_MAX_DIMENSION and re-encoded as JPEG;
    video inputs are scaled to VIDEO_MAX_DIMENSION at VIDEO_FPS_CAP and capped
    at VIDEO_MAX_SECONDS. Anything else (or any ffmpeg failure, timeout, or
    empty-output) returns the original bytes — never raises (R-5).
    """
    if not data:
        return data
    top = str(media_type).partition("/")[0].lower()
    if top != "image" and top != "video":
        return data
    timeout = timeout_seconds if timeout_seconds is not None else PREPROCESS_TIMEOUT_SECONDS
    run: Runner = runner if runner is not None else _run
    args = _image_args(media_type, timeout) if top == "image" else _video_args(timeout)
    if top == "video":
        run = runner if runner is not None else _run_video
    else:
        run = runner if runner is not None else _run
    returncode, out, _err = await run(args, timeout, data)
    if returncode != 0 or not out:
        # Same contract as the never-raises provider: a failed optimisation
        # passes the ORIGINAL through, so the outer byte gates still bind.
        log.warning("ffmpeg preprocess failed (rc=%s); passing original", returncode)
        return data
    return out


# The generic URL surface of `media_fetch` (DEC-56 extension, 2026-08-31):
# any tool that surfaces an image/video URL — social_fetch media rows,
# web_fetch page images, a future tool — funnels into the same read path.
# This downloader is the bound on the FIRST byte hop, so a hostile or
# enormous remote cannot silently blow the turn: a hard byte ceiling and a
# hard timeout, both far below the loop's own budgets, and a redirect cap.
URL_DOWNLOAD_MAX_BYTES = 64 * 1024 * 1024      # 64 MiB — a real clip's worth
URL_DOWNLOAD_TIMEOUT_SECONDS = 25.0            # < loop budget; a hang is a wedge
URL_DOWNLOAD_MAX_REDIRECTS = 5

Downloader = Callable[[str, int, float], Awaitable[tuple[bytes, str]]]


async def _download(url: str, max_bytes: int, timeout_seconds: float) -> tuple[bytes, str]:
    """GET `url` with a byte ceiling and timeout. NEVER raises: any failure is
    `(b"", "")`. Returns `(data, content_type)`. Follows at most
    URL_DOWNLOAD_MAX_REDIRECTS."""
    import aiohttp

    current = url
    try:
        async with aiohttp.ClientSession() as session:
            for _ in range(URL_DOWNLOAD_MAX_REDIRECTS + 1):
                async with session.get(
                    current, timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                ) as response:
                    if response.status >= 300 and response.status < 400:
                        loc = response.headers.get("Location")
                        if not loc:
                            return b"", ""
                        current = loc
                        continue
                    if response.status != 200:
                        return b"", ""
                    content_type = response.headers.get("Content-Type", "")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            return b"", ""
                        chunks.append(chunk)
                    return b"".join(chunks), content_type
            return b"", ""
    except Exception:
        return b"", ""


async def download_media(
    url: str,
    *,
    max_bytes: int = URL_DOWNLOAD_MAX_BYTES,
    timeout_seconds: float = URL_DOWNLOAD_TIMEOUT_SECONDS,
    downloader: Downloader | None = None,
) -> tuple[bytes, str]:
    """Fetch `url` under the byte/time ceilings. NEVER raises (R-5). Returns
    `(data, content_type)`; `(b"", "")` on any failure or over-ceiling body.
    Injection seam mirrors the preprocess runner (CLAUDE.md test-seam policy)."""
    run: Downloader = downloader if downloader is not None else _download
    return await run(url, max_bytes, timeout_seconds)