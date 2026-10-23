#!/usr/bin/env python3
"""Real-audio gate: run the trained 喂Nora ONNX on Miles's 21.2s recording."""
import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "oww-src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "oww-data", "piper-sample-generator"))

import numpy as np
from openwakeword.model import Model

BASE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(BASE)  # voice-lab root (scripts/ → lab root)
onnx = os.path.join(LAB, "oww-data", "output", "喂Nora.onnx")
wav = "/tmp/gate16k.wav"  # ffmpeg-resampled 16k mono of Miles's 21.2s recording

if not os.path.exists(onnx):
    raise FileNotFoundError(f"missing {onnx}")
if not os.path.exists(wav):
    raise FileNotFoundError(f"missing {wav}")
print("onnx:", onnx)
print("wav:", wav)

oww = Model(
    wakeword_models=[onnx],
    inference_framework="onnx",
    # NOTE: training never used Speex NS — evalu8ing with it would be a
    # mismatched front end. Keep False to mirror production preprocessing
    # (sidecar applies VAD/debounce at a higher layer).
)

with wave.open(wav, "rb") as wf:
    sr = wf.getframerate()
    print("sr:", sr)
    raw = wf.readframes(wf.getnframes())

pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

chunk = 512  # 32ms @ 16k
fires = []
for i in range(0, len(pcm) - chunk, chunk):
    seg = pcm[i : i + chunk]
    pred = oww.predict(seg)
    if pred:
        key = list(pred.keys())[0]
        scores = pred[key]
        t = i / sr
        if scores > 0.3:
            fires.append((round(t, 2), round(float(scores), 3)))

print("=== fires (t, score) ===")
for t, s in fires:
    print(f"  {t:.2f}s  {s:.3f}")
print(f"total windows: {len(pcm)//chunk}, fires: {len(fires)}")
