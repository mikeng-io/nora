"""`DuckSource` mixer arithmetic — zero Discord, zero network.

Two synthetic sources stand in for the real ones: a constant-amplitude tone
as music, a short burst as speech. The properties asserted are the three
`crossfade.py`/`duck_source.py` inherit (design/voice-channel/01 §3): the
music is scaled by `duck_gain` WHILE speech has frames, restored to 1.0 after
(along a ramp, not a step), and the mixed frame always carries BOTH sources —
a mixer that silently dropped one would pass a naive "is the music quieter"
check.
"""

from __future__ import annotations

import struct

import discord
import pytest

from nora.platform.discord.duck_source import DuckSource


def _frame(amplitude: int, samples: int = 8) -> bytes:
    return struct.pack("<h", amplitude) * samples


def _first_sample(frame: bytes) -> int:
    return struct.unpack("<h", frame[:2])[0]


class _Tone(discord.AudioSource):
    """A real `AudioSource` subclass emitting `len(amplitudes)` frames of the
    given constant amplitudes, then EOF. `cleanup()` is counted, not omitted —
    `DuckSource`'s own cleanup contract is part of what is under test."""

    def __init__(self, amplitudes: list[int]) -> None:
        self._amplitudes = amplitudes
        self.cleanups = 0

    def read(self) -> bytes:
        if not self._amplitudes:
            return b""
        return _frame(self._amplitudes.pop(0))

    def cleanup(self) -> None:
        self.cleanups += 1


def test_music_is_ducked_during_speech_and_restored_after():
    music = _Tone([1000] * 6)
    speech = _Tone([500] * 3)
    duck = DuckSource(music, speech, duck_gain=0.25, ramp_ms=20)  # 1 frame ramp

    during = [duck.read() for _ in range(3)]
    after = [duck.read() for _ in range(3)]

    # Steady state from the first frame (one-frame ramp): music at 0.25 ->
    # 250, plus the speech's 500. BOTH halves must be in the frame: 750 is
    # neither 250 (speech dropped) nor 500 (music muted) nor 1000 (no duck).
    assert [_first_sample(f) for f in during] == [750, 750, 750]
    # Restored to unity the frame after speech EOF, and delegating to music
    # alone from then on — identical values, and no speech mixed in.
    assert [_first_sample(f) for f in after] == [1000, 1000, 1000]


def test_the_gain_moves_along_a_ramp_not_a_step():
    """A hard step is audible as a click (01 §3). With a 3-frame ramp the
    gain walks 1.0 -> 0.75 -> 0.5 -> 0.25 going down and back up, and the
    music amplitudes prove each intermediate value is actually applied."""
    music = _Tone([1024] * 8)
    speech = _Tone([0] * 4)  # silent speech: the mix reads as pure music
    duck = DuckSource(music, speech, duck_gain=0.25, ramp_ms=60)  # 3 frames

    frames = [_first_sample(duck.read()) for _ in range(8)]

    assert frames == [768, 512, 256, 256, 512, 768, 1024, 1024]


def test_a_music_eof_mid_speech_never_cuts_the_speech():
    """Returning b"" while speech still has frames would end the player AND
    fire `after` early — the queue would advance mid-sentence. Speech plays
    alone until its own end, and the player's EOF comes from the restore
    path meeting music's real EOF afterwards."""
    music = _Tone([1000] * 2)
    speech = _Tone([500] * 4)
    duck = DuckSource(music, speech, duck_gain=0.25, ramp_ms=20)

    frames = [duck.read() for _ in range(5)]

    assert [_first_sample(f) for f in frames[:4]] == [750, 750, 500, 500]
    assert frames[4] == b""


def test_both_sources_exhausted_is_a_real_eof():
    duck = DuckSource(_Tone([]), _Tone([]), duck_gain=0.25, ramp_ms=20)

    assert duck.read() == b""


def test_cleanup_cleans_both_sources_exactly_once():
    music = _Tone([1000] * 4)
    speech = _Tone([500] * 1)
    duck = DuckSource(music, speech, duck_gain=0.25, ramp_ms=20)

    duck.read()           # speech still live
    duck.read()           # speech EOF observed -> speech cleaned on the spot
    assert speech.cleanups == 1
    assert music.cleanups == 0
    duck.cleanup()
    assert speech.cleanups == 1
    assert music.cleanups == 1


def test_keep_alive_is_held_for_the_whole_mix():
    """The previously-playing `PCMVolumeTransformer` must stay referenced for
    as long as the mixer lives — dropping it mid-mix is the CPython `__del__`
    cascade documented in `crossfade.py` (verified live 2026-08-24)."""
    sentinel = object()
    duck = DuckSource(
        _Tone([1000] * 2), _Tone([500] * 1),
        duck_gain=0.25, ramp_ms=20, keep_alive=sentinel,
    )

    assert duck._keep_alive is sentinel
    duck.read()
    assert duck._keep_alive is sentinel


def test_a_duck_gain_outside_the_open_unit_interval_is_refused():
    with pytest.raises(ValueError, match="duck_gain"):
        DuckSource(_Tone([1]), _Tone([1]), duck_gain=0.0)
    with pytest.raises(ValueError, match="duck_gain"):
        DuckSource(_Tone([1]), _Tone([1]), duck_gain=1.0)
