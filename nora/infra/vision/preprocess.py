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
extra dependency: the container and host already ship `/usr/bin/ffmpeg`, so this is a
subprocess wrapper, not a pip install.

The layer is a pure view-function: bytes in, bytes out. It does not decide
policy (the per-modality `max_bytes` gates in `[media.describe.*]` still run
afterwards on the RESULT), it never raises into the loop, and on any failure
it returns the input unchanged — a preprocess miss degrades to sending the
original bytes, never to `unavailable`.
That is deliberate: preprocess is a token-saving optimisation, and R-5 says a
quiet "couldn't optimise" beats a fabricated failure. An
unshrinkable input still hits `too_large`.

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
import contextlib
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
# NOT the video read policy; the single-view size for `preprocess_media`, which
# a non-chunking caller (and every image caller) uses. A single view cuts a
# clip to its first 120 seconds, so a partial read must never be served as a
# complete answer (R-5).
#
# `cut_video` is the read policy: transient chunks, in order, each through the
# describer, and a `VideoCut` that SAYS what it did — how long the clip was,
# how much was read, and whether the chunk ceiling bound.
VIDEO_MAX_SECONDS = 120             # one view's worth; see `cut_video`
VIDEO_CRF = 26                      # x264 CRF 26 ≈ visually lossless for sampling
# The audio track's own encode (DEC-197). Named rather than inherited from
# ffmpeg's per-container default, so a clip's token cost does not depend on
# which container it arrived in. AAC 64 kb/s adds ~3.5 % prompt tokens on a
# 35 s clip.
VIDEO_AUDIO_CODEC = "aac"
VIDEO_AUDIO_BITRATE = "64k"
# Stereo at most. Without it a 7.1 source encodes as eight channels sharing the
# same 64 kb/s — ~8 kb/s each, worse speech for the same bytes — and the
# channel count, like the codec above, would come from the container.
VIDEO_AUDIO_CHANNELS = "2"

# Chunk geometry. 1-2 minutes per chunk — 90 s is the middle of that range, and
# the ceiling is what turns an unbounded clip into a bounded number of model
# calls. Both are overridable from `[media.describe.video]`; these are the
# defaults a config without the keys gets.
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
        log.warning("ffmpeg timed out after %.0fs: %s", timeout_seconds, args[:2])
        return None, b"", b""
    finally:
        # A timeout AND a cancellation (the turn's `wait_for` fired, or an
        # Instagram read was stopped) both leave ffmpeg running: `wait_for`
        # only abandons `communicate`. Killed and reaped on every path out.
        await _reap(proc)
    return proc.returncode, out, err


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Kill `proc` if it is still running, and wait for it. Safe to call on
    one that already exited, and shielded, so a second cancellation arriving
    while it waits cannot leave the process behind."""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await asyncio.shield(proc.wait())


async def _run_video(
    args: list[str], timeout_seconds: float, data: bytes
) -> tuple[int | None, bytes, bytes]:
    """Video variant of `_run`: the input bytes land in a scratch file because
    a video container is not seekable over a pipe. The scratch file is removed
    in all paths. The OUTPUT still rides the pipe (fragmented mp4 is written
    sequentially), so only the input needed the file."""
    import tempfile

    # ONE outer try owns the scratch file, so it is removed on every path
    # out: a spawn that fails, a cancellation WHILE spawning, a timeout, a
    # cancellation during the read, and a second cancellation during the
    # (shielded) reap, which raises out of `_reap` before anything after it
    # in the inner `finally` would run.
    scratch: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".input", delete=False) as f:
            scratch = f.name
            f.write(data)
        try:
            proc = await asyncio.create_subprocess_exec(
                *[scratch if a == "pipe:0" else a for a in args],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            log.exception("could not spawn ffmpeg (video)")
            return None, b"", b""
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            log.warning("ffmpeg timed out after %.0fs: %s", timeout_seconds, args[:2])
            out, err = b"", b""
        finally:
            # Every path out, cancellation included: ffmpeg killed and reaped.
            await _reap(proc)
        return proc.returncode, out, err
    finally:
        # The scratch copy of the clip (up to the download ceiling, 64 MiB).
        if scratch is not None:
            with contextlib.suppress(OSError):
                os.unlink(scratch)


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


def _video_args(
    *, start: float = 0.0, seconds: float = VIDEO_MAX_SECONDS, audio: bool = True,
) -> list[str]:
    """Scale + re-encode one window of a clip to ≤720p, ≤24fps, H.264 CRF 26.

    `-ss` BEFORE `-i` is deliberate: it makes ffmpeg seek the input rather than
    decode-and-discard everything ahead of the window, which is the difference
    between a chunk of a ten-minute clip costing a second and costing ten. It
    is also why `_run_video`'s scratch file matters more here than for a single
    view — an input seek needs a seekable input, and a pipe is not one.

    The audio track is KEPT (DEC-197), so she can hear a clip as well as see
    it. A clip with no audio stream still encodes, simply without one — `-c:a`
    names the codec for a stream that exists and asks for none that does not.

    `audio=False` is the silent encode (`-an`), the FALLBACK
    (`_encode_window`): a clip whose selected audio track ffmpeg cannot decode
    fails the audible encode outright, and reads fine with its sound thrown
    away.
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
        *(
            ["-c:a", VIDEO_AUDIO_CODEC, "-b:a", VIDEO_AUDIO_BITRATE,
             "-ac", VIDEO_AUDIO_CHANNELS]
            if audio else ["-an"]
        ),
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov",
        "pipe:1",
    ]


def video_encode_signature() -> str:
    """Every encode setting a video window is cut with, as one stable string.

    A cached reading is a function of the view the model saw, not only of the
    bytes: change the resolution, frame cap, CRF or audio encode and the same
    clip reads differently. This string goes into the reading cache's key
    (DEC-198), so such a change MISSES instead of serving a reading of a view
    that no longer exists.
    """
    return " ".join(_video_args(start=0.0, seconds=float(VIDEO_CHUNK_SECONDS)))


async def _encode_window(
    run: Runner, *, start: float, seconds: float, budget_seconds: float, data: bytes,
) -> tuple[int | None, bytes, bool]:
    """One window, WITH its audio — and silent if the audible encode fails.

    Keeping the audio track must never cost a clip that is readable
    without it (DEC-197): a selected audio stream ffmpeg has no decoder for exits
    non-zero ("Decoding requested, but no decoder found") where `-an` succeeds.
    So a refused audible encode is retried ONCE without audio — the picture is
    still worth reading — and only a clip that fails both ways is refused.
    """
    returncode, out, _err = await run(
        _video_args(start=start, seconds=seconds), budget_seconds, data)
    if returncode == 0 and out:
        return returncode, out, True
    log.warning(
        "ffmpeg refused the audible encode (rc=%s); retrying the window silent",
        returncode,
    )
    returncode, out, _err = await run(
        _video_args(start=start, seconds=seconds, audio=False), budget_seconds, data)
    # The third value says whether the window kept its SOUND. A silent retry
    # can succeed after an audible encode that merely timed out, and the
    # reading of that window is then a reading of the picture alone —
    # `VideoCut.degraded` carries that forward so it is never cached as the
    # audible reading its key promises.
    return returncode, out, False


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
class VideoWindow:
    """One transient chunk, WITH the instant it was cut from.

    The start time rides with the bytes rather than being re-derived from a
    list index: a refused window is DROPPED from the run, so `windows[k]` is
    not the k-th window of the clip once anything has failed. Deriving the
    timecode from the position would label everything after a skip with a time
    it did not come from — a 6-minute clip losing its 1:30 window would render
    the 3:00 content as `[1:30]`, and Nora would say «at 1:30 he does X» about
    something at 3:00.
    """

    start_seconds: float
    seconds: float
    data: bytes


@dataclass(frozen=True)
class VideoCut:
    """What one chunked read of a clip actually covered.

    **Every field exists so the answer can say what it did.** A bounded read is
    a legitimate policy, but one that records nothing would let a two-minute
    read of a ten-minute video come back phrased as a description OF the video.

    `duration_seconds` is `None` when ffprobe could not measure the clip. That
    is NOT the same as a short clip, and `capped` stays False for it: an
    unmeasurable duration means the read cannot claim completeness either way,
    and `note()` says exactly that instead of inventing a number.

    `capped` is True only when the CHUNK CEILING bound. **It is not the only
    way a read comes back short**: ffmpeg refusing a
    window drops that window from `windows` and shortens `covered_seconds`
    while `capped` stays False. `note()` therefore keys on `complete`, not on
    `capped` — `capped` only chooses which sentence.
    """

    windows: tuple[VideoWindow, ...]
    duration_seconds: float | None
    covered_seconds: float
    capped: bool
    reason: str = ""
    # True when this cut is not the one the same bytes would ALWAYS produce:
    # the duration could not be measured, a window was dropped, or a window
    # fell back to a silent encode. Any of those can be a transient ffmpeg
    # failure (a timeout, a killed process) as easily as a fact about the
    # clip, and nothing here can tell them apart — so a reading of a degraded
    # cut is served, but never cached (DEC-198).
    degraded: bool = False

    @property
    def chunks(self) -> tuple[bytes, ...]:
        """The bytes alone, in order. For a caller that genuinely does not need
        the timecodes — nothing that RENDERS a segment may use this."""
        return tuple(w.data for w in self.windows)

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
        plain words, no metric names — because this sentence is what stops a
        partial read being served as a complete answer.

        **Keyed on `complete`, never on `capped`.** Returning `""` whenever the
        ceiling had not bound would let a read shortened by a refused window go
        out with a confident description and no caveat — the shape DEC-161
        exists to end.
        """
        if self.complete:
            return ""
        if self.duration_seconds is None:
            return (
                "(I could not tell how long this clip is, so I may not have "
                "watched all of it.)"
            )
        minutes = round(self.duration_seconds / 60)
        if self.capped:
            return (
                f"(This clip runs about {minutes} minutes and I only watched "
                f"the first {round(self.covered_seconds / 60)} of them — I have "
                f"not seen the rest.)"
            )
        missing = max(0.0, self.duration_seconds - self.covered_seconds)
        return (
            f"(About {max(1, round(missing / 60))} minutes of this "
            f"{minutes}-minute clip would not open at all, so there are parts "
            f"of it I have not seen.)"
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

    Storage is untouched: the encrypted original
    is one blob and is never rewritten, as with `preprocess_media`'s single
    view. These chunks are produced by ffmpeg at read time and
    are discarded by the caller when the read is over.

    Failure is degraded, never raised, and never silently: a clip whose
    duration cannot be probed falls back to ONE `preprocess_media`-shaped view
    and reports `duration_seconds=None`, which `complete` reads as "not known
    to be whole" and `note()` says out loud. A chunk that ffmpeg refuses is
    skipped and shortens `covered_seconds` — the read stays honest about how
    much of the clip actually reached a model.
    """
    if not data:
        return VideoCut(windows=(), duration_seconds=None, covered_seconds=0.0,
                        capped=False, reason="empty")
    chunk_seconds = max(1, chunk_seconds)
    max_chunks = max(1, max_chunks)
    run: Runner = runner if runner is not None else _run_video
    timeout = timeout_seconds if timeout_seconds is not None else PREPROCESS_TIMEOUT_SECONDS
    # ffprobe gets its OWN, shorter budget: reading a container header is not
    # a transcode, so it must not borrow the transcode's 30 s.
    duration = await probe_duration(
        data, runner=runner,
        timeout_seconds=(
            FFPROBE_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        ),
    )

    if duration is None:
        # UNMEASURABLE, not short. One view, and `duration_seconds=None` is
        # what stops the caller claiming it read the whole thing.
        returncode, out, _audible = await _encode_window(
            run, start=0.0, seconds=float(chunk_seconds), budget_seconds=timeout, data=data)
        if returncode != 0 or not out:
            return VideoCut(windows=(), duration_seconds=None, covered_seconds=0.0,
                            capped=False, reason="ffmpeg", degraded=True)
        return VideoCut(
            windows=(VideoWindow(
                start_seconds=0.0, seconds=float(chunk_seconds), data=out),),
            duration_seconds=None, covered_seconds=float(chunk_seconds),
            capped=False, reason="unmeasured", degraded=True,
        )

    wanted = math.ceil(duration / chunk_seconds)
    taken = min(wanted, max_chunks)
    windows: list[VideoWindow] = []
    covered = 0.0
    degraded = False
    for index in range(taken):
        start = float(index * chunk_seconds)
        length = min(float(chunk_seconds), duration - start)
        if length <= 0:
            break
        returncode, out, audible = await _encode_window(
            run, start=start, seconds=length, budget_seconds=timeout, data=data)
        if returncode != 0 or not out:
            degraded = True
            # One refused window is not a refused read. Skipping it keeps the
            # windows that DID reach a model and shortens what the caller may
            # claim to have seen; failing the whole read would throw away
            # fourteen good minutes over one bad one. The GAP is visible
            # because each surviving window carries its own start time — a
            # dropped one does not shift its neighbours' timecodes.
            log.warning("ffmpeg refused video chunk %d (rc=%s)", index, returncode)
            continue
        windows.append(VideoWindow(start_seconds=start, seconds=length, data=out))
        covered += length
        degraded = degraded or not audible
    return VideoCut(
        windows=tuple(windows),
        duration_seconds=duration,
        covered_seconds=covered,
        # The CEILING bound, and ONLY that. A refused window also shortens the
        # read, but it is a different fact — `covered_seconds` carries it and
        # `complete` reads both, which is why `note()` keys on `complete`.
        capped=wanted > max_chunks,
        reason="" if windows else "ffmpeg",
        degraded=degraded,
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
    if top == "video":
        run = runner if runner is not None else _run_video
        returncode, out, _audible = await _encode_window(
            run, start=0.0, seconds=VIDEO_MAX_SECONDS, budget_seconds=timeout, data=data)
    else:
        run = runner if runner is not None else _run
        returncode, out, _err = await run(_image_args(media_type), timeout, data)
    if returncode != 0 or not out:
        # Same contract as the never-raises provider: a failed optimisation
        # passes the ORIGINAL through, so the outer byte gates still bind.
        log.warning("ffmpeg preprocess failed (rc=%s); passing original", returncode)
        return data
    return out


# The generic URL surface of `media_fetch` (DEC-56 extension):
# any tool that surfaces an image/video URL — x_read media rows,
# web_fetch page images — funnels into the same read path.
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
    a bare `current = loc` would turn every
    CDN that redirects that way into `unavailable`. `safe` is injectable for local-server
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
                        # NEW url. `allow_redirects=True` would check
                        # the first url and connect to the last.
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
