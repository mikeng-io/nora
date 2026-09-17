#!/usr/bin/env python3
"""Cut candidate real-audio wake segments from the DAVE live recordings.

Both recordings (live-miles-repaired.wav, 100000000000000001-*.wav) contain
「喂 Nora」utterances. This script uses an energy-based VAD to find speech
bursts, cuts each burst to a wav, and prints a rough transcript guess via
SenseVoice so the operator can pick positives/negatives quickly.

Output: data/realprobe/candidates/ (wav per burst) + manifest.json
"""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import numpy as np

SRC = [
    Path("/home/norty/workspace/voice-lab-data/sidecar/recordings/live-miles-repaired.wav"),
    Path("/home/norty/workspace/voice-lab-data/sidecar/recordings/100000000000000001-1789405579646.wav"),
]
OUT = Path("/home/norty/workspace/voice-lab-data/data/realprobe/candidates")
OUT.mkdir(parents=True, exist_ok=True)

SR = 48000
HOP = int(0.02 * SR)      # 20 ms
WIN_MS = 25
THRESH = 300               # int16 amplitude
MIN_BURST = 0.35           # seconds
PAD = 0.35                 # seconds before/after burst
MAX_BURST = 4.0


def read_wav(path: Path) -> tuple[np.ndarray, int, int]:
    with wave.open(str(path)) as w:
        n = w.getnframes()
        ch = w.getnchannels()
        rate = w.getframerate()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        a = a[::ch]  # take first channel (per-user stream is stereo from DAVE)
    return a.astype(np.float32), rate, ch


def find_bursts(a: np.ndarray, rate: int) -> list[tuple[int, int]]:
    energy = np.abs(a)
    frames = (len(a) - WIN_MS * rate // 1000) // HOP + 1
    active = np.zeros(frames, dtype=bool)
    for i in range(frames):
        s = i * HOP
        seg = energy[s:s + WIN_MS * rate // 1000]
        if len(seg) and seg.mean() > THRESH:
            active[i] = True
    bursts: list[tuple[int, int]] = []
    i = 0
    while i < frames:
        if active[i]:
            j = i
            while j < frames and active[j]:
                j += 1
            start = max(0, i * HOP - int(PAD * rate))
            end = min(len(a), j * HOP + int(PAD * rate))
            if (end - start) / rate >= MIN_BURST:
                bursts.append((start, end))
            i = j
        else:
            i += 1
    return bursts


def cut(a: np.ndarray, rate: int, start: int, end: int, out: Path) -> None:
    import struct
    data = a[start:end].astype(np.int16).tobytes()
    with open(out, "wb") as f:
        h = bytearray(44)
        h[0:4] = b"RIFF"
        struct.pack_into("<I", h, 4, 36 + len(data))
        h[8:12] = b"WAVE"
        h[12:16] = b"fmt "
        struct.pack_into("<I", h, 16, 16)
        struct.pack_into("<H", h, 20, 1)
        struct.pack_into("<H", h, 22, 1)
        struct.pack_into("<I", h, 24, rate)
        struct.pack_into("<I", h, 28, rate * 2)
        struct.pack_into("<H", h, 32, 2)
        struct.pack_into("<H", h, 34, 16)
        h[36:40] = b"data"
        struct.pack_into("<I", h, 40, len(data))
        f.write(h)
        f.write(data)


def main() -> None:
    manifest = []
    idx = 0
    for src in SRC:
        a, rate, _ch = read_wav(src)
        print(f"{src.name}: {len(a)/rate:.1f}s {rate}Hz", flush=True)
        for (s, e) in find_bursts(a, rate):
            dur = (e - s) / rate
            if dur > MAX_BURST:
                continue
            name = f"real_{idx:03d}_{src.stem[:12]}_{int(s/rate)}-{int(e/rate)}s.wav"
            out = OUT / name
            cut(a, rate, s, e, out)
            manifest.append({"file": f"candidates/{name}", "src": src.name, "start_s": round(s/rate, 2), "duration_s": round(dur, 2), "label": "unlabeled"})
            idx += 1
            print(f"  burst {idx}: {s/rate:.1f}-{e/rate:.1f}s ({dur:.1f}s) -> {name}", flush=True)
    (OUT.parent / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"total candidates: {len(manifest)}")


if __name__ == "__main__":
    main()
