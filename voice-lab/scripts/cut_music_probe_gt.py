#!/usr/bin/env python3
"""Re-cut the music-probe recording using SenseVoice sentence_info timestamps
(ground truth) instead of manual estimates."""
from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import numpy as np
from funasr import AutoModel

SRC = Path("/home/norty/workspace/voice-lab-data/sidecar/recordings/100000000000000001-1789410577532.wav")
OUT = Path("/home/norty/workspace/voice-lab-data/data/realprobe/music-segments")
OUT.mkdir(parents=True, exist_ok=True)

model = AutoModel(model="iic/SenseVoiceSmall", disable_update=True)
res = model.generate(input=str(SRC), language="yue", use_itn=False, batch_size_s=60)
sentences = res[0].get("sentence_info") or []
print("sentence_info:")
for s in sentences:
    print(f"  [{s.get('start',0)/1000:.1f}-{s.get('end',0)/1000:.1f}s] {s.get('text','')}")


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


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        n = w.getnframes(); ch = w.getnchannels(); rate = w.getframerate()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        a = a[::ch]
    return a.astype(np.float32), rate


# Label map from text prefix (operator script order):
POS_TEXTS = ["喂 Nora。", "喂 Nora 下一首。", "喂 Nora 停一停。", "喂 Nora 幫我播歌。", "喂 Nora 而家幾點呀？", "喂 Nora 唔該。"]

a, rate = read_wav(SRC)
manifest = []
idx = 0
for s in sentences:
    text = s.get("text", "")
    start = s.get("start", 0) / 1000
    end = s.get("end", 0) / 1000
    label = "pos" if any(p.replace("，", "").replace("。", "").replace("？", "").replace("？", "") in text.replace(" ", "") for p in POS_TEXTS) else "neg"
    # Heuristic: pos if contains 喂 and (Nora/nora); else neg unless obviously '好喇多謝'
    if "喂" in text and ("ora" in text or "Nora" in text):
        label = "pos"
    elif "多謝" in text or "好喇" in text or "忙唔忙" in text or "笑咗" in text or "少咗" in text:
        label = "neg"
    name = f"seg_{idx:02d}_{label}_{start:.1f}-{end:.1f}s.wav"
    seg = a[int(start * rate):int(end * rate)]
    if len(seg) < rate * 0.3:
        idx += 1
        continue
    write_wav(OUT / name, seg, rate)
    manifest.append({"file": name, "label": label, "text": text, "start_s": round(start, 2), "end_s": round(end, 2)})
    print(f"  {label:3s} {start:5.1f}-{end:5.1f}s -> {name}  ({text})")
    idx += 1

(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
print(f"total: {len(manifest)}")
