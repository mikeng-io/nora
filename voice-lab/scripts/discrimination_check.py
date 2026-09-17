#!/usr/bin/env python3
"""Full discrimination check: trap negatives (near-phonetic 喂勞拉 etc) vs
positives, and the temporal distribution of fires in the real gate."""
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
oww = Model(wakeword_models=[onnx], inference_framework="onnx", enable_speex_noise_suppression=True)

def score_wav(path, label, chunk=1280):
    with wave.open(path, "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16)
    oww.reset()
    peaks = []
    for i in range(0, len(pcm) - chunk + 1, chunk):
        peaks.append(float(list(oww.predict(pcm[i : i + chunk]).values())[0]))
    return max(peaks) if peaks else 0.0

# near-phonetic trap negatives (喂勞拉 etc) — the discriminator pressure set
traps = sorted(glob.glob(os.path.join(LAB, "oww-data/output/喂Nora/negative_train", "*.wav")))
trap_peaks = []
for p in traps[:20]:
    basename = os.path.basename(p)
    # MiniMax near-trap files start with 喂; background cuts are hex names
    is_trap_like = any(k in basename for k in ["勞拉", "露娜", "羅拉", "樂拉", "娜娜", "阿娜"])
    pk = score_wav(p, None)
    if is_trap_like:
        trap_peaks.append((basename[:20], pk))
print("=== near-phonetic trap negatives (should be LOW) ===")
for n, pk in trap_peaks:
    print(f"  {n:22s} peak={pk:.3f}")
print()

# real gate time distribution of fires
with wave.open("/tmp/gate16k.wav", "rb") as wf:
    sr = wf.getframerate()
    raw = wf.readframes(wf.getnframes())
pcm = np.frombuffer(raw, dtype=np.int16)
oww.reset()
chunk = 1280
fire_times = []
for i in range(0, len(pcm) - chunk + 1, chunk):
    s = float(list(oww.predict(pcm[i : i + chunk]).values())[0])
    if s > 0.3:
        fire_times.append(i / sr)
print("=== REAL GATE fire times (s) ===")
print([round(t, 1) for t in fire_times])
if fire_times:
    # clusters: consecutive fires within 0.3s count as one event
    events = 1
    for a, b in zip(fire_times, fire_times[1:]):
        if b - a > 0.5:
            events += 1
    print(f"total fires: {len(fire_times)}, approx distinct events: {events}")
