#!/usr/bin/env python3
"""Cut the music-probe recording into per-utterance clips (manual timings from
the audio content transcribed at 02:33 — verified against the operator's
script) and label them for the real-audio wake probe.

Labels (from the operator's recording script + SenseVoice transcript):
  pos: 喂 Nora。 / 喂 Nora 下一首。 / 喂 Nora 停一停。 / 喂 Nora 幫我播歌。 /
       喂 Nora 而家幾點呀？ / 喂 Nora 唔該。
  neg: Nora 你今日忙唔忙呀？ / 佢話「喂 Nora」嗰陣我真係笑咗。 / 好啦，多謝。
"""
from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import numpy as np

SRC = Path("/home/norty/workspace/voice-lab-data/sidecar/recordings/100000000000000001-1789410577532.wav")
OUT = Path("/home/norty/workspace/voice-lab-data/data/realprobe/music-segments")
OUT.mkdir(parents=True, exist_ok=True)

# Manual boundaries in SECONDS from listening + SenseVoice full-text (21.2s total).
# Each clip = start_s, end_s, label, text
SEGMENTS = [
    (0.3, 1.7, "pos", "喂 Nora。"),
    (1.9, 3.6, "pos", "喂 Nora 下一首。"),
    (3.9, 5.6, "pos", "喂 Nora 停一停。"),
    (5.9, 7.8, "pos", "喂 Nora 幫我播歌。"),
    (8.1, 10.2, "pos", "喂 Nora 而家幾點呀？"),
    (10.6, 13.0, "neg", "Nora 你今日忙唔忙呀？"),
    (13.3, 15.9, "neg", "佢話「喂 Nora」嗰陣我真係笑咗。"),
    (16.2, 17.8, "pos", "喂 Nora 唔該。"),
    (18.1, 20.1, "neg", "好啦，多謝。"),
]


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        n = w.getnframes()
        ch = w.getnchannels()
        rate = w.getframerate()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        a = a[::ch]
    return a.astype(np.float32), rate


def write_wav(path: Path, a: np.ndarray, rate: int) -> None:
    data = a.astype(np.int16).tobytes()
    with open(path, "wb") as f:
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
    a, rate = read_wav(SRC)
    print(f"source: {len(a)/rate:.1f}s {rate}Hz")
    manifest = []
    for idx, (s, e, label, text) in enumerate(SEGMENTS):
        seg = a[int(s * rate):int(e * rate)]
        if len(seg) < rate * 0.3:
            print(f"  SKIP {idx}: too short ({len(seg)/rate:.2f}s)")
            continue
        name = f"seg_{idx:02d}_{label}_{s}-{e}s.wav"
        out = OUT / name
        write_wav(out, seg, rate)
        manifest.append({"file": name, "label": label, "text": text, "start_s": s, "end_s": e})
        print(f"  {label:3s} {s:5.1f}-{e:5.1f}s ({e-s:.1f}s) -> {name}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"total segments: {len(manifest)}")


if __name__ == "__main__":
    main()
