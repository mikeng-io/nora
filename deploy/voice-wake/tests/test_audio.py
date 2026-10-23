"""PCM conditioning: the shapes and the int16 contract the wake model needs.

The one that earns its keep: openWakeWord silently scores ZERO on float
input, so every test here
asserts dtype alongside values — a resampler returning float64 with perfect
values is still a broken gate.
"""

import io
import wave
from pathlib import Path

import numpy as np
import pytest

from wakegate.audio import (
    BLOCK_48K,
    OWW_CHUNK,
    downsample_to_16k,
    pcm16_to_wav,
    stereo_to_mono,
)


def test_stereo_to_mono_averages_channels_without_overflow():
    left = np.array([32767, -32768, 1000], dtype=np.int16)
    right = np.array([32767, -32768, -1000], dtype=np.int16)
    stereo = np.empty(6, dtype=np.int16)
    stereo[0::2], stereo[1::2] = left, right
    mono = stereo_to_mono(stereo.tobytes())
    assert mono.dtype == np.int16
    assert mono.tolist() == [32767, -32768, 0]


def test_stereo_to_mono_refuses_an_odd_byte_count():
    with pytest.raises(ValueError):
        stereo_to_mono(b"\x01\x02\x03")


def test_downsample_block_alignment_is_exact():
    """The service loop's contract: one 80 ms 48 kHz block -> exactly one
    openWakeWord 1280-sample chunk, int16 in and int16 out."""
    t = np.arange(BLOCK_48K) / 48000
    block = (np.sin(2 * np.pi * 440 * t) * 20000).astype(np.int16)
    chunk = downsample_to_16k(block)
    assert chunk.dtype == np.int16
    assert chunk.size == OWW_CHUNK
    # A 440 Hz tone survives a 3:1 polyphase downsample with its amplitude
    # essentially intact (the alternative — decimation by slicing — aliases).
    assert abs(chunk).max() > 18000


def test_downsample_refuses_a_misaligned_block():
    with pytest.raises(ValueError):
        downsample_to_16k(np.zeros(BLOCK_48K - 1, dtype=np.int16))


def test_pcm16_to_wav_round_trips():
    samples = (np.sin(np.arange(1600) / 10) * 10000).astype(np.int16)
    with wave.open(io.BytesIO(pcm16_to_wav(samples)), "rb") as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, 16000)
        back = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    assert back.tolist() == samples.tolist()


def test_a_changed_block_to_chunk_ratio_fails_the_import_under_any_flags():
    # The ratio is checked at import by a raise, not an assert: `python -O`
    # strips asserts, and a changed constant would then ship silently. Mutate
    # the constant in the module's source and import it fresh.
    import wakegate.audio as audio

    src = Path(audio.__file__).read_text(encoding="utf-8")
    mutant = src.replace("OWW_CHUNK = 1280", "OWW_CHUNK = 1281", 1)
    assert mutant != src
    with pytest.raises(RuntimeError, match="OWW_CHUNK"):
        exec(compile(mutant, audio.__file__, "exec"), {"__name__": "wakegate_audio_mutant"})
