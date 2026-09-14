"""Ducking for Discord voice playback — music attenuates under speech and
restores after (design/voice-channel/01 §3).

Discord's voice player only ever reads from ONE `discord.AudioSource` at a
time (`VoiceClient.play()`/`VoiceClient.source`); it has no native concept of
two sounds overlapping. `DuckSource` generalises the mechanic its sibling
`crossfade.py` shipped — two sources read simultaneously behind the single
source the player sees, blended per 20ms frame — to a different gain law:

- `CrossfadeSource` is A→B handover: `old` ramps 1→0, `new` ramps 0→1, then
  `old` is dropped forever.
- `DuckSource` is A stays, B over it: `music` ramps down to `duck_gain` and
  HOLDS there while `speech` has frames, then ramps back to 1.0 when `speech`
  is exhausted and reading delegates to `music` alone. The ducked source
  survives the whole speech segment; nothing is handed over.

The ramp (default 150ms, `[speech.tts] duck_ramp_ms`) exists because a hard
gain step is audible as a click. The gain is applied to the MUSIC frames
only, INSIDE the mix — `DiscordVoiceSession` still wraps this whole source in
its one outer `PCMVolumeTransformer` at the listener's persisted volume, and
a duck cycle never calls `set_volume()`: the two gains are independent by
construction, and mutating the outer one would clobber the user's setting and
bring the music back wrong (01 §3's called-out trap).

Uses the stdlib `audioop` module for the sample arithmetic — the same spike-
era dependency `crossfade.py` documents: deprecated since 3.11, gone in 3.13,
to be replaced before an upgrade past 3.12 is possible.
"""

from __future__ import annotations

import audioop  # deprecated stdlib module -- see module docstring
import logging

import discord

log = logging.getLogger("nora.music")

_SAMPLE_WIDTH = 2  # 16-bit PCM, discord.opus.Encoder's own format
_FRAME_SECONDS = 0.02  # 20ms frames, discord.opus.Encoder.FRAME_SIZE's own cadence


class DuckSource(discord.AudioSource):
    """Reads `music` and `speech` simultaneously. While `speech` has frames,
    `music` is scaled toward `duck_gain` (a short linear ramp, not a step);
    when `speech` is exhausted, `music` ramps back to 1.0 and reading
    continues from `music` alone. Both inputs are raw (pre-
    `PCMVolumeTransformer`) sources; the caller wraps the WHOLE source in one
    outer volume transformer so a `/music volume` change applies uniformly
    whether or not speech is in progress — and so ducking never touches that
    setting itself."""

    def __init__(
        self, music: discord.AudioSource, speech: discord.AudioSource, *,
        duck_gain: float, ramp_ms: int = 150,
        keep_alive: object | None = None,
    ) -> None:
        # Everything assigned BEFORE the validation raise: `AudioSource.
        # __del__` calls `cleanup()`, so a `__init__` that raises still gets
        # its destructor run against whatever attributes exist.
        self._music = music
        self._speech = speech
        self._speech_done = False
        self._speech_cleaned = False
        if not 0.0 < duck_gain < 1.0:
            raise ValueError(
                f"duck_gain must sit strictly between 0 and 1 (got {duck_gain}) — "
                "0 mutes the music rather than ducking it, 1 ducks nothing"
            )
        self._duck_gain = duck_gain
        self._ramp_frames = max(1, round(ramp_ms / 1000 / _FRAME_SECONDS))
        self._gain = 1.0
        # `keep_alive` is the `PCMVolumeTransformer` that wrapped `music` for
        # playback BEFORE the caller reassigned its `_current_source` to the
        # new wrapper around this mixer. It is never read -- held ONLY so its
        # refcount doesn't hit zero. The bug it prevents is `crossfade.py`'s,
        # verified live 2026-08-24 (fixed in 35c401c2): dropping that
        # wrapper's last reference runs CPython's refcounting `__del__` ->
        # `cleanup()` -> `music.cleanup()` IMMEDIATELY, poisoning this mixer's
        # own `music` source before it reads a single frame, and the failure
        # surfaces as a silent stop that sounds exactly like a hard cut.
        self._keep_alive = keep_alive
        log.info(
            "duck started: gain 1.0 -> %.2f over %s frames (%sms)",
            duck_gain, self._ramp_frames, ramp_ms,
        )

    def is_opus(self) -> bool:
        return False

    def read(self) -> bytes:
        if self._speech_done:
            return self._music.read()
        music_chunk = self._music.read()
        speech_chunk = self._speech.read()
        if not speech_chunk:
            # Speech exhausted. Music ramps back to 1.0 over the same ramp;
            # once there, this source delegates to `music` alone. If MUSIC is
            # exhausted too, a bare EOF is the truth — the player's `after`
            # must fire so the queue advances.
            if not music_chunk:
                return b""
            self._step_gain(up=True)
            restored = audioop.mul(music_chunk, _SAMPLE_WIDTH, self._gain)
            if self._gain >= 1.0:
                log.info("duck restored; continuing from music alone")
                self._finish_speech()
            return restored
        if not music_chunk:
            # The music track hit its own EOF mid-sentence. Returning b""
            # here would cut the speech short AND fire the player's `after`
            # early; play speech alone and let the restore path above meet
            # music's real EOF afterwards, which is where the advance belongs.
            self._step_gain(up=False)
            return speech_chunk
        self._step_gain(up=False)
        ducked = audioop.mul(music_chunk, _SAMPLE_WIDTH, self._gain)
        # `audioop.add` needs equal-length frames; a short read (a source's
        # last frame) pads against silence rather than dropping the frame.
        if len(speech_chunk) < len(ducked):
            speech_chunk += b"\x00" * (len(ducked) - len(speech_chunk))
        elif len(ducked) < len(speech_chunk):
            ducked += b"\x00" * (len(speech_chunk) - len(ducked))
        return audioop.add(ducked, speech_chunk, _SAMPLE_WIDTH)

    def cleanup(self) -> None:
        self._finish_speech()
        self._music.cleanup()

    def _step_gain(self, *, up: bool) -> None:
        step = (1.0 - self._duck_gain) / self._ramp_frames
        if up:
            self._gain = min(1.0, self._gain + step)
        else:
            self._gain = max(self._duck_gain, self._gain - step)

    def _finish_speech(self) -> None:
        self._speech_done = True
        if not self._speech_cleaned:
            self._speech_cleaned = True
            self._speech.cleanup()
