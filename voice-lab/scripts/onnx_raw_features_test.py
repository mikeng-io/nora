#!/usr/bin/env python3
"""Decisive diagnostic: feed RAW TRAIN-TIME FEATURES into the exported ONNX.
If scores are non-zero here, the model itself is alive and the streaming
feature pipeline (PCM -> featurizer) is what dies. If zero here, export/bug.
validation_set_features.npy = (481345, 96) float32 — features used during
training FP evals, same featurizer. ONNX wants [1,16,96] (16 stacked frames).
"""
import os
import sys

import numpy as np
import onnxruntime as ort

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
onnx_path = os.path.join(LAB, "oww-data", "output", "喂Nora.onnx")
feat_path = os.path.join(LAB, "oww-data", "features", "validation_set_features.npy")

X = np.load(feat_path, mmap_mode="r")
print("features:", X.shape, X.dtype)

sess = ort.InferenceSession(onnx_path)
in_name = sess.get_inputs()[0].name
out_name = sess.get_outputs()[0].name
print("onnx in:", in_name, sess.get_inputs()[0].shape)
print("onnx out:", out_name, sess.get_outputs()[0].shape)

# Build 16-frame windows, stride 16 (no overlap) — sample up to 64 windows
windows = []
for i in range(0, min(len(X) - 16, 64 * 16), 16):
    windows.append(X[i : i + 16])
print("windows:", len(windows))

scores = []
for w in windows:
    inp = w[None, :, :].astype(np.float32)  # [1,16,96]
    out = sess.run([out_name], {in_name: inp})[0]
    scores.append(float(out.reshape(-1)[0]))

scores = np.array(scores)
print("=== ONNX scores on raw training features ===")
print("min:", scores.min(), "max:", scores.max(), "mean:", scores.mean())
print(">0.3:", int((scores > 0.3).sum()), "of", len(scores))
print("first 10:", scores[:10].round(4).tolist())
