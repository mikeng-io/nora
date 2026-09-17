#!/usr/bin/env python3
"""EXP1: dense per-window score trace over the real 21.2s recording."""
import numpy as np
from lib_score import load_model, read_wav_int16, score_stream

oww = load_model()
pcm, sr = read_wav_int16("/tmp/gate16k.wav")
print(f"real audio: {len(pcm)/sr:.2f}s  sr={sr}  rms={np.sqrt((pcm.astype(np.float64)**2).mean()):.0f}  peak={np.abs(pcm).max()}")

t, s = score_stream(oww, pcm)
print(f"windows={len(s)}  max={s.max():.4f}  mean={s.mean():.4f}  median={np.median(s):.4f}")

# full trace, compressed: print every window with score > 0.01
print("\n=== windows with score > 0.01 ===")
for ti, si in zip(t, s):
    if si > 0.01:
        bar = "#" * int(si * 50)
        print(f"  {ti:6.2f}s  {si:.4f} {bar}")

# local maxima clusters (peaks separated by >0.5s of sub-0.05)
print("\n=== peak clusters (score>0.05, gap>0.4s) ===")
idx = np.where(s > 0.05)[0]
if len(idx):
    clusters, cur = [], [idx[0]]
    for a, b in zip(idx, idx[1:]):
        if (b - a) * 0.08 > 0.4:
            clusters.append(cur); cur = [b]
        else:
            cur.append(b)
    clusters.append(cur)
    for c in clusters:
        pk = c[int(np.argmax(s[c]))]
        print(f"  t={t[c[0]]:.2f}-{t[c[-1]]:.2f}s  peak={s[pk]:.4f} @ {t[pk]:.2f}s  nwin={len(c)}")

for th in (0.5, 0.45, 0.4, 0.3, 0.2, 0.1):
    print(f"threshold {th:.2f}: {int((s>=th).sum())} windows fire")
np.save("/home/norty/workspace/wake-council-opus/experiments/real_trace.npy", np.vstack([t, s]))
