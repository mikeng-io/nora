#!/usr/bin/env python3
"""SenseVoice-verify each cut segment of the music probe (ground truth)."""
from __future__ import annotations

import json
from pathlib import Path

from funasr import AutoModel

OUT = Path("/home/norty/workspace/voice-lab-data/data/realprobe/music-segments")
model = AutoModel(model="iic/SenseVoiceSmall", disable_update=True)

manifest = json.loads((OUT / "manifest.json").read_text())
for m in manifest:
    path = OUT / m["file"]
    res = model.generate(input=str(path), language="yue", use_itn=False, batch_size_s=60)
    text = res[0]["text"] if res else "(none)"
    import re
    text = re.sub(r"<\|[^|]*\|>", "", text).strip()
    flag = "OK" if m["text"][:3].replace("，", "").replace("。", "").replace("？", "") in text.replace(" ", "") or text[:3] in ("喂", "nora", "Nora") else "??"
    print(f"[{flag}] {m['label']:3s} {m['file']}")
    print(f"    expected: {m['text']}")
    print(f"    got     : {text}")
