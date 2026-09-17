#!/usr/bin/env python3
"""EXP7: independently measure FP/hour on the official 11h speech/noise/music set."""
import numpy as np, onnxruntime as ort, time
from numpy.lib.stride_tricks import sliding_window_view
ONNX = "/home/norty/workspace/wake-council-opus/oww-data/output/喂Nora.onnx"
s = ort.InferenceSession(ONNX, providers=["CPUExecutionProvider"])
inp = s.get_inputs()[0]; name = inp.name; n_frames = inp.shape[1]
F = np.load("/home/norty/workspace/wake-council-opus/oww-data/features/validation_set_features.npy")
hours = F.shape[0] * 0.08 / 3600
print(f"features {F.shape}  ~{hours:.2f} h of speech/noise/music (all NEGATIVE)")

W = sliding_window_view(F, (n_frames, F.shape[1])).squeeze(1)  # (N,16,96)
t0 = time.time()
sc = np.empty(len(W), dtype=np.float32)
for i in range(len(W)):
    sc[i] = s.run(None, {name: W[i][None].astype(np.float32)})[0].ravel()[0]
print(f"scored {len(sc)} windows in {time.time()-t0:.0f}s  mean={sc.mean():.4f} max={sc.max():.4f}")

def activations(sc, th, debounce_frames=25):  # 2s debounce
    n, last = 0, -10**9
    for i in np.where(sc >= th)[0]:
        if i - last > debounce_frames:
            n += 1; last = i
    return n

print(f"\n{'thresh':>7s} {'raw win>=th':>12s} {'%time':>7s} {'activations':>12s} {'FP/hour':>10s}")
for th in (0.9, 0.8, 0.7, 0.6, 0.5, 0.45, 0.4):
    raw = int((sc >= th).sum()); act = activations(sc, th)
    print(f"{th:7.2f} {raw:12d} {100*raw/len(sc):6.1f}% {act:12d} {act/hours:10.1f}")
np.save("valfp.npy", sc)
