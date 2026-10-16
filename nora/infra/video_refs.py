"""Fit a held clip or voice note to the video provider's reference limits, with ffmpeg.

MiniMax H3 takes a reference video or audio only inside fixed ranges: a clip is
MP4 or MOV, H.264 or H.265, AAC or MP3 sound, 2 to 15 s, 256 to
5760 px a side, aspect 0.4 to 2.5, 23.976 to 60 fps, at most 50 MB; audio is WAV or MP3, 2 to 15 s,
at most 15 MB. What people send rarely fits: a voice note is Opus or AAC, a screen recording runs
at 12 fps, a phone clip is a minute long. A clip outside the ranges is not refused at create: the
create answers 200 with a task id and the task fails seconds later (a 12 fps
clip: `video fps 12.000, expected [23.976, 60]`; an `audio/mpeg` data URI: `audio format ".mpeg"
not allowed`). So every reference is re-encoded here, before the create, into one shape that fits:

- **a clip**: the first `seconds` of it, centre-cropped into aspect 0.4 to 2.5, scaled so the long
  side is at most `VIDEO_MAX_EDGE` and the short side at least 256, at a frame rate clamped into
  `VIDEO_FPS_RANGE`, H.264 yuv420p, its sound (if any) as AAC, metadata stripped (a phone clip
  carries its location, the reason `strip_exif` exists for photos, DEC-47), `+faststart`.
- **audio**: the first `seconds`, mono MP3 at 128 kb/s, metadata stripped.

A clip's sound is kept only when the caller says so (`sound`): `video_create` passes it only with
`allow_reference_audio` on, since a clip of someone talking lends their voice exactly as a voice
note does. A clip or audio shorter than `MIN_SECONDS` (for a clip, its picture's own length, not
the container's) is never padded: it is refused (`too_short`), since a loop or silence would be
a reference nobody sent. A clip is cut by frame count as well as by time, so whole frames at a
rate like 29.97 never run it past its seconds (15.015 s for 15).

Both ffprobe and ffmpeg read the held bytes as a local file in a known media container only
(`_INPUT_GUARD`): a blob that is really a playlist (HLS, concat) would otherwise make ffmpeg
open the files or links it names and send what it found to the provider.

The stored blob is never touched; this makes a transient copy for one create call. Never raises:
every failure is a `Fitted` with `data=None` and a `reason`.

The subprocess runner is injectable (`runner`), the test seam `nora.infra.vision.preprocess`
uses; the happy-path tests run real ffmpeg.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger("nora.video_refs")

__all__ = [
    "ASPECT_RANGE",
    "AUDIO_MAX_BYTES",
    "MIN_SECONDS",
    "VIDEO_FPS_RANGE",
    "VIDEO_MAX_BYTES",
    "VIDEO_MAX_EDGE",
    "VIDEO_MIN_EDGE",
    "Fitted",
    "fit_audio",
    "fit_video",
    "video_fps",
    "video_geometry",
]

Runner = Callable[[list[str], float], Awaitable[tuple[int | None, bytes, bytes]]]

#: The provider's per-reference floor, in seconds, for a clip and for audio alike.
MIN_SECONDS = 2.0
#: The output is 480P, so a reference larger than this carries nothing the model can use, only
#: bytes; the provider's own ceiling is 5760.
VIDEO_MAX_EDGE = 1280
VIDEO_MIN_EDGE = 256
ASPECT_RANGE = (0.4, 2.5)
#: Inside the provider's 23.976 to 60: below is duplicated up to 24, above is dropped to 30.
VIDEO_FPS_RANGE = (24.0, 30.0)
VIDEO_MAX_BYTES = 50 * 1024 * 1024
AUDIO_MAX_BYTES = 15 * 1024 * 1024
#: The wall for one ffprobe or one ffmpeg run. A 15 s window of a 480P-bound clip encodes in a
#: few seconds; the desk holds its lock across the create, so a stuck encode must end.
RUN_SECONDS = 60.0

#: The demuxers a held clip or sound may be read with (ffmpeg matches any name in a demuxer's
#: list, so `mov` admits MP4, M4A and MOV, `matroska` admits WebM), and local files as the only
#: protocol. Prepended before every `-i` and on every ffprobe.
_INPUT_GUARD = [
    "-protocol_whitelist", "file",
    "-format_whitelist",
    "mov,matroska,ogg,mp3,wav,aac,flac,avi,mpegts,flv,amr,caf,aiff,asf",
]

Reason = Literal["", "too_short", "no_stream", "unreadable", "too_large"]


@dataclass(frozen=True)
class Fitted:
    """A reference re-encoded to fit (`data`, `mime`, `seconds` actually kept), or `data=None`
    with the `reason` it could not be."""

    data: bytes | None = field(default=None, repr=False)
    mime: str = ""
    seconds: float = 0.0
    reason: Reason = ""


async def _run(args: list[str], timeout_seconds: float) -> tuple[int | None, bytes, bytes]:
    """One subprocess, killed and reaped on every path out."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        log.exception("could not spawn %s", args[0])
        return None, b"", b""
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        log.warning("%s timed out after %.0fs", args[0], timeout_seconds)
        out, err = b"", b""
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await asyncio.shield(proc.wait())
    return proc.returncode, out, err


@dataclass(frozen=True)
class _Probe:
    """`seconds` is the length that matters for the kind: the picture's own for a clip (the
    container's is its longest stream, so a 1.5 s picture under 3 s of sound read as 3 s)."""

    seconds: float
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False


def _rate(text: object) -> float:
    """ffprobe's `num/den` frame rate as a float; 0 when absent or unreadable."""
    num, _, den = str(text or "").partition("/")
    try:
        value = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def _rotation(stream: dict[str, Any]) -> int:
    """The display rotation a phone clip carries (a tag or side data), as degrees."""
    tag = (stream.get("tags") or {}).get("rotate")
    for item in stream.get("side_data_list") or ():
        if isinstance(item, dict) and "rotation" in item:
            tag = item["rotation"]
    try:
        return abs(int(float(tag or 0))) % 180
    except (TypeError, ValueError):
        return 0


async def _probe(run: Runner, path: Path) -> _Probe | None:
    code, out, _ = await run(
        ["ffprobe", "-v", "error", *_INPUT_GUARD, "-print_format", "json", "-show_format",
         "-show_streams", str(path)],
        RUN_SECONDS,
    )
    if code != 0:
        return None
    try:
        info = json.loads(out)
        seconds = float((info.get("format") or {}).get("duration") or 0.0)
    except (ValueError, TypeError, AttributeError):
        return None
    streams = [s for s in info.get("streams") or () if isinstance(s, dict)]
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = any(s.get("codec_type") == "audio" for s in streams)
    if video is None:
        return _Probe(seconds=seconds, has_audio=audio)
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if _rotation(video) == 90:
        # ffmpeg applies the rotation when it decodes, so the frame it hands the filters is
        # the displayed one, turned a quarter.
        width, height = height, width
    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    picture = _stream_seconds(video, fps)
    return _Probe(seconds=min(seconds, picture) if picture > 0 else seconds, width=width,
                  height=height, fps=fps, has_video=True, has_audio=audio)


def _clock_seconds(text: object) -> float:
    """Matroska's `HH:MM:SS.fffffffff` stream tag as seconds; 0 when absent or unreadable."""
    hours, _, rest = str(text or "").rpartition(":")
    hh, _, mm = hours.rpartition(":")
    try:
        return float(hh or 0) * 3600 + float(mm or 0) * 60 + float(rest)
    except ValueError:
        return 0.0


def _stream_seconds(stream: dict[str, Any], fps: float) -> float:
    """A stream's own length: its `duration`, else Matroska's `DURATION` tag (WebM and MKV put
    no duration on a stream), else frames over rate; 0 when none."""
    try:
        own = float(stream.get("duration") or 0.0)
    except (TypeError, ValueError):
        own = 0.0
    if own > 0:
        return own
    tags = stream.get("tags") or {}
    tagged = _clock_seconds(tags.get("DURATION") or tags.get("duration"))
    if tagged > 0:
        return tagged
    try:
        frames = int(stream.get("nb_frames") or 0)
    except (TypeError, ValueError):
        frames = 0
    return frames / fps if frames and fps > 0 else 0.0


def _even(value: float) -> int:
    return max(2, round(value / 2.0) * 2)


def _floor_even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def video_geometry(width: int, height: int) -> tuple[int, int, int, int]:
    """The centre crop (`cw`, `ch`) that brings a frame into `ASPECT_RANGE`, and the size
    (`w`, `h`) it is scaled to: long side at most `VIDEO_MAX_EDGE`, short side at least
    `VIDEO_MIN_EDGE`, both even (yuv420p needs it)."""
    low, high = ASPECT_RANGE
    cw, ch = float(width), float(height)
    if cw / ch > high:
        cw = ch * high
    elif cw / ch < low:
        ch = cw / low
    scale = min(1.0, VIDEO_MAX_EDGE / max(cw, ch))
    if min(cw, ch) * scale < VIDEO_MIN_EDGE:
        scale = VIDEO_MIN_EDGE / min(cw, ch)
    w, h = _even(cw * scale), _even(ch * scale)
    # Rounding each side to even can tip a ratio at the limit just outside it (501x1300 gave
    # 500x1252, 0.3994): shorten the LONG side two pixels at a time until it
    # is back in. The long side is never near the short-side floor, so this cannot break it.
    while w / h > high:
        w -= 2
    while w / h < low:
        h -= 2
    # The crop rounds DOWN to even, so it never asks for a pixel the frame does not have.
    return _floor_even(cw), _floor_even(ch), w, h


def video_fps(source: float) -> float:
    """The frame rate a clip is encoded at: its own, clamped into `VIDEO_FPS_RANGE` (24 when
    ffprobe could not read one)."""
    low, high = VIDEO_FPS_RANGE
    return low if source <= 0 else min(max(source, low), high)


async def _fit(
    data: bytes, *, seconds: float, kind: Literal["video", "audio"], runner: Runner | None,
    sound: bool = False,
) -> Fitted:
    run = runner or _run
    with tempfile.TemporaryDirectory(prefix="nora-ref-") as scratch:
        source = Path(scratch) / "in"
        # A held clip can be tens of MB; the write and the read below stay off the event loop.
        await asyncio.to_thread(source.write_bytes, data)
        probe = await _probe(run, source)
        if probe is None:
            log.warning("a %s reference could not be read by ffprobe", kind)
            return Fitted(reason="unreadable")
        if (kind == "video" and not probe.has_video) or (kind == "audio" and not probe.has_audio):
            return Fitted(reason="no_stream")
        if probe.seconds < MIN_SECONDS:
            return Fitted(reason="too_short", seconds=probe.seconds)
        keep = min(probe.seconds, max(seconds, MIN_SECONDS))
        head = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *_INPUT_GUARD,
                "-i", str(source), "-t", f"{keep:.3f}", "-map_metadata", "-1"]
        if kind == "audio":
            target, mime, ceiling = Path(scratch) / "out.mp3", "audio/mp3", AUDIO_MAX_BYTES
            attempts = [[*head, "-vn", "-ac", "1", "-ar", "44100", "-c:a", "libmp3lame",
                         "-b:a", "128k", str(target)]]
        else:
            target, mime, ceiling = Path(scratch) / "out.mp4", "video/mp4", VIDEO_MAX_BYTES
            cw, ch, w, h = video_geometry(probe.width, probe.height)
            fps = video_fps(probe.fps)
            picture = [
                "-map", "0:v:0",
                "-vf", f"crop={cw}:{ch},scale={w}:{h},fps={fps:g},format=yuv420p",
                # Whole frames only, never past `keep` (`-t` alone rounds up to the next frame),
                # and never under the 2 s floor, which rounding down would cross at a 2 s share
                # and 29.97 fps (59 frames, 1.97 s). A clip that passed the
                # floor has those frames to give.
                "-frames:v", str(max(math.floor(keep * fps), math.ceil(MIN_SECONDS * fps))),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-movflags", "+faststart",
            ]
            track = ["-map", "0:a:0", "-c:a", "aac", "-b:a", "128k"]
            attempts = (
                [[*head, *picture, *track, str(target)]] if probe.has_audio and sound else []
            )
            # A sound track ffmpeg cannot decode fails the whole encode; the picture is still
            # the reference, so the silent encode is the fallback, as in `preprocess`.
            attempts.append([*head, *picture, "-an", str(target)])
        for args in attempts:
            code, _, err = await run(args, RUN_SECONDS)
            if code == 0 and target.exists() and target.stat().st_size > 0:
                out = await asyncio.to_thread(target.read_bytes)
                if len(out) > ceiling:
                    return Fitted(reason="too_large", seconds=keep)
                return Fitted(data=out, mime=mime, seconds=keep)
            log.warning("ffmpeg could not fit a %s reference (rc %s): %s",
                        kind, code, err[-300:].decode("utf-8", "replace"))
        return Fitted(reason="unreadable")


async def fit_video(
    data: bytes, *, seconds: float, sound: bool = False, runner: Runner | None = None,
) -> Fitted:
    """A held clip as an MP4 the provider takes, at most `seconds` long, silent unless `sound`.
    Never raises."""
    try:
        return await _fit(data, seconds=seconds, kind="video", runner=runner, sound=sound)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("fitting a video reference failed")
        return Fitted(reason="unreadable")


async def fit_audio(data: bytes, *, seconds: float, runner: Runner | None = None) -> Fitted:
    """A held voice note or audio as an MP3 the provider takes, at most `seconds` long.
    Never raises."""
    try:
        return await _fit(data, seconds=seconds, kind="audio", runner=runner)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("fitting an audio reference failed")
        return Fitted(reason="unreadable")
