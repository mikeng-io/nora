#!/usr/bin/env python3
"""EXP2: how much leading context does the model need before the wake word?

Production question: if the voice-wake container opens a FRESH stream when
Discord signals 'user started speaking', the wake word arrives while
openwakeword's 16-frame embedding buffer is still cold.
"""
import numpy as np
from lib_score import load_model, read_wav_int16, score_stream

oww = load_model()
pcm, sr = read_wav_int16("/tmp/gate16k.wav")

PEAKS = [2.80, 4.56, 6.56, 8.24, 10.16, 12.08, 14.40, 17.12, 18.64]
LEADS = [0.0, 0.16, 0.32, 0.48, 0.64, 0.96, 1.28, 1.60, 2.00]
# assume the spoken wake word begins ~1.3s before the score peak
ONSET_BACK = 1.30
TAIL = 0.60

print(f"{'utt@peak':>10s} " + " ".join(f"L={l:4.2f}" for l in LEADS))
rows = []
for P in PEAKS:
    onset = P - ONSET_BACK
    row = []
    for L in LEADS:
        a = int(max(0, (onset - L)) * sr)
        b = int((P + TAIL) * sr)
        seg = pcm[a:b]
        _, s = score_stream(oww, seg)
        row.append(s.max() if len(s) else 0.0)
    rows.append(row)
    print(f"{P:9.2f}s " + " ".join(f"{v:6.3f}" for v in row))

R = np.array(rows)
print("\n" + "-" * 70)
print(f"{'MEAN':>10s} " + " ".join(f"{v:6.3f}" for v in R.mean(axis=0)))
print(f"{'MIN':>10s} " + " ".join(f"{v:6.3f}" for v in R.min(axis=0)))
print(f"{'>=0.5':>10s} " + " ".join(f"{int((R[:,i]>=0.5).sum()):6d}" for i in range(len(LEADS))) + "   /9 utterances")
print(f"{'>=0.4':>10s} " + " ".join(f"{int((R[:,i]>=0.4).sum()):6d}" for i in range(len(LEADS))) + "   /9 utterances")
np.save("/home/norty/workspace/wake-council-opus/experiments/coldstart.npy", R)
