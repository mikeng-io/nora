#!/usr/bin/env python3
"""Side-by-side gate: OLD v1 model vs NEW v2 (fixed training) model."""
import glob
import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "oww-src"))
import numpy as np
from openwakeword.model import Model

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THR = 0.3
CHUNK = 1280


def run(onnx, label):
    oww = Model(wakeword_models=[onnx], inference_framework="onnx")

    def score_wav(path, name):
        with wave.open(path, "rb") as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        pcm = np.frombuffer(raw, dtype=np.int16)
        oww.reset()
        fires = 0
        peaks = []
        for i in range(0, len(pcm) - CHUNK + 1, CHUNK):
            s = float(list(oww.predict(pcm[i : i + CHUNK]).values())[0])
            peaks.append(s)
            if s > THR:
                fires += 1
        print(f"  {name:38s} peak={max(peaks):.3f} fires={fires}/{len(peaks)} (sr={sr})")

    pos = sorted(glob.glob(os.path.join(LAB, "oww-data/output/喂Nora/positive_train", "*.wav")))[:3]
    neg = sorted(glob.glob(os.path.join(LAB, "oww-data/output/喂Nora/negative_train", "*.wav")))[:2]
    print(f"== {label} ==")
    for p in pos:
        score_wav(p, "POS " + os.path.basename(p)[:20])
    for n in neg:
        score_wav(n, "NEG " + os.path.basename(n)[:20])
    gate = "/tmp/gate16k.wav"
    if os.path.exists(gate):
        score_wav(gate, "REAL 21.2s (5 wake + music)")


run(os.path.join(LAB, "oww-data/output/喂Nora.onnx"), "OLD model (v1 train)")
print()
run(os.path.join(LAB, "data/modal-output-v2/喂Nora.onnx"), "NEW model (v2 fixed training)")
