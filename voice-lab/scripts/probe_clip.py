#!/usr/bin/env python3
"""Probe predict_clip return structure."""
import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "oww-src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "oww-data", "piper-sample-generator"))

import numpy as np
from openwakeword.model import Model

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
onnx = os.path.join(LAB, "oww-data", "output", "喂Nora.onnx")

oww = Model(
    wakeword_models=[onnx],
    inference_framework="onnx",
    enable_speex_noise_suppression=True,
)

pos = os.path.join(LAB, "oww-data/output/喂Nora/positive_train", "00000-喂-Nora-0.wav")
with wave.open(pos, "rb") as wf:
    raw = wf.readframes(wf.getnframes())
pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

res = oww.predict_clip(pcm)
print("TYPE:", type(res))
if isinstance(res, list):
    print("first 5:", res[:5])
    print("len:", len(res))
elif isinstance(res, dict):
    print("KEYS:", list(res.keys())[:5])
    for k in list(res.keys())[:2]:
        v = res[k]
        print(f"--- key {k}: type={type(v)} sample={v[:5] if hasattr(v, '__getitem__') else v}")
