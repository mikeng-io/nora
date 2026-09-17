#!/usr/bin/env python3
"""Three-model temporal gate comparison on Miles's 21.2s real recording.
v1 = broken training (gradient drop / 6000x weight), v2 = fixes applied,
v3 = + real zh-HK speech negatives.

Focus: does the 10.24s continuous fire (v2) disappear in v3?
"""
import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "oww-src"))
import numpy as np
from openwakeword.model import Model

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNK = 1280
SR = 16000
GAP_SPLIT = 10  # windows; 10*80ms = 800ms


def temporal(onnx, label, thr=0.3):
    with wave.open("/tmp/gate16k.wav", "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16)
    oww = Model(wakeword_models=[onnx], inference_framework="onnx")
    oww.reset()
    over_idx = []
    scores = []
    for i in range(0, len(pcm) - CHUNK + 1, CHUNK):
        s = float(list(oww.predict(pcm[i : i + CHUNK]).values())[0])
        scores.append(s)
        if s > thr:
            over_idx.append(i // CHUNK)
    events = []
    if over_idx:
        start = prev = over_idx[0]
        for w in over_idx[1:]:
            if w - prev > GAP_SPLIT:
                events.append((start * CHUNK / SR, (prev + 1) * CHUNK / SR))
                start = w
            prev = w
        events.append((start * CHUNK / SR, (prev + 1) * CHUNK / SR))
    print(f"== {label} (thr={thr}) ==")
    print(f"  windows={len(scores)} fired={len(over_idx)} ({100*len(over_idx)/len(scores):.1f}%)")
    print(f"  score: p50={np.percentile(scores,50):.3f} p90={np.percentile(scores,90):.3f} p99={np.percentile(scores,99):.3f} max={max(scores):.3f}")
    print(f"  events: {len(events)}")
    for a, b in events:
        print(f"    {a:5.2f}s - {b:5.2f}s  dur={b-a:4.2f}s")
    print()


temporal(os.path.join(LAB, "oww-data/output/喂Nora.onnx"), "v1 (broken)")
temporal(os.path.join(LAB, "data/modal-output-v2/喂Nora.onnx"), "v2 (fixes)")
temporal(os.path.join(LAB, "data/modal-output-v3/喂Nora.onnx"), "v3 (+zh-HK speech neg)")
