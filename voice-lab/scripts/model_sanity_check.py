#!/usr/bin/env python3
"""Sanity-check the trained 喂Nora model: real positives must fire, negatives must not."""
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
    vad_threshold=0.5,
)


def score_wav(path, label):
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        if sr != 16000:
            raise ValueError(f"{label}: sr={sr}")
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    chunk = 512
    scores = []
    for i in range(0, len(pcm) - chunk, chunk):
        pred = oww.predict(pcm[i : i + chunk])
        if pred:
            # single wakeword model → score = the only value in the dict
            scores.append(float(list(pred.values())[0]))
    peak = max(scores) if scores else 0.0
    fires = sum(1 for s in scores if s > 0.3)
    print(f"{label:40s} peak={peak:.3f} fires={fires}/{len(scores)}")


pos = sorted(glob.glob(os.path.join(LAB, "oww-data/output/喂Nora/positive_train", "*.wav")))[:3]
for p in pos:
    score_wav(p, "POSITIVE " + os.path.basename(p)[:26])

neg = sorted(glob.glob(os.path.join(LAB, "oww-data/output/喂Nora/negative_train", "*.wav")))[:3]
for p in neg:
    score_wav(p, "NEGATIVE " + os.path.basename(p)[:26])
