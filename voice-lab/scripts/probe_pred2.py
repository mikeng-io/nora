#!/usr/bin/env python3
"""Probe predict() with proper 512-sample windows on a real positive clip."""
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
# probe: no-VAD variant so we can isolate whether VAD is eating every frame
oww_novad = Model(
    wakeword_models=[onnx],
    inference_framework="onnx",
    enable_speex_noise_suppression=True,
)

pos = os.path.join(LAB, "oww-data/output/喂Nora/positive_train", "00000-喂-Nora-0.wav")
with wave.open(pos, "rb") as wf:
    sr = wf.getframerate()
    raw = wf.readframes(wf.getnframes())
print("sr:", sr)
pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
print("len:", len(pcm))

# 3 windows, both VAD and no-VAD paths
for off in range(0, min(1536, len(pcm) - 512), 512):
    seg = pcm[off : off + 512]
    for name, m in (("VAD", oww), ("noVAD", oww_novad)):
        pred = m.predict(seg)
        if isinstance(pred, dict):
            for k, v in pred.items():
                print(f"[{name}] win@{off}: {k}={v}", end=" ")
            print()
        else:
            print(f"[{name}] win@{off}: RAW {pred}")
