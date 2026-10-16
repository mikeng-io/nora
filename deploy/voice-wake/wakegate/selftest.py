"""Boot self-test: prove the wake path can still wake, before a user tries.

Why this exists. A gate can be armed, `/health` can answer 200, frames and
bytes can count up and the model can score every 80 ms chunk while the gate
never fires. The score is the one number that says otherwise, so the gate
demonstrates, once at boot, that a known 「喂 Nora」 crosses its own threshold
THROUGH THE PRODUCTION PATH: the
sidecar's 20 ms 48 kHz stereo frame shape, `stereo_to_mono`, the 3:1
`downsample_to_16k`, the real ONNX model, and `WakeGate` itself. A stub
anywhere would defeat the point: every component can look fine on its own
while the path as a whole cannot wake.

Fail-closed, and the three outcomes stay distinct: PASS means an utterance
came out, FAIL means the audio was scored and did not wake her, ERROR means
the test could not be run at all. ERROR is never PASS. None of them stop the
service: a gate that cannot self-test is still better than no gate, and the
operator's signal is the log line and `/health`, not a crash loop that would
destroy the evidence (the same reason `/health` is liveness, not readiness).
"""

from __future__ import annotations

import logging
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio import BLOCK_48K, SOURCE_CHANNELS, SOURCE_RATE, downsample_to_16k, stereo_to_mono
from .gate import WakeGate

log = logging.getLogger(__name__)

#: Shipped alongside the model and tracked in git for the same reason: the
#: test is worthless if the artefact it asserts against can drift.
DEFAULT_CLIP = Path(__file__).parents[1] / "selftest" / "wake-positive-48k-stereo.wav"


@dataclass(frozen=True)
class SelfTestResult:
    """`status` is one of pass | fail | error — never collapsed to a bool."""

    status: str
    peak: float
    threshold: float
    detail: str
    elapsed_ms: float

    def as_json(self) -> dict:
        return {
            "status": self.status,
            "peak": round(self.peak, 4),
            "threshold": self.threshold,
            "detail": self.detail,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


def _read_48k_stereo(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        if w.getnchannels() != SOURCE_CHANNELS or w.getframerate() != SOURCE_RATE:
            raise ValueError(
                f"clip is {w.getnchannels()}ch @ {w.getframerate()} Hz, "
                f"expected {SOURCE_CHANNELS}ch @ {SOURCE_RATE} Hz"
            )
        if w.getsampwidth() != 2:
            raise ValueError(f"clip is {w.getsampwidth() * 8}-bit, expected 16-bit")
        return w.readframes(w.getnframes())


def run_self_test(
    *,
    score,
    threshold: float,
    clip_path: Path = DEFAULT_CLIP,
) -> SelfTestResult:
    """Drive `clip_path` through the production conditioning and gate.

    `score` is the SAME callable the live gate is built with, so a model that
    loads but cannot score is caught here rather than by a user."""
    started = time.monotonic()

    def done(status: str, peak: float, detail: str) -> SelfTestResult:
        return SelfTestResult(
            status=status,
            peak=peak,
            threshold=threshold,
            detail=detail,
            elapsed_ms=(time.monotonic() - started) * 1000.0,
        )

    try:
        pcm = _read_48k_stereo(clip_path)
    except FileNotFoundError:
        return done("error", 0.0, f"clip {clip_path} is absent")
    except Exception as exc:  # noqa: BLE001 — any unreadable clip is ERROR, not FAIL
        return done("error", 0.0, f"clip {clip_path} unreadable: {exc}")

    # A fresh gate, never the live one: the self-test must not leave a
    # half-collected utterance or a warmed feature window behind it.
    gate = WakeGate(score=score, threshold=threshold)
    peak = 0.0
    woke = False
    try:
        mono48 = stereo_to_mono(pcm)
        blocks = mono48.size // BLOCK_48K
        if blocks == 0:
            return done("error", 0.0, f"clip is shorter than one {BLOCK_48K}-sample block")
        for i in range(blocks):
            block = mono48[i * BLOCK_48K : (i + 1) * BLOCK_48K]
            if gate.feed(downsample_to_16k(block)) is not None:
                woke = True
            peak = max(peak, gate.last_score)
        # The clip ends mid-utterance (there is no trailing silence to count),
        # so the owner's wall-clock end-pointer finishes it, exactly as the
        # live seam does for a burst-gated client.
        if gate.flush() is not None:
            woke = True
    except Exception as exc:  # noqa: BLE001 — a broken path is ERROR, not a quiet FAIL
        return done("error", peak, f"scoring raised: {type(exc).__name__}: {exc}")

    if woke:
        return done("pass", peak, "a known wake clip woke the gate")
    return done(
        "fail",
        peak,
        f"a known wake clip peaked at {peak:.3f}, below the {threshold:.2f} threshold",
    )


def log_self_test(result: SelfTestResult) -> None:
    """One greppable line. `wake selftest` is the whole filter."""
    line = (
        "wake selftest: %s peak=%.3f threshold=%.2f elapsed_ms=%.1f — %s",
        result.status.upper(),
        result.peak,
        result.threshold,
        result.elapsed_ms,
        result.detail,
    )
    if result.status == "pass":
        log.info(*line)
    else:
        # FAIL and ERROR are both operator-facing: the gate is up and cannot
        # be trusted to wake.
        log.error(*line)
