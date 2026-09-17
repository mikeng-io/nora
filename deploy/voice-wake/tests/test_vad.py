"""The real Silero VAD (openWakeWord's bundled ONNX) behind `SileroSpeech`.

Skipped where openwakeword/onnxruntime are absent. Pins the property the gate
relies on: loud non-speech reads as NOT speech, a real 「喂 Nora」 TTS clip
reads as speech, and the gate + real VAD ends an utterance inside the tail
when loud noise follows the last word — the case ducked music defeated.
"""

import glob
import wave
from pathlib import Path

import numpy as np
import pytest
from wakegate.audio import OWW_CHUNK
from wakegate.gate import WakeGate

pytest.importorskip("openwakeword")
from wakegate.vad import SileroSpeech

LAB_POSITIVE = Path("/home/norty/workspace/voice-lab-data/oww-data/output/喂Nora/positive_train")


def _chunks(pcm: np.ndarray):
    n = pcm.size // OWW_CHUNK
    return [pcm[i * OWW_CHUNK:(i + 1) * OWW_CHUNK] for i in range(n)]


def _loud_noise(seconds: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.normal(0, 3000, int(16000 * seconds))).astype(np.int16)  # RMS ~3000: far above 400


def test_loud_white_noise_is_not_speech():
    vad = SileroSpeech(threshold=0.5)
    flags = [vad(c) for c in _chunks(_loud_noise(3.0))]
    assert sum(flags) <= len(flags) // 10, f"noise flagged as speech in {sum(flags)}/{len(flags)}"


def test_digital_silence_is_not_speech():
    vad = SileroSpeech(threshold=0.5)
    assert not any(vad(c) for c in _chunks(np.zeros(16000 * 2, dtype=np.int16)))


@pytest.mark.skipif(not LAB_POSITIVE.is_dir(), reason="voice-lab clips not on this host")
def test_a_tts_wake_clip_reads_as_speech_and_loud_noise_after_it_ends_the_utterance():
    clip = sorted(glob.glob(str(LAB_POSITIVE / "*.wav")))[0]
    with wave.open(clip, "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    vad = SileroSpeech(threshold=0.5)
    speech_chunks = _chunks(pcm)
    heard = sum(vad(c) for c in speech_chunks)
    assert heard >= len(speech_chunks) // 3, "speech clip not heard as speech"
    vad.reset()
    # gate with the REAL vad: fire on the first chunk, speak the clip, then 3 s of loud noise.
    tail = 10  # 0.8 s
    gate = WakeGate(score=lambda _c: 1.0, threshold=0.5, silence_tail_chunks=tail,
                    max_chunks=125, preroll_chunks=4, speech=vad)
    utterance = None
    for c in speech_chunks + _chunks(_loud_noise(3.0, seed=1)):
        utterance = gate.feed(c) or utterance
        if utterance is not None:
            break
    assert utterance is not None, "gate never ended under loud non-speech — the music defect"
    # ended within the tail after the clip, not at the 10 s ceiling
    assert utterance.audio.size <= (len(speech_chunks) + tail + 8) * OWW_CHUNK
