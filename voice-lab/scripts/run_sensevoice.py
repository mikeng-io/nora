#!/usr/bin/env python3
"""Transcribe Cantonese clips with SenseVoiceSmall, compare vs reference text."""
import json
import sys
from pathlib import Path

from funasr import AutoModel

CLIPS = {
    "casual_convo": "喂，你今日得唔得閒呀？我想約你去食飯。",
    "numbers_dates": "我聽日三點喺地鐵站等你，記住帶埋張八達通。",
    "mixed_eng": "你部 iPhone 幾多錢？我想 check 下個 email 先。",
    "classic": "大家好，我係 Nora，好開心見到你哋！",
    "formal_news": "天文台表示，本港今日天氣炎熱，最高氣溫達三十二度。",
}

def main() -> None:
    print("loading model...", flush=True)
    model = AutoModel(model="iic/SenseVoiceSmall", disable_update=True)
    print("model loaded", flush=True)

    results = []
    for name, ref in CLIPS.items():
        path = Path("clips") / f"{name}.mp3"
        res = model.generate(
            input=str(path),
            language="auto",
            use_itn=True,
            batch_size_s=60,
        )
        text = res[0]["text"] if res else "(no result)"
        results.append({"clip": name, "reference": ref, "hypothesis": text})
        print(f"\n=== {name} ===", flush=True)
        print(f"  ref : {ref}", flush=True)
        print(f"  hyp : {text}", flush=True)

    Path("results/transcripts.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2)
    )
    print("\nwrote results/transcripts.json", flush=True)

if __name__ == "__main__":
    sys.exit(main())
