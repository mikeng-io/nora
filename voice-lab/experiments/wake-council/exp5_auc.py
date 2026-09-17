#!/usr/bin/env python3
"""EXP5: ROC-AUC positive vs speech-negative. Is there ANY discrimination?"""
import glob, os, re
import numpy as np
from lib_score import load_model, read_wav_int16, score_stream

oww = load_model()
CORP = "/home/norty/workspace/wake-council-opus/oww-data/output/喂Nora"

def peak(p):
    pcm, sr = read_wav_int16(p)
    if sr != 16000 or len(pcm) < 1280: return None
    _, s = score_stream(oww, pcm)
    return s.max() if len(s) else None

def collect(pat, n, speech_only=False):
    out = []
    for p in sorted(glob.glob(pat)):
        b = os.path.basename(p)
        ishex = bool(re.fullmatch(r"[0-9a-f]{32}\.wav", b))
        if speech_only and ishex: continue
        if speech_only is False and not ishex and "neg" in pat: pass
        v = peak(p)
        if v is not None: out.append(v)
        if len(out) >= n: break
    return np.array(out)

pos = collect(os.path.join(CORP, "positive_test", "*.wav"), 400)
negs = collect(os.path.join(CORP, "negative_test", "*.wav"), 400, speech_only=True)

def auc(a, b):
    """P(score(pos) > score(neg)) via rank statistic."""
    allv = np.concatenate([a, b]); r = allv.argsort().argsort() + 1
    ra = r[:len(a)].sum()
    return (ra - len(a)*(len(a)+1)/2) / (len(a)*len(b))

print(f"positives      n={len(pos)} mean={pos.mean():.4f} sd={pos.std():.4f}")
print(f"speech-negs    n={len(negs)} mean={negs.mean():.4f} sd={negs.std():.4f}")
print(f"\nROC-AUC (positive vs speech-negative) = {auc(pos, negs):.4f}   [0.5 = random]")
d = (pos.mean()-negs.mean())/np.sqrt((pos.var()+negs.var())/2)
print(f"d-prime = {d:.4f}")

print(f"\n{'thresh':>7s} {'TPR(wake)':>10s} {'FPR(speech)':>12s}")
for th in (0.7, 0.6, 0.5, 0.45, 0.4, 0.3, 0.2):
    print(f"{th:7.2f} {100*(pos>=th).mean():9.1f}% {100*(negs>=th).mean():11.1f}%")
np.save("pos.npy", pos); np.save("negs.npy", negs)
