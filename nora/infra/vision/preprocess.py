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
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger("nora.vision.preprocess")

# Where media-request bytes are reduced to, before the model sees them.
# Deliberately modest: vision models are trained on ~1MP inputs; anything
# beyond that is pure token spend for no comprehension gain.
IMAGE_MAX_DIMENSION = 1600          # longest edge, px
IMAGE_JPEG_QUALITY = 85             # q85 vs q100: ~4x smaller, no visible diff
VIDEO_MAX_DIMENSION = 720           # longest edge, px
VIDEO_FPS_CAP = 24                  # ~1.3x token cut vs 30fps, no motion lost
# DEPRECATED as a READ POLICY (issue #117), kept as the single-view fallback.
#
# This constant used to be the whole video story: every clip was cut to its
# first 120 seconds before the model saw it, with no metric, no log line, and
# nothing in the answer admitting the cut — so a ten-minute video produced a
# description of its first two minutes, phrased as a description of the video.
# A partial read served as a complete answer, which is the shape R-5 exists to
# prevent, and chunking WITHOUT a reported cap would only have moved the same
# silent lie to a larger number.
#
# `cut_video` is the read policy now: transient chunks, in order, each through
# the existing describer, and a `VideoCut` that SAYS what it did — how long the
# clip was, how much was read, and whether the chunk ceiling bound. This value
# survives as the single-view size for `preprocess_media`, which is still what
# a non-chunking caller (and every image caller) uses.
VIDEO_MAX_SECONDS = 120             # one view's worth; see `cut_video`
VIDEO_CRF = 26                      # x264 CRF 26 ≈ visually lossless for sampling

# Chunk geometry (issue #117's decision). 1-2 minutes per chunk «picked per
# situation» — 90 s is the middle of the operator's range, and the ceiling is
# what turns an unbounded clip into a bounded number of model calls. Both are
# overridable from `[media.describe.video]`; these are the defaults a config
# written before the keys existed keeps.
VIDEO_CHUNK_SECONDS = 90
VIDEO_MAX_CHUNKS = 10               # 10 x 90 s = 15 minutes of a clip read in full
FFPROBE_TIMEOUT_SECONDS = 10.0

# How long one ffmpeg run may take. A 2K clip scaled to 720p is seconds;
# anything slower is a wedge, and `unavailable` honesty beats a hung turn.
PREPROCESS_TIMEOUT_SECONDS = 30.0

Runner = Callable[[list[str], float, bytes], Awaitable[tuple[int | None, bytes, bytes]]]


async def _run(
    args: list[str], timeout_seconds: float, data: bytes
) -> tuple[int | None, bytes, bytes]:
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


async def _run_video(
    args: list[str], timeout_seconds: float, data: bytes
) -> tuple[int | None, bytes, bytes]:
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


def _image_args(media_type: str) -> list[str]:
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


def _video_args(*, start: float = 0.0, seconds: float = VIDEO_MAX_SECONDS) -> list[str]:
    """Scale + re-encode one window of a clip to ≤720p, ≤24fps, H.264 CRF 26.

    `-ss` BEFORE `-i` is deliberate: it makes ffmpeg seek the input rather than
    decode-and-discard everything ahead of the window, which is the difference
    between a chunk of a ten-minute clip costing a second and costing ten. It
    is also why `_run_video`'s scratch file matters more here than for a single
    view — an input seek needs a seekable input, and a pipe is not one.

    `-an` stays. The video path has stripped the audio track since it shipped,
    the chunks stay silent, and issue #117 explicitly does not change that:
    extracting the audio and running STT over it was proposed and rejected as
    scope.
    """
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}",
        "-i", "pipe:0",
        "-vf", (
            f"scale='min({VIDEO_MAX_DIMENSION},iw)':-2,"
            f"fps={VIDEO_FPS_CAP}"
        ),
        "-t", f"{seconds:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(VIDEO_CRF),
        "-an",
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov",
        "pipe:1",
    ]


def _probe_args() -> list[str]:
    """ffprobe's duration, in seconds, as one bare number on stdout."""
    return [
        "ffprobe", "-hide_banner", "-loglevel", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        "pipe:0",
    ]


async def probe_duration(
    data: bytes, *, runner: Runner | None = None, timeout_seconds: float | None = None,
) -> float | None:
    """How long this clip is, in seconds, or `None` if that cannot be read.

    `None` is a real answer and not a zero: «I could not measure it» is what
    makes `cut_video` fall back to a single view and SAY it could not tell
    whether it read the whole thing, rather than asserting a duration it does
    not have (R-27 — a wiring gap is `unavailable`, never `nothing_found`).
    """
    if not data:
        return None
    run: Runner = runner if runner is not None else _run_video
    timeout = timeout_seconds if timeout_seconds is not None else FFPROBE_TIMEOUT_SECONDS
    returncode, out, _err = await run(_probe_args(), timeout, data)
    if returncode != 0 or not out:
        return None
    try:
        seconds = float(out.decode("ascii", "ignore").strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


@dataclass(frozen=True)
class VideoCut:
    """What one chunked read of a clip actually covered (issue #117).

    **Every field exists so the answer can say what it did.** The bug this
    replaces was not that a clip was truncated — a bounded read is a legitimate
    policy — it was that nothing anywhere recorded the cut, so a two-minute
    read of a ten-minute video came back phrased as a description OF the video.

    `duration_seconds` is `None` when ffprobe could not measure the clip. That
    is NOT the same as a short clip, and `capped` stays False for it: an
    unmeasurable duration means the read cannot claim completeness either way,
    and `note()` says exactly that instead of inventing a number.

    `capped` is True only when the CHUNK CEILING bound — the clip really is
    longer than `covered_seconds` and the rest was never read. That is the flag
    the caller records in `provenance` and states in the description.
    """

    chunks: tuple[bytes, ...]
    duration_seconds: float | None
    covered_seconds: float
    capped: bool
    reason: str = ""

    @property
    def complete(self) -> bool:
        """Whether the whole clip was read. `False` for an unmeasurable clip —
        unknown is not the same as covered, and this property is what a caller
        branches on, so it must fail closed."""
        return (
            not self.capped
            and self.duration_seconds is not None
            and self.covered_seconds + 1.0 >= self.duration_seconds
        )

    def note(self) -> str:
        """One sentence for the description, or `""` when the read was whole.

        Written in the register the transcript and Nora's own reply read in —
        plain words, no metric names — because this sentence is the whole point
        of the change: it is what stops a partial read being served as a
        complete answer.
        """
        if self.complete:
            return ""
        if self.duration_seconds is None:
            return (
                "(I could not tell how long this clip is, so I may not have "
                "watched all of it.)"
            )
        if not self.capped:
            return ""
        return (
            f"(This clip runs about {round(self.duration_seconds / 60)} minutes "
            f"and I only watched the first {round(self.covered_seconds / 60)} "
            f"of them — I have not seen the rest.)"
        )


async def cut_video(
    data: bytes,
    *,
    chunk_seconds: int = VIDEO_CHUNK_SECONDS,
    max_chunks: int = VIDEO_MAX_CHUNKS,
    runner: Runner | None = None,
    timeout_seconds: float | None = None,
) -> VideoCut:
    """The clip as an ORDERED run of transient, model-ready chunks.

    Storage is untouched (issue #117's first decision): the encrypted original
    is one blob and is never rewritten, exactly as `preprocess_media`'s single
    view never rewrote it. These chunks are produced by ffmpeg at read time and
    are discarded by the caller when the read is over.

    Failure is degraded, never raised, and never silently: a clip whose
    duration cannot be probed falls back to ONE `preprocess_media`-shaped view
    and reports `duration_seconds=None`, which `complete` reads as "not known
    to be whole" and `note()` says out loud. A chunk that ffmpeg refuses is
    skipped and shortens `covered_seconds` — the read stays honest about how
    much of the clip actually reached a model.
    """
    if not data:
        return VideoCut(chunks=(), duration_seconds=None, covered_seconds=0.0,
                        capped=False, reason="empty")
    chunk_seconds = max(1, chunk_seconds)
    max_chunks = max(1, max_chunks)
    run: Runner = runner if runner is not None else _run_video
    timeout = timeout_seconds if timeout_seconds is not None else PREPROCESS_TIMEOUT_SECONDS
    duration = await probe_duration(data, runner=runner, timeout_seconds=timeout)

    if duration is None:
        # UNMEASURABLE, not short. One view — the exact pre-#117 behaviour —
        # and `duration_seconds=None` is what stops the caller claiming it read
        # the whole thing. Degrading to the old shape is right; degrading to
        # the old SILENCE is what this issue exists to end.
        returncode, out, _err = await run(
            _video_args(start=0.0, seconds=float(chunk_seconds)), timeout, data)
        if returncode != 0 or not out:
            return VideoCut(chunks=(), duration_seconds=None, covered_seconds=0.0,
                            capped=False, reason="ffmpeg")
        return VideoCut(chunks=(out,), duration_seconds=None,
                        covered_seconds=float(chunk_seconds), capped=False,
                        reason="unmeasured")

    wanted = math.ceil(duration / chunk_seconds)
    taken = min(wanted, max_chunks)
    chunks: list[bytes] = []
    covered = 0.0
    for index in range(taken):
        start = index * chunk_seconds
        window = min(float(chunk_seconds), duration - start)
        if window <= 0:
            break
        returncode, out, _err = await run(
            _video_args(start=start, seconds=window), timeout, data)
        if returncode != 0 or not out:
            # One refused window is not a refused read. Skipping it keeps the
            # chunks that DID reach a model and shortens what the caller may
            # claim to have seen; failing the whole read would throw away
            # fourteen good minutes over one bad one.
            log.warning("ffmpeg refused video chunk %d (rc=%s)", index, returncode)
            continue
        chunks.append(out)
        covered += window
    return VideoCut(
        chunks=tuple(chunks),
        duration_seconds=duration,
        covered_seconds=covered,
        # The CEILING bound, not "a chunk failed". Those are different facts
        # and only the first one means «the rest of this clip exists and was
        # never looked at», which is what the note has to say.
        capped=wanted > max_chunks,
        reason="" if chunks else "ffmpeg",
    )


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
    args = _image_args(media_type) if top == "image" else _video_args()
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
SafeUrl = Callable[[str], Awaitable[bool]]


async def _download(
    url: str,
    max_bytes: int,
    timeout_seconds: float,
    *,
    safe: SafeUrl | None = None,
) -> tuple[bytes, str]:
    """GET `url` with a byte ceiling and timeout. NEVER raises: any failure is
    `(b"", "")`. Returns `(data, content_type)`. Follows at most
    URL_DOWNLOAD_MAX_REDIRECTS, EACH HOP RE-VALIDATED.

    The hop-by-hop SSRF screen is the same one `web_fetch`'s link previews
    run (`nora.infra.preview.next_hop` + `_safe`): a downloader that checked
    only the FIRST url and then followed `Location` blindly would let a
    public page 302 to `http://169.254.169.254/…` or a `file://` target —
    the exact escape the redirect re-validation exists to close. `next_hop`
    also resolves a RELATIVE `Location` against the url it came from, which
    a bare `current = loc` would have broken into `unavailable` for every
    CDN that redirects that way. `safe` is injectable for local-server
    tests (CLAUDE.md test-seam policy); the default is the preview screen.
    """
    import aiohttp

    from ..preview import _safe as _preview_safe
    from ..preview import next_hop as _preview_next_hop

    check = safe if safe is not None else _preview_safe
    current = url
    try:
        async with aiohttp.ClientSession() as session:
            for _ in range(URL_DOWNLOAD_MAX_REDIRECTS + 1):
                if not await check(current):
                    return b"", ""
                async with session.get(
                    current, allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                ) as response:
                    hop = _preview_next_hop(
                        response.status, response.headers.get("Location"), current,
                    )
                    if hop is not None:
                        # Back round the loop, which re-runs `check` on the
                        # NEW url. `allow_redirects=True` would have checked
                        # the first url and connected to the last.
                        current = hop
                        continue
                    if response.status in (301, 302, 303, 307, 308):
                        return b"", ""      # a redirect with no Location goes nowhere
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
    except Exception:            # noqa: BLE001 — never raise from a download; any failure is `(b"", "")`
        return b"", ""


async def download_media(
    url: str,
    *,
    max_bytes: int = URL_DOWNLOAD_MAX_BYTES,
    timeout_seconds: float = URL_DOWNLOAD_TIMEOUT_SECONDS,
    downloader: Downloader | None = None,
    safe: SafeUrl | None = None,
) -> tuple[bytes, str]:
    """Fetch `url` under the byte/time ceilings. NEVER raises (R-5). Returns
    `(data, content_type)`; `(b"", "")` on any failure or over-ceiling body.
    Injection seams mirror the preprocess runner (CLAUDE.md test-seam
    policy); the downloader default is the SSRF-safe hop-validated `_download`."""
    run: Downloader = downloader if downloader is not None else _download
    # `safe` is a concern of the DEFAULT hop-validating downloader only;
    # an injected `downloader` owns its own screen (or is a test fake).
    if downloader is None:
        return await _download(url, max_bytes, timeout_seconds, safe=safe)
    return await run(url, max_bytes, timeout_seconds)
