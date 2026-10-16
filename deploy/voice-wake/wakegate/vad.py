"""Speech-or-not per 80 ms chunk — Silero VAD, the model openWakeWord ships.

RMS measures LOUD; the end of an utterance is decided by SPEECH. Under ducked
music the RMS floor sits near 1,000, so a quiet-RMS threshold never sees the
speaker stop, and a second 「喂 Nora」 inside that window would be appended to the
first utterance instead of starting a new one.

Silero VAD answers that question: trained speech-vs-everything-else, ~2 MB,
sub-millisecond per chunk on CPU, and `openwakeword.vad.VAD` already wraps
the ONNX with its recurrent state. A TV, a fan, mic crackle and music are
loud-but-not-speech and read as silence here.

The gate takes this as an injected `speech(chunk) -> bool`, exactly as it
takes the wake scorer — the state machine stays testable without ONNX.
"""

from __future__ import annotations

import numpy as np

from .audio import OWW_CHUNK

_FRAME = 640  # 40 ms: 1280 = 2 x 640, and the Silero ONNX accepts this frame size


class SileroSpeech:
    """`speech(chunk) -> bool` over 80 ms 16 kHz int16 chunks; stateful, so
    one instance per user, `reset()` after each utterance (the recurrent
    state would otherwise carry the last word into the next listening)."""

    def __init__(self, threshold: float = 0.5) -> None:
        from openwakeword.vad import VAD  # onnxruntime import stays out of the test path

        self._vad = VAD()
        self._threshold = threshold
        self.last_probability = 0.0

    def __call__(self, chunk: np.ndarray) -> bool:
        if chunk.size != OWW_CHUNK:
            raise ValueError(f"chunk of {chunk.size}, expected {OWW_CHUNK}")
        self.last_probability = float(self._vad.predict(chunk, frame_size=_FRAME))
        return self.last_probability >= self._threshold

    def reset(self) -> None:
        self._vad.reset_states()
        self.last_probability = 0.0
