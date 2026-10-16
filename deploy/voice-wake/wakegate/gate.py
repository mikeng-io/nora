"""The per-user wake gate: a two-state machine over 80 ms 16 kHz chunks.

LISTENING — every chunk is scored by the injected scorer (openWakeWord in
production, a stub in tests) and appended to a short pre-roll ring. A score
at or above `threshold` switches to COLLECTING with the pre-roll as the
utterance's head, so the STT transcript carries the 「喂 Nora」 itself and
the reactive path sees the same words a typed message would have carried.

COLLECTING — chunks accumulate until `silence_tail_chunks` consecutive quiet
chunks or `max_chunks`, whichever first; the utterance is then returned and
the gate resets to LISTENING. The silence tail is what lets the user keep
speaking past the wake word — the utterance is 「喂 Nora <command>」, not just
the wake word.

"Quiet" is decided by the injected `speech(chunk) -> bool` detector when one
is given (Silero VAD in production, `vad.py`): a chunk with no speech in it is
quiet however loud the music, TV or mic noise behind it. Without a detector
the gate falls back to loudness — per-chunk RMS below `silence_rms` — which
ducked music defeats: a music floor above the threshold never reads as quiet,
so utterances run long and swallow the next 「喂 Nora」.

The gate counts CHUNKS, so it can only end an utterance while chunks keep
arriving. The Discord client sends packets only while its own mic gate is
open — after the phrase there is nothing to count, and a COLLECTING gate
would wait for the user's NEXT sound. `flush()`
is the owner's wall-clock end-pointer: the service calls it when no frame
has arrived for the silence tail, and the utterance ends on real time.

The scorer is injected (`__init__` only — no test hooks on the instance)
so the state machine is unit-testable without the ONNX model, and the
model is scored without the state machine.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .audio import OWW_CHUNK


@dataclass(frozen=True)
class Utterance:
    """One completed utterance: 16 kHz mono int16 audio (pre-roll included)
    and the peak wake score that triggered collection."""

    audio: np.ndarray
    peak_score: float


class WakeGate:
    def __init__(
        self,
        *,
        score: Callable[[np.ndarray], float],
        threshold: float = 0.5,
        silence_tail_chunks: int = 12,   # 12 × 80 ms = 0.96 s of trailing silence
        max_chunks: int = 125,           # 125 × 80 ms = 10 s utterance ceiling
        preroll_chunks: int = 19,        # ~1.5 s kept so the wake word itself transcribes
        silence_rms: float = 400.0,      # int16 RMS under this reads as silence (fallback)
        speech: Callable[[np.ndarray], bool] | None = None,  # VAD; None = RMS fallback
    ) -> None:
        self._score = score
        self._speech = speech
        self._threshold = threshold
        self._tail = silence_tail_chunks
        self._max = max_chunks
        self._silence_rms = silence_rms
        self._preroll: deque[np.ndarray] = deque(maxlen=preroll_chunks)
        self._collecting = False
        self._buffer: list[np.ndarray] = []
        self._silent_run = 0
        self._peak = 0.0
        self._last_score = 0.0
        self._listening_peak = 0.0

    @property
    def collecting(self) -> bool:
        return self._collecting

    @property
    def last_score(self) -> float:
        """The score of the most recent chunk. Exposed so a caller can tell whether the
        model is seeing near-misses or silence, which need opposite fixes."""
        return self._last_score

    def take_listening_peak(self) -> float:
        """The highest score seen while LISTENING since the last call, and
        reset. The owner samples this to report what the model is actually
        seeing, without a log line per 80 ms chunk."""
        peak, self._listening_peak = self._listening_peak, 0.0
        return peak

    def feed(self, chunk: np.ndarray) -> Utterance | None:
        """One 80 ms (1280-sample) int16 chunk; an `Utterance` the moment one
        completes, else None.

        EVERY chunk is scored, including while COLLECTING, and the score is
        only consulted in LISTENING. The scorer is stateful — openWakeWord's
        preprocessor holds the rolling melspectrogram/embedding window — so
        starving it during collection would freeze the wake word's features
        in that window, and the first chunk scored after the utterance would
        re-fire on audio that is by then seconds of silence (`reset()` clears
        the prediction buffer, not the preprocessor)."""
        if chunk.size != OWW_CHUNK:
            raise ValueError(f"chunk of {chunk.size}, expected {OWW_CHUNK}")
        score = self._score(chunk)
        self._last_score = score
        if not self._collecting:
            self._listening_peak = max(self._listening_peak, score)
            self._preroll.append(chunk)
            if score >= self._threshold:
                self._collecting = True
                self._buffer = list(self._preroll)
                self._silent_run = 0
                self._peak = score
            return None
        self._buffer.append(chunk)
        if self._speech is not None:
            quiet = not self._speech(chunk)
        else:
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
            quiet = rms < self._silence_rms
        self._silent_run = self._silent_run + 1 if quiet else 0
        if self._silent_run >= self._tail or len(self._buffer) >= self._max:
            return self._complete()
        return None

    def flush(self) -> Utterance | None:
        """End the utterance NOW — the owner's wall-clock end-pointer for a
        source that stops sending chunks (a burst-gated client). None when
        the gate is LISTENING: there is nothing to end."""
        if not self._collecting:
            return None
        return self._complete()

    def _complete(self) -> Utterance:
        utterance = Utterance(audio=np.concatenate(self._buffer), peak_score=self._peak)
        self._collecting = False
        self._buffer = []
        self._silent_run = 0
        self._peak = 0.0
        return utterance
