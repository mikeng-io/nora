#!/usr/bin/env python3
"""Extract openWakeWord standard features from JackyHoCL Cantonese+English
speech corpus (audio bytes in parquet) using the OFFICIAL
compute_features_from_generator API — same pipeline train.py uses.

Each clip is split into 2.0s (32000-sample) windows at 16kHz int16 PCM;
windows land at arbitrary positions (no alignment), exactly like real
sliding-window inference sees them. Clip-internal silence pauses stay
inside the clip — that's realistic conversation.
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "oww-src"))
import numpy as np
import pyarrow.parquet as pq
import soundfile as sf
import librosa
from openwakeword.utils import compute_features_from_generator

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARQUET = "/tmp/jackyhk-train0.parquet"
OUT = os.path.join(LAB, "oww-data/features/zhhk_speech_background.npy")
WINDOW = 32000  # 2.0s * 16k — matches official total_length → 16-frame features
MAX_WINDOWS = 20000
BATCH = 128

t = pq.read_table(PARQUET)
audio_col = t.column("audio").to_pylist()
print(f"rows={len(audio_col)}", flush=True)


def gen():
    """Yield batches of (<=128, 20480) int16 PCM windows from speech clips."""
    n_out = 0
    batch = []
    for rec in audio_col:
        if n_out >= MAX_WINDOWS:
            break
        try:
            data, sr = sf.read(io.BytesIO(rec["bytes"]), dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)
            if sr != 16000:
                data = librosa.resample(data, orig_sr=sr, target_sr=16000)
            pcm = (np.clip(data, -1, 1) * 32767).astype(np.int16)
        except Exception as e:
            print(f"  skip row: {e}", flush=True)
            continue
        n = len(pcm) // WINDOW
        if n == 0:
            continue  # clip < 1.28s
        for k in range(n):
            if n_out >= MAX_WINDOWS:
                break
            batch.append(pcm[k * WINDOW : (k + 1) * WINDOW])
            n_out += 1
            if len(batch) >= BATCH:
                yield np.stack(batch).astype(np.int16)
                batch = []
    if batch:
        yield np.stack(batch).astype(np.int16)
    print(f"generator total windows: {n_out}", flush=True)


print("computing features via official pipeline...", flush=True)
compute_features_from_generator(
    gen(),
    n_total=MAX_WINDOWS,
    clip_duration=WINDOW,
    output_file=OUT,
    device="cpu",
    ncpu=1,
)
arr = np.load(OUT, mmap_mode="r")
print(f"saved {OUT} shape={arr.shape} dtype={arr.dtype}", flush=True)
print("DONE", flush=True)
