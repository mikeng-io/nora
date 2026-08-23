"""Crossfade mixing for Discord music playback — spike, 2026-08-24
(`[music] crossfade_seconds`, `nora/config.py`).

Discord's voice player only ever reads from ONE `discord.AudioSource` at a
time (`VoiceClient.play()`/`VoiceClient.source`); it has no native concept
of two tracks overlapping. `CrossfadeSource` is what makes that possible:
it wraps the OUTGOING track's still-live source and the INCOMING track's
fresh source behind a single source that reads both every 20ms frame and
blends them with a linear crossfade, then — once the fade window elapses —
delegates purely to the incoming source. `DiscordVoiceSession` swaps this
in via `VoiceClient.source = ...` (a live swap, not a stop/restart), so the
underlying `AudioPlayer` thread never sees a gap to begin with.

Uses the stdlib `audioop` module for the actual sample mixing (fast, C-
implemented) — `audioop` is deprecated since Python 3.11 and gone in 3.13;
this is a spike, not the intended long-term dependency. If crossfading
survives past the spike, replace this with something not on the removal
list (e.g. `array`-based mixing, or a small C extension) before an upgrade
past 3.12 is possible.
"""

from __future__ import annotations

import audioop  # deprecated stdlib module -- see module docstring
import logging

import discord

log = logging.getLogger("nora.music")

_SAMPLE_WIDTH = 2  # 16-bit PCM, discord.opus.Encoder's own format
_FRAME_SECONDS = 0.02  # 20ms frames, discord.opus.Encoder.FRAME_SIZE's own cadence


class CrossfadeSource(discord.AudioSource):
    """Reads `old` and `new` simultaneously for `duration_seconds`, ramping
    `old`'s gain 1→0 and `new`'s gain 0→1 (linear — equal-power would sound
    smoother but this is the spike), then reads purely from `new`. Both
    inputs are raw (pre-`PCMVolumeTransformer`) sources; the caller wraps
    the WHOLE mixer in one outer volume transformer so a `/music volume`
    change applies uniformly whether or not a fade is in progress."""

    def __init__(
        self, old: discord.AudioSource, new: discord.AudioSource, *, duration_seconds: float,
    ) -> None:
        self._old = old
        self._new = new
        self._total_frames = max(1, round(duration_seconds / _FRAME_SECONDS))
        self._frame = 0
        self._old_exhausted = False

    def is_opus(self) -> bool:
        return False

    def read(self) -> bytes:
        if self._old_exhausted:
            return self._new.read()
        old_chunk = self._old.read()
        new_chunk = self._new.read()
        if not old_chunk:
            # The outgoing track hit its own true EOF before the fade
            # window elapsed (a track shorter than expected, or a
            # duration_seconds yt-dlp got slightly wrong) -- nothing left
            # to blend, just hand off to `new` early rather than mixing
            # against silence for the remaining frames.
            self._old_exhausted = True
            self._old.cleanup()
            return new_chunk
        self._frame += 1
        progress = min(1.0, self._frame / self._total_frames)
        if self._frame >= self._total_frames:
            self._old_exhausted = True
            self._old.cleanup()
        new_scaled = (
            audioop.mul(new_chunk, _SAMPLE_WIDTH, progress)
            if new_chunk else b"\x00" * len(old_chunk)
        )
        return audioop.add(
            audioop.mul(old_chunk, _SAMPLE_WIDTH, 1.0 - progress), new_scaled, _SAMPLE_WIDTH,
        )

    def cleanup(self) -> None:
        if not self._old_exhausted:
            self._old.cleanup()
        self._new.cleanup()
