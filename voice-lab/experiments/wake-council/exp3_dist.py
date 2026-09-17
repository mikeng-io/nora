#!/usr/bin/env python3
"""EXP3: score distributions — synthetic train/test positives, negatives, RIR samples."""
import glob, os, sys
import numpy as np
from lib_score import load_model, read_wav_int16, score_stream

oww = load_model()
LAB = "/home/norty/workspace/wake-council-opus"
CORP = os.path.join(LAB, "oww-data", "output", "喂Nora")

def peaks(files, label, limit=250):
    out = []
    for p in files[:limit]:
        try:
            pcm, sr = read_wav_int16(p)
        except Exception:
            continue
        if sr != 16000 or len(pcm) < 1280:
            continue
        _, s = score_stream(oww, pcm)
        if len(s): out.append(s.max())
    a = np.array(out)
    if not len(a):
        print(f"{label:34s} NO FILES"); return a
    print(f"{label:34s} n={len(a):4d}  mean={a.mean():.3f} med={np.median(a):.3f} "
          f"p5={np.percentile(a,5):.3f} p25={np.percentile(a,25):.3f} max={a.max():.3f}  "
          f">=.5:{100*(a>=.5).mean():5.1f}%  >=.4:{100*(a>=.4).mean():5.1f}%")
    return a

for d in ["positive_train", "positive_test", "negative_train", "negative_test"]:
    fs = sorted(glob.glob(os.path.join(CORP, d, "*.wav")))
    print(f"[{d}] {len(fs)} files")

res = {}
for d in ["positive_test", "positive_train", "negative_test", "negative_train"]:
    fs = sorted(glob.glob(os.path.join(CORP, d, "*.wav")))
    res[d] = peaks(fs, d)

print()
rir = sorted(glob.glob("/tmp/wake-rir-samples/rir_*.wav"))
clean = sorted(glob.glob("/tmp/wake-rir-samples/00_clean*.wav"))
for p in clean + rir:
    pcm, sr = read_wav_int16(p)
    _, s = score_stream(oww, pcm)
    print(f"  {os.path.basename(p):40s} sr={sr} dur={len(pcm)/sr:5.2f}s peak={s.max():.3f}")

np.save(os.path.join(LAB, "experiments", "dist.npy"), np.array([0]))
for k, v in res.items():
    np.save(os.path.join(LAB, "experiments", f"dist_{k}.npy"), v)
