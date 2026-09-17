#!/usr/bin/env python3
"""EXP6: what actually moves the score? gain, RIR, codec, noise."""
import numpy as np, subprocess, os, tempfile
from scipy import signal as sig
from lib_score import load_model, read_wav_int16, score_stream
import soundfile as sf

oww = load_model()
pcm, sr = read_wav_int16("/tmp/gate16k.wav")
BASE = pcm.astype(np.float64)

def sc(x):
    x = np.clip(x, -32768, 32767).astype(np.int16)
    _, s = score_stream(oww, x)
    return s

base = sc(BASE)
print(f"BASELINE            peak={base.max():.3f}  n>=0.5={int((base>=0.5).sum())}")

print("\n=== GAIN (level) sensitivity ===")
for g_db in (12, 6, 0, -6, -12, -18, -24, -30):
    g = 10 ** (g_db / 20)
    s = sc(BASE * g)
    print(f"  {g_db:+4d} dB   peak={s.max():.3f}  n>=0.5={int((s>=0.5).sum()):3d}")

print("\n=== ADDITIVE NOISE (SNR) ===")
rms = np.sqrt((BASE**2).mean())
rng = np.random.default_rng(0)
for snr in (30, 20, 15, 10, 5, 0):
    n = rng.normal(0, rms / (10**(snr/20)), len(BASE))
    s = sc(BASE + n)
    print(f"  SNR {snr:2d} dB  peak={s.max():.3f}  n>=0.5={int((s>=0.5).sum()):3d}")

print("\n=== RIR convolution (BIRD rooms) ===")
import glob
rirs = sorted(glob.glob("/home/norty/workspace/wake-council-opus/oww-data/rir/mono16k/*.wav"))[:6]
for rp in rirs:
    h, hsr = sf.read(rp)
    if h.ndim > 1: h = h.mean(axis=1)
    y = sig.fftconvolve(BASE, h)[:len(BASE)]
    y = y / (np.abs(y).max() + 1e-9) * np.abs(BASE).max()
    s = sc(y)
    print(f"  {os.path.basename(rp)[:34]:34s} peak={s.max():.3f}  n>=0.5={int((s>=0.5).sum()):3d}")

print("\n=== OPUS codec round-trip (Discord path) ===")
for br in ("64k", "32k", "24k"):
    with tempfile.TemporaryDirectory() as td:
        wav_in = os.path.join(td, "in.wav"); opus = os.path.join(td, "a.opus"); out = os.path.join(td, "out.wav")
        sf.write(wav_in, (BASE/32768.0), 16000)
        r1 = subprocess.run(["ffmpeg","-y","-loglevel","error","-i",wav_in,"-c:a","libopus","-b:a",br,"-ar","48000",opus], capture_output=True)
        r2 = subprocess.run(["ffmpeg","-y","-loglevel","error","-i",opus,"-ar","16000","-ac","1",out], capture_output=True)
        if r1.returncode or r2.returncode:
            print(f"  opus {br}: ffmpeg unavailable/failed: {(r1.stderr or r2.stderr)[:120]}"); continue
        y, ysr = sf.read(out, dtype="int16")
        _, s = score_stream(oww, y)
        print(f"  opus {br:4s} peak={s.max():.3f}  n>=0.5={int((s>=0.5).sum()):3d}")
