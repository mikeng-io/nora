#!/usr/bin/env python3
"""Probe openwakeword predict() return structure."""
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
    vad_threshold=0.5,
)

wav_path = "/tmp/gate16k.wav"
with wave.open(wav_path, "rb") as wf:
    raw = wf.readframes(wf.getnframes())
pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

pred = oww.predict(pcm[0:2048])
print("type:", type(pred))
print("keys:", list(pred.keys()) if hasattr(pred, "keys") else "N/A")
for k, v in pred.items():
    print(f"  {k}: type={type(v)} shape={getattr(v, 'shape', None)} value={v}")
