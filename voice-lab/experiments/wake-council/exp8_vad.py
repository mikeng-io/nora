#!/usr/bin/env python3
"""EXP8: energy-VAD the real recording; compare speech bursts vs model fires."""
import numpy as np
from lib_score import load_model, read_wav_int16, score_stream
pcm, sr = read_wav_int16("/tmp/gate16k.wav")
x = np.abs(pcm.astype(np.float64))
hop = 320  # 20ms
e = np.array([x[i:i+hop].mean() for i in range(0, len(x)-hop, hop)])
voiced = e > 200
# merge bursts separated by <300ms, drop bursts <250ms
bursts, i = [], 0
while i < len(voiced):
    if voiced[i]:
        j = i
        while j < len(voiced) and (voiced[j] or (j+15 < len(voiced) and voiced[j:j+15].any())):
            j += 1
        if (j-i)*0.02 >= 0.25: bursts.append((i*0.02, j*0.02))
        i = j
    else: i += 1
print(f"energy-VAD speech bursts in the 21.2s recording: {len(bursts)}")
for a,b in bursts: print(f"  {a:6.2f}s - {b:6.2f}s   ({b-a:.2f}s)")

oww = load_model()
t, s = score_stream(oww, pcm)
fired = []
idx = np.where(s > 0.5)[0]
if len(idx):
    cur=[idx[0]]
    for a,b in zip(idx, idx[1:]):
        if (b-a)*0.08 > 0.4: fired.append(cur); cur=[b]
        else: cur.append(b)
    fired.append(cur)
print(f"\nmodel fire events (>=0.5): {len(fired)}")
for c in fired: print(f"  {t[c[0]]:6.2f}s - {t[c[-1]]:6.2f}s  peak={s[c].max():.3f}")
print(f"\n=> {len(bursts)} speech bursts, {len(fired)} model fires")
