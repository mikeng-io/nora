#!/usr/bin/env python3
"""EXP4: per-phrase-class score. Which negative classes does the model fire on?"""
import glob, os, re, collections
import numpy as np
from lib_score import load_model, read_wav_int16, score_stream

oww = load_model()
CORP = "/home/norty/workspace/wake-council-opus/oww-data/output/喂Nora"

def cls(fn):
    b = os.path.basename(fn)
    if re.fullmatch(r"[0-9a-f]{32}\.wav", b): return "__BACKGROUND__"
    return re.sub(r"-[0-9a-f]{12}\.wav$", "", b)

def peak(p):
    pcm, sr = read_wav_int16(p)
    if sr != 16000 or len(pcm) < 1280: return None
    _, s = score_stream(oww, pcm)
    return s.max() if len(s) else None

groups = collections.defaultdict(list)
for p in sorted(glob.glob(os.path.join(CORP, "negative_test", "*.wav"))):
    groups[cls(p)].append(p)

N = 30
rows = []
for k, fs in groups.items():
    vals = [v for v in (peak(p) for p in fs[:N]) if v is not None]
    if vals:
        a = np.array(vals)
        rows.append((k, len(a), a.mean(), np.median(a), (a >= 0.5).mean(), a.max()))

rows.sort(key=lambda r: -r[4])
print(f"{'NEGATIVE class':28s} {'n':>4s} {'mean':>6s} {'med':>6s} {'fire>=.5':>9s} {'max':>6s}")
print("-" * 66)
for k, n, m, md, fr, mx in rows:
    flag = "  <== CONTAINS WAKE PHRASE" if ("喂Nora啦" in k or "喂呀Nora" in k) else ""
    print(f"{k:28s} {n:4d} {m:6.3f} {md:6.3f} {100*fr:8.1f}% {mx:6.3f}{flag}")

# positives for reference
pv = [v for v in (peak(p) for p in sorted(glob.glob(os.path.join(CORP,"positive_test","*.wav")))[:60]) if v is not None]
pa = np.array(pv)
print("-" * 66)
print(f"{'POSITIVE (喂 Nora)':28s} {len(pa):4d} {pa.mean():6.3f} {np.median(pa):6.3f} {100*(pa>=.5).mean():8.1f}% {pa.max():6.3f}")
