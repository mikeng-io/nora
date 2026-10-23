#!/usr/bin/env python3
"""Shared scoring helpers for the 喂Nora wake-word investigation."""
import os, sys, wave
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "oww-src"))
import numpy as np
from openwakeword.model import Model

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONNX = os.path.join(LAB, "oww-data", "output", "喂Nora.onnx")
CHUNK = 1280  # 80 ms @ 16k, openwakeword native hop

def load_model():
    return Model(wakeword_models=[ONNX], inference_framework="onnx")

def read_wav_int16(path):
    with wave.open(path, "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(f"{path}: not 16-bit PCM")
        sr, ch = wf.getframerate(), wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return pcm, sr

def score_stream(oww, pcm, chunk=CHUNK):
    oww.reset()
    times, scores = [], []
    for i in range(0, len(pcm) - chunk + 1, chunk):
        s = float(list(oww.predict(pcm[i:i+chunk]).values())[0])
        times.append((i + chunk) / 16000.0)
        scores.append(s)
    return np.array(times), np.array(scores)

def score_file(oww, path, chunk=CHUNK):
    pcm, sr = read_wav_int16(path)
    if sr != 16000:
        raise ValueError(f"{path} is {sr} Hz")
    return score_stream(oww, pcm, chunk)
