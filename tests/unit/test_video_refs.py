"""`nora.infra.video_refs`: a held clip or voice note fitted to the video provider's reference
limits.

The geometry and frame-rate rules are pure and tested as such. The fits run REAL ffmpeg on
clips ffmpeg generates here, and ffprobe reads back what came out against the provider's
ranges, since the provider refuses an out-of-range reference only at the poll (for example a
12 fps clip).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from nora.infra.video_refs import (
    ASPECT_RANGE,
    VIDEO_FPS_RANGE,
    VIDEO_MAX_EDGE,
    VIDEO_MIN_EDGE,
    fit_audio,
    fit_video,
    video_fps,
    video_geometry,
)

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not _HAS_FFMPEG, reason="real ffmpeg not installed here")


@pytest.mark.parametrize(("width", "height"), [
    (1920, 600), (600, 1920), (3840, 2160), (100, 80), (481, 271), (5000, 300), (1280, 720),
])
def test_the_geometry_always_lands_inside_the_providers_ranges(width, height):
    cw, ch, w, h = video_geometry(width, height)
    assert cw <= width and ch <= height                 # the crop never asks for a pixel
    assert cw % 2 == ch % 2 == w % 2 == h % 2 == 0      # yuv420p
    assert ASPECT_RANGE[0] <= w / h <= ASPECT_RANGE[1]
    assert max(w, h) <= max(VIDEO_MAX_EDGE, VIDEO_MIN_EDGE * ASPECT_RANGE[1] + 2)
    assert min(w, h) >= VIDEO_MIN_EDGE - 1


def test_rounding_to_even_never_tips_the_aspect_out_of_range():
    """The long side is trimmed back without leaving the aspect out of range (501x1300);
    a sweep over sizes, not one example, since the miss lives on a rounding boundary."""
    for width in range(2, 2400, 13):
        for height in (*range(2, 2400, 17), 1300, 1252, 1253):
            _, _, w, h = video_geometry(width, height)
            assert ASPECT_RANGE[0] <= w / h <= ASPECT_RANGE[1], (width, height, w, h)
    assert video_geometry(501, 1300)[2:] == (500, 1250)


def test_a_frame_already_inside_the_ranges_is_not_cropped():
    assert video_geometry(1280, 720) == (1280, 720, 1280, 720)


@pytest.mark.parametrize(("source", "sent"), [
    (12.0, 24.0), (23.976, 24.0), (25.0, 25.0), (29.97, 29.97), (60.0, 30.0), (0.0, 24.0),
])
def test_the_frame_rate_is_clamped_into_range(source, sent):
    assert video_fps(source) == pytest.approx(sent)
    assert VIDEO_FPS_RANGE[0] <= video_fps(source) <= VIDEO_FPS_RANGE[1]


def _make(tmp_path: Path, name: str, *args: str) -> bytes:
    out = tmp_path / name
    subprocess.run(  # noqa: S603 - fixed ffmpeg argv, tmp paths
        ["ffmpeg", "-loglevel", "error", "-y", *args, str(out)], check=True)
    return out.read_bytes()


def _probe(tmp_path: Path, data: bytes, name: str) -> dict:
    path = tmp_path / name
    path.write_bytes(data)
    out = subprocess.run(  # noqa: S603 - fixed ffprobe argv, a tmp path
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         str(path)], check=True, capture_output=True,
    ).stdout
    return json.loads(out)


@needs_ffmpeg
async def test_a_slow_wide_long_clip_comes_out_inside_every_range(tmp_path):
    """A failing shape: 12 fps (refused at the poll), plus a 3.2 aspect and 20 s."""
    clip = _make(tmp_path, "in.mp4", "-f", "lavfi", "-i", "testsrc=size=1920x600:rate=12",
                 "-f", "lavfi", "-i", "sine=d=20", "-t", "20", "-c:v", "libx264",
                 "-c:a", "aac", "-shortest")
    fitted = await fit_video(clip, seconds=6.0, sound=True)
    assert fitted.data is not None and fitted.mime == "video/mp4", fitted
    assert fitted.seconds == pytest.approx(6.0)
    info = _probe(tmp_path, fitted.data, "out.mp4")
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert video["codec_name"] == "h264" and audio["codec_name"] == "aac"
    num, den = video["avg_frame_rate"].split("/")
    assert 23.976 <= int(num) / int(den) <= 60
    assert ASPECT_RANGE[0] <= video["width"] / video["height"] <= ASPECT_RANGE[1]
    assert min(video["width"], video["height"]) >= 256
    assert max(video["width"], video["height"]) <= VIDEO_MAX_EDGE
    assert 2.0 <= float(info["format"]["duration"]) <= 6.1
    assert "tags" not in info["format"] or "location" not in info["format"]["tags"]


@needs_ffmpeg
async def test_a_silent_clip_is_fitted_without_sound(tmp_path):
    clip = _make(tmp_path, "in.mp4", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30",
                 "-t", "3")
    fitted = await fit_video(clip, seconds=6.0)
    assert fitted.data is not None
    info = _probe(tmp_path, fitted.data, "out.mp4")
    assert [s["codec_type"] for s in info["streams"]] == ["video"]
    assert fitted.seconds == pytest.approx(3.0, abs=0.1)


@needs_ffmpeg
async def test_a_voice_note_becomes_a_mono_mp3_cut_to_its_share(tmp_path):
    """Signal and WhatsApp voice notes are Opus or AAC; the provider takes WAV or MP3 only."""
    note = _make(tmp_path, "in.ogg", "-f", "lavfi", "-i", "sine=d=9", "-c:a", "libopus")
    fitted = await fit_audio(note, seconds=4.0)
    assert fitted.data is not None and fitted.mime == "audio/mp3", fitted
    info = _probe(tmp_path, fitted.data, "out.mp3")
    (stream,) = info["streams"]
    assert (stream["codec_name"], stream["channels"]) == ("mp3", 1)
    assert 2.0 <= float(info["format"]["duration"]) <= 4.1


@needs_ffmpeg
async def test_a_clip_under_two_seconds_is_refused_never_padded(tmp_path):
    clip = _make(tmp_path, "in.mp4", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30",
                 "-t", "1")
    fitted = await fit_video(clip, seconds=6.0)
    assert (fitted.data, fitted.reason) == (None, "too_short")


@needs_ffmpeg
async def test_a_voice_note_offered_as_a_clip_has_no_picture(tmp_path):
    note = _make(tmp_path, "in.ogg", "-f", "lavfi", "-i", "sine=d=5", "-c:a", "libopus")
    assert (await fit_video(note, seconds=6.0)).reason == "no_stream"


@needs_ffmpeg
async def test_bytes_that_are_not_media_are_unreadable():
    assert (await fit_video(b"not a clip", seconds=6.0)).reason == "unreadable"
    assert (await fit_audio(b"not a sound", seconds=6.0)).reason == "unreadable"


async def test_a_sound_track_that_will_not_encode_falls_back_to_the_picture_alone():
    """The picture is still the reference, so a broken sound track costs only the sound."""
    calls: list[list[str]] = []
    probe = {"format": {"duration": "5"}, "streams": [
        {"codec_type": "video", "width": 640, "height": 480, "avg_frame_rate": "30/1"},
        {"codec_type": "audio"},
    ]}

    async def runner(args: list[str], wall: float) -> tuple[int | None, bytes, bytes]:
        calls.append(args)
        if args[0] == "ffprobe":
            return 0, json.dumps(probe).encode(), b""
        if "-an" not in args:
            return 1, b"", b"audio decode failed"
        Path(args[-1]).write_bytes(b"\x00\x00\x00\x18ftypisom")  # noqa: ASYNC240 - a fake
        return 0, b"", b""

    fitted = await fit_video(b"clip", seconds=6.0, sound=True, runner=runner)
    assert fitted.data is not None
    assert ["-an" in a for a in calls[1:]] == [False, True]


async def test_a_runner_that_cannot_spawn_is_unreadable_not_a_crash():
    async def runner(args: list[str], wall: float) -> tuple[int | None, bytes, bytes]:
        return None, b"", b""

    assert (await fit_audio(b"x", seconds=6.0, runner=runner)).reason == "unreadable"


@needs_ffmpeg
async def test_a_clip_is_silent_unless_its_sound_may_leave(tmp_path):
    """A clip of someone talking lends their voice as a voice note does, so its sound goes only
    when audio references are on (not when the audio gate is off)."""
    clip = _make(tmp_path, "in.mp4", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30",
                 "-f", "lavfi", "-i", "sine=d=4", "-t", "4", "-c:v", "libx264", "-c:a", "aac")
    silent = await fit_video(clip, seconds=6.0)
    assert silent.data is not None
    assert [s["codec_type"] for s in _probe(tmp_path, silent.data, "s.mp4")["streams"]] == [
        "video"]
    voiced = await fit_video(clip, seconds=6.0, sound=True)
    assert voiced.data is not None
    assert sorted(s["codec_type"] for s in _probe(tmp_path, voiced.data, "v.mp4")["streams"]) == [
        "audio", "video"]


@needs_ffmpeg
async def test_whole_frames_never_run_a_clip_past_its_seconds(tmp_path):
    """`-t 15` at 29.97 fps would give 450 frames, 15.015 s, over the provider's 15 s."""
    clip = _make(tmp_path, "in.mp4", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30000/1001",
                 "-t", "20", "-c:v", "libx264")
    fitted = await fit_video(clip, seconds=15.0)
    assert fitted.data is not None
    (stream,) = _probe(tmp_path, fitted.data, "out.mp4")["streams"]
    assert float(stream["duration"]) <= 15.0


@needs_ffmpeg
async def test_a_short_picture_under_longer_sound_is_too_short(tmp_path):
    """The container's length is its longest stream: 1.5 s of picture under 3 s of sound reads
    as 3 s and passes the floor."""
    clip = _make(tmp_path, "in.mp4", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:d=1.5",
                 "-f", "lavfi", "-i", "sine=d=3", "-c:v", "libx264", "-c:a", "aac")
    assert (await fit_video(clip, seconds=6.0, sound=True)).reason == "too_short"


@needs_ffmpeg
async def test_a_playlist_posing_as_a_clip_is_never_followed():
    """A held blob that is really a playlist would make ffmpeg open what it names and send it
    on. An `ffconcat` list is detected from its bytes alone on ffmpeg 7.1 (the image's) and 9,
    and a relative name passes concat's own safe mode; today that can reach only the scratch
    directory, and HLS is refused for its extension, so ffmpeg's own checks already hold. The
    allowlist is the guard that does not depend on them: this pins that every run carries it
    and that the list is refused at the probe."""
    calls: list[list[str]] = []

    async def recording(args: list[str], wall: float) -> tuple[int | None, bytes, bytes]:
        from nora.infra.video_refs import _run
        calls.append(args)
        return await _run(args, wall)

    playlist = b"ffconcat version 1.0\nfile in\n"
    assert (await fit_video(playlist, seconds=6.0, runner=recording)).reason == "unreadable"
    assert calls and all("-format_whitelist" in a for a in calls)
    assert len(calls) == 1          # refused at the probe: nothing was ever encoded


@needs_ffmpeg
async def test_a_two_second_share_at_29_97_fps_stays_at_the_floor(tmp_path):
    """Three clips under the default 6 s each get 2 s; rounding the frame count down would give 59
    frames, 1.97 s, under the provider's floor."""
    clip = _make(tmp_path, "in.mp4", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30000/1001",
                 "-t", "8", "-c:v", "libx264")
    fitted = await fit_video(clip, seconds=2.0)
    assert fitted.data is not None
    (stream,) = _probe(tmp_path, fitted.data, "out.mp4")["streams"]
    assert float(stream["duration"]) >= 2.0


@needs_ffmpeg
async def test_a_short_webm_picture_under_longer_sound_is_too_short(tmp_path):
    """WebM puts no duration on a stream, so the floor falls back to the container's."""
    clip = _make(tmp_path, "in.webm", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:d=1.5",
                 "-f", "lavfi", "-i", "sine=d=3", "-c:v", "libvpx-vp9", "-deadline", "realtime",
                 "-c:a", "libopus")
    assert (await fit_video(clip, seconds=6.0, sound=True)).reason == "too_short"
