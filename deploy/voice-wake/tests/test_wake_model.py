"""The REAL v3 ONNX against REAL clips — the gate's measurement, not its
plumbing (codec/gate have their own tests).

Positive: a Piper-TTS 「喂 Nora」 clip from the training lab (read-only);
it must score above the shipped 0.5 threshold. Negative: background speech
from the same training run must stay below it. The third test drives the
clip through the production stack — model scorer + WakeGate + a silence
tail — and asserts an Utterance actually comes out, which is the property
the live seam depends on.

Skipped when the model file or the lab clips are absent (a fresh clone has
the model; the lab tree only exists on the training host).
"""

import glob
import os
import wave
from pathlib import Path

import numpy as np
import pytest

from wakegate.audio import OWW_CHUNK
from wakegate.gate import WakeGate

MODEL = Path(__file__).parents[1] / "models" / "喂Nora.onnx"
# the training corpus lives OUTSIDE the repo (large); see voice-lab/README.md
LAB_DATA = Path(os.environ.get("VOICE_LAB_DATA", "/home/norty/workspace/voice-lab-data"))
LAB_POSITIVE = LAB_DATA / "oww-data/output/喂Nora/positive_train"
LAB_NEGATIVE = LAB_DATA / "oww-data/output/喂Nora/negative_train"

pytestmark = pytest.mark.skipif(
    not MODEL.is_file() or not LAB_POSITIVE.is_dir(),
    reason="wake model or the voice-lab clips are not present on this host",
)


@pytest.fixture
def model():
    """Function-scoped ON PURPOSE: openWakeWord's preprocessor holds a
    rolling feature window that `reset()` does NOT clear, so a model that
    just scored a positive keeps scoring it for the next ~1.5 s of audio —
    a module-scoped instance bleeds the positive clip INTO the negative
    test (0.985 on background speech). Production avoids this by
    scoring every chunk through the gate (see WakeGate.feed's docstring);
    a test that wants a clean window builds a clean model."""
    from openwakeword.model import Model

    return Model(wakeword_model_paths=[str(MODEL)])


def _pcm16(wav_path: str) -> np.ndarray:
    with wave.open(wav_path, "rb") as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, 16000)
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)


def _peak(model, samples: np.ndarray) -> float:
    model.reset()
    peak = 0.0
    for i in range(0, samples.size - OWW_CHUNK + 1, OWW_CHUNK):
        peak = max(peak, float(list(model.predict(samples[i : i + OWW_CHUNK]).values())[0]))
    return peak


def test_a_tts_喂nora_clip_scores_above_threshold(model):
    clip = sorted(glob.glob(str(LAB_POSITIVE / "*.wav")))[0]
    assert _peak(model, _pcm16(clip)) > 0.5


def test_background_speech_stays_below_threshold(model):
    clip = sorted(glob.glob(str(LAB_NEGATIVE / "*.wav")))[0]
    assert _peak(model, _pcm16(clip)) < 0.5


def test_the_full_gate_path_emits_an_utterance(model):
    clip = sorted(glob.glob(str(LAB_POSITIVE / "*.wav")))[0]
    samples = _pcm16(clip)
    tail = np.zeros(16 * OWW_CHUNK, dtype=np.int16)  # 1.28 s of trailing silence
    gate = WakeGate(
        score=lambda chunk: float(list(model.predict(chunk).values())[0]),
        threshold=0.5,
    )
    utterance = None
    for i in range(0, samples.size, OWW_CHUNK):
        chunk = samples[i : i + OWW_CHUNK]
        if chunk.size < OWW_CHUNK:
            chunk = np.pad(chunk, (0, OWW_CHUNK - chunk.size))
        utterance = gate.feed(chunk) or utterance
    for i in range(0, tail.size, OWW_CHUNK):
        utterance = gate.feed(tail[i : i + OWW_CHUNK]) or utterance
    assert utterance is not None, "the wake clip never completed an utterance"
    assert utterance.peak_score > 0.5
    assert utterance.audio.size >= samples.size
