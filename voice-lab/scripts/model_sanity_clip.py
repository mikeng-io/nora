#!/usr/bin/env python3
"""Re-test the trained model with the OFFICIAL clip-level API (predict_clip),
which handles 1280-sample chunking + padding internally."""
import glob
import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "oww-src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "oww-data", "piper-sample-generator"))

import numpy as np
from openwakeword.model import Model

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
onnx = os.path.join(LAB, "oww-data", "output", "喂Nora.onnx")
if not os.path.exists(onnx):
    raise FileNotFoundError(onnx)

oww = Model(
    wakeword_models=[onnx],
    inference_framework="onnx",
    enable_speex_noise_suppression=True,
)


def score_wav(path, label):
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    # official clip-level scoring: returns per-chunk scores dict
    res = oww.predict_clip(pcm)
    # predict_clip returns {model_name: [per-chunk scores]} (or dict of dicts with timing)
    scores = res["喂Nora"] if isinstance(res, dict) and "喂Nora" in res else res
    if isinstance(scores, dict):
        scores = scores.get("喂Nora", list(scores.values())[0] if scores else [])
    peak = float(np.max(scores)) if len(scores) else 0.0
    fires = sum(1 for s in scores if s > 0.3)
    print(f"{label:40s} n={len(scores)} peak={peak:.3f} fires={fires}")


pos = sorted(glob.glob(os.path.join(LAB, "oww-data/output/喂Nora/positive_train", "*.wav")))[:5]
for p in pos:
    score_wav(p, "POSITIVE " + os.path.basename(p)[:26])

neg = sorted(glob.glob(os.path.join(LAB, "oww-data/output/喂Nora/negative_train", "*.wav")))[:3]
for p in neg:
    score_wav(p, "NEGATIVE " + os.path.basename(p)[:26])

# real-audio gate file (already 16k mono)
gate16k = "/tmp/gate16k.wav"
with wave.open(gate16k, "rb") as wf:
    raw = wf.readframes(wf.getnframes())
pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
res = oww.predict_clip(pcm)
scores = res["喂Nora"] if isinstance(res, dict) and "喂Nora" in res else res
if isinstance(scores, dict):
    scores = scores.get("喂Nora", list(scores.values())[0] if scores else [])
peak = float(np.max(scores)) if len(scores) else 0.0
fires = sum(1 for s in scores if s > 0.3)
print(f"{'REAL 21.2s GATE':40s} n={len(scores)} peak={peak:.3f} fires={fires}")
