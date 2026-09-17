#!/usr/bin/env python3
"""Verify Kimi's harness-bug claim: feeding float32 [-1,1] into the
openWakeWord streaming path truncates to int16 silence (all zeros),
while feeding native int16 PCM gives real scores."""
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

pcm_int16 = np.frombuffer(raw, dtype=np.int16)
pcm_float = pcm_int16.astype(np.float32) / 32768.0

print(f"int16: min={pcm_int16.min()} max={pcm_int16.max()}")
print(f"float: min={pcm_float.min():.4f} max={pcm_float.max():.4f}")

# Kimi says streaming path astype(int16) on the float list → 0 for |v|<1
scores_float = []
oww.reset()
for i in range(0, len(pcm_float) - 512 + 1, 512):
    scores_float.append(float(list(oww.predict(pcm_float[i : i + 512]).values())[0]))

scores_int = []
oww.reset()
for i in range(0, len(pcm_int16) - 512 + 1, 512):
    scores_int.append(float(list(oww.predict(pcm_int16[i : i + 512]).values())[0]))

peak_f = max(scores_float)
peak_i = max(scores_int)
print(f"FLOAT path: peak={peak_f:.4f} nonzero={sum(1 for s in scores_float if s > 0.01)}/{len(scores_float)}")
print(f"INT16 path: peak={peak_i:.4f} nonzero={sum(1 for s in scores_int if s > 0.01)}/{len(scores_int)}")
