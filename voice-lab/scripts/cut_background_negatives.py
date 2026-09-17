#!/usr/bin/env python3
"""Cut 2-second negative clips from fma (music) + FSD50k (noise) samples.

These are the 'music bleed / environment noise' negatives: pure background
audio that must never trigger the wake word. Zero MiniMax cost — the samples
downloaded from the official openwakeword resources already.

Layout (matches what train.py expects for negatives):
  oww-data/output/喂Nora/negative_train/<uuid>.wav
  oww-data/output/喂Nora/negative_test/<uuid>.wav

16 kHz mono WAV required (data.py:674). fma/fsd50k samples are already 16k
mono WAV per the official notebook packaging; conversion is a safety net.
"""
from __future__ import annotations

import random
import subprocess
import uuid
import wave
from pathlib import Path

import numpy as np

SRC_DIRS = [
    Path("/home/norty/workspace/voice-lab-data/oww-data/background/fma_sample"),
    Path("/home/norty/workspace/voice-lab-data/oww-data/background/fsd50k_sample"),
]
OUT = Path("/home/norty/workspace/voice-lab-data/oww-data/output/喂Nora")
N_TRAIN = 5000   # per source dir
N_TEST = 500
CLIP_S = 2.0     # 2-second clips (matches total_length min)
SR = 16000


def load_wav(p: Path) -> np.ndarray | None:
    try:
        with wave.open(str(p)) as w:
            rate = w.getframerate()
            ch = w.getnchannels()
            n = w.getnframes()
            if rate != SR or ch != 1:
                # convert via ffmpeg (safety net)
                tmp = p.with_suffix(".tmp.wav")
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(p), "-ar", str(SR), "-ac", "1", str(tmp)],
                    check=True, capture_output=True,
                )
                with wave.open(str(tmp)) as w2:
                    data = np.frombuffer(w2.readframes(w2.getnframes()), dtype=np.int16).astype(np.float32)
                tmp.unlink(missing_ok=True)
                return data
            data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)
            return data
    except Exception:
        return None


def main() -> int:
    rng = random.Random(42)
    train_dir = OUT / "negative_train"
    test_dir = OUT / "negative_test"
    train_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    for src_dir in SRC_DIRS:
        files = sorted(src_dir.rglob("*.wav")) if src_dir.exists() else []
        print(f"{src_dir.name}: {len(files)} source wavs", flush=True)
        if not files:
            continue
        # target counts per source dir
        have_train = len(list(train_dir.glob("*.wav")))
        for split, n_target in ((train_dir, N_TRAIN), (test_dir, N_TEST)):
            need = max(0, n_target - len(list(split.glob("*.wav"))))
            made = 0
            while made < need and files:
                f = rng.choice(files)
                a = load_wav(f)
                if a is None or len(a) < int(SR * CLIP_S * 0.5):
                    files.remove(f)
                    continue
                n_clip = int(SR * CLIP_S)
                n_full = len(a) // n_clip
                if n_full == 0:
                    files.remove(f)
                    continue
                # take 1 random 2s chunk per source file, then drop it
                start = rng.randrange(0, max(1, n_full)) * n_clip
                seg = a[start : start + n_clip]
                seg = seg[:n_clip]
                if len(seg) < n_clip:
                    seg = np.pad(seg, (0, n_clip - len(seg)))
                out = split / f"{uuid.uuid4().hex}.wav"
                with wave.open(str(out), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(SR)
                    w.writeframes(seg.astype(np.int16).tobytes())
                made += 1
                files.remove(f)
            print(f"  {split.name}: +{made} (total {len(list(split.glob('*.wav')))})", flush=True)
    print(f"done: negatives -> {OUT}/negative_*", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
