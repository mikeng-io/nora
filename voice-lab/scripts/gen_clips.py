#!/usr/bin/env python3
"""Generate Cantonese test clips via edge-tts (zh-HK) for SenseVoice evaluation."""
import asyncio
import edge_tts
from pathlib import Path

CLIPS = {
    "casual_convo": "喂，你今日得唔得閒呀？我想約你去食飯。",
    "numbers_dates": "我聽日三點喺地鐵站等你，記住帶埋張八達通。",
    "mixed_eng": "你部 iPhone 幾多錢？我想 check 下個 email 先。",
    "classic": "大家好，我係 Nora，好開心見到你哋！",
    "formal_news": "天文台表示，本港今日天氣炎熱，最高氣溫達三十二度。",
}

VOICE = "zh-HK-HiuGaaiNeural"

async def main() -> None:
    out = Path("clips")
    out.mkdir(exist_ok=True)
    for name, text in CLIPS.items():
        path = out / f"{name}.mp3"
        if path.exists():
            print(f"skip {name}")
            continue
        tts = edge_tts.Communicate(text, VOICE, rate="-10%")
        await tts.save(str(path))
        print(f"ok {name}: {text[:20]}...")

asyncio.run(main())
