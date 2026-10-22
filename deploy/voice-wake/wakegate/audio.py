"""PCM conditioning for openWakeWord: 48 kHz stereo s16le -> 16 kHz mono int16.

The sidecar's frames are 20 ms of 48 kHz stereo s16le (3840 bytes);
openWakeWord expects 80 ms chunks of 16 kHz mono **int16** PCM. The int16
part is load-bearing: feeding float [-1, 1] silently
truncates every sample to 0 and the model scores nothing — the wake gate
would look healthy and never fire.

Blocks are aligned so the 3:1 downsample is exact: 3840 mono samples at
48 kHz (80 ms) -> 1280 samples at 16 kHz, openWakeWord's own frame size.
"""

from __future__ import annotations

import io
import wave

import numpy as np
from scipy.signal import resample_poly

SOURCE_RATE = 48000
TARGET_RATE = 16000
SOURCE_CHANNELS = 2
SOURCE_FRAME_BYTES = 3840  # 20 ms @ 48 kHz stereo s16le — the sidecar's frame
BLOCK_48K = 3840  # 80 ms of 48 kHz mono samples
OWW_CHUNK = 1280  # 80 ms of 16 kHz mono samples — openWakeWord's input frame
if BLOCK_48K // 3 != OWW_CHUNK:  # a raise, not an assert: `python -O` strips asserts
    raise RuntimeError(f"BLOCK_48K // 3 must equal OWW_CHUNK: {BLOCK_48K // 3} != {OWW_CHUNK}")


def stereo_to_mono(pcm: bytes) -> np.ndarray:
    """One sidecar frame (s16le stereo interleaved) -> 48 kHz mono int16."""
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size % SOURCE_CHANNELS:
        raise ValueError(f"odd sample count {samples.size} — not stereo s16le")
    stereo = samples.reshape(-1, SOURCE_CHANNELS).astype(np.int32)
    # Mean of the two channels in int32, so +32767/+32767 cannot overflow.
    return ((stereo[:, 0] + stereo[:, 1]) // 2).astype(np.int16)


def downsample_to_16k(mono48: np.ndarray) -> np.ndarray:
    """48 kHz mono int16 -> 16 kHz mono int16; length must be a multiple of 3
    (the caller's block alignment — see the module docstring)."""
    if mono48.size % 3:
        raise ValueError(f"block of {mono48.size} is not a multiple of 3")
    out = resample_poly(mono48.astype(np.float32), up=1, down=3)
    return np.clip(np.rint(out), -32768, 32767).astype(np.int16)


def pcm16_to_wav(samples: np.ndarray, *, rate: int = TARGET_RATE) -> bytes:
    """16 kHz mono int16 samples -> a WAV byte string, the `file` part the
    STT service's OpenAI-compatible endpoint expects."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.astype(np.int16).tobytes())
    return buf.getvalue()
