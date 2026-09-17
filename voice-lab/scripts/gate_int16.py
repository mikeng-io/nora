#!/usr/bin/env python3
"""Re-run the real-audio gate with CORRECT int16 input per Kimi's finding.
Also score a few true positives/negatives the same way."""
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

oww = Model(wakeword_models=[onnx], inference_framework="onnx")

def score_wav(path, label, chunk=1280):
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16)
    oww.reset()
    fires = 0
    peaks = []
    for i in range(0, len(pcm) - chunk + 1, chunk):
        s = float(list(oww.predict(pcm[i : i + chunk]).values())[0])
        peaks.append(s)
        if s > 0.3:
            fires += 1
    print(f"{label:45s} peak={max(peaks):.3f} fires={fires}/{len(peaks)}  (sr={sr})")

# positives
pos = sorted(glob.glob(os.path.join(LAB, "oww-data/output/喂Nora/positive_train", "*.wav")))[:3]
for p in pos:
    score_wav(p, "POSITIVE " + os.path.basename(p)[:26])

# negatives
neg = sorted(glob.glob(os.path.join(LAB, "oww-data/output/喂Nora/negative_train", "*.wav")))[:3]
for p in neg:
    score_wav(p, "NEGATIVE " + os.path.basename(p)[:26])

# real gate — already 16k mono in /tmp/gate16k.wav
score_wav("/tmp/gate16k.wav", "REAL 21.2s GATE (5 喂Nora under music)")
