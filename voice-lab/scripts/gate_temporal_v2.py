#!/usr/bin/env python3
"""Temporal fire pattern on the 21.2s real recording — corrected timestamps.

Window i (sample offset i*CHUNK) -> real second = (i*CHUNK)/16000.
Events = consecutive fired windows merged; gap > 800ms splits.
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
GAP_SPLIT = 0.8


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
    real = [w * CHUNK / SR for w in over_idx]
    # merge consecutive fired windows into events (gap in window indices > 10 = 800ms)
    events = []
    if over_idx:
        start = prev = over_idx[0]
        for w in over_idx[1:]:
            if w - prev > 10:
                events.append((start * CHUNK / SR, (prev + 1) * CHUNK / SR))
                start = w
            prev = w
        events.append((start * CHUNK / SR, (prev + 1) * CHUNK / SR))
    print(f"== {label} (thr={thr}) ==")
    print(f"  total windows={len(scores)} fired={len(over_idx)} ({100*len(over_idx)/len(scores):.1f}%)")
    print(f"  score: min={min(scores):.3f} p50={np.percentile(scores,50):.3f} p90={np.percentile(scores,90):.3f} max={max(scores):.3f}")
    print(f"  merged events (>=thr, gap>800ms): {len(events)}")
    for a, b in events:
        print(f"    {a:5.2f}s - {b:5.2f}s  dur={b-a:4.2f}s")
    print()


temporal(os.path.join(LAB, "oww-data/output/喂Nora.onnx"), "OLD model v1")
temporal(os.path.join(LAB, "data/modal-output-v2/喂Nora.onnx"), "NEW model v2")
