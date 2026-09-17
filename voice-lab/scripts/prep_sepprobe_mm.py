#!/usr/bin/env python3
"""Prepare the MiniMax 6-voice wake set for the probe: mp3 -> 16k mono wav,
plus build a sepprobe-format meta.json with {f, text, voice, rate, pitch}.

Target dir mirrors data/sepprobe: data/sepprobe-mm/ (mp3+wav+meta.json),
so backbone_probe5.py can re-run the leave-one-variant-out evaluation with
the new speaker diversity WITHOUT touching the original edge-tts set.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

SRC = Path("/home/norty/workspace/voice-lab-data/data/wake-minimax")
OUT = Path("/home/norty/workspace/voice-lab-data/data/sepprobe-mm")
SR = "16000"


def to_wav(mp3: Path) -> Path:
    wav = OUT / "wav" / (mp3.stem + ".wav")
    wav.parent.mkdir(parents=True, exist_ok=True)
    if wav.exists() and wav.stat().st_size > 1000:
        return wav
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3), "-ar", SR, "-ac", "1", str(wav)],
        check=True, capture_output=True,
    )
    return wav


def main() -> None:
    meta = json.loads((SRC / "meta.json").read_text())
    pos = []
    for m in meta:
        mp3 = SRC / m["file"]
        if not mp3.exists():
            print(f"skip missing {mp3}", flush=True)
            continue
        wav = to_wav(mp3)
        pos.append({
            "f": f"{OUT}/{wav.stem}.mp3",  # probe: .mp3->.wav, D->W (absolute, original convention)
            "text": m["variant"],
            "voice": m["voice"],
            "rate": "0%",
            "pitch": "+0Hz",
            "src": "minimax",
        })
    # Copy the original negative set (unchanged) so the probe has negatives.
    src_meta = json.loads(Path("/home/norty/workspace/voice-lab-data/data/sepprobe/meta.json").read_text())
    neg = []
    for m in src_meta["neg"]:
        src_f = Path("/home/norty/workspace/voice-lab-data/data/sepprobe") / m["f"]
        # f is an absolute .mp3 path inside sepprobe in the original meta;
        # rewrite it to point at sepprobe-mm (mp3 sibling for convention, wav below).
        rel_name = Path(m["f"]).name
        out_f = OUT / rel_name
        if src_f.exists() and not out_f.exists():
            out_f.write_bytes(src_f.read_bytes())
        wav_src = Path("/home/norty/workspace/voice-lab-data/data/sepprobe") / "wav" / (Path(m["f"]).stem + ".wav")
        if wav_src.exists():
            wav_dst = OUT / "wav" / (Path(m["f"]).stem + ".wav")
            wav_dst.parent.mkdir(parents=True, exist_ok=True)
            if not wav_dst.exists():
                wav_dst.write_bytes(wav_src.read_bytes())
        neg.append({**m, "f": f"{OUT}/{rel_name}", "src": "edge-tts"})
    (OUT / "meta.json").write_text(json.dumps({"pos": pos, "neg": neg}, ensure_ascii=False, indent=1))
    print(f"pos: {len(pos)}  neg: {len(neg)}  -> {OUT}")


if __name__ == "__main__":
    main()
