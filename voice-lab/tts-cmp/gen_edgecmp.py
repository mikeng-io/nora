import asyncio, edge_tts
TEXT = "好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。"
# Does edge-tts pitch/rate get us closer to a "cute girl" register?
CASES = [
  ("HiuGaai_baseline",      "zh-HK-HiuGaaiNeural", "+0Hz",   "+0%"),
  ("HiuGaai_brighter",      "zh-HK-HiuGaaiNeural", "+35Hz",  "+0%"),
  ("HiuGaai_bright_fast",   "zh-HK-HiuGaaiNeural", "+35Hz",  "+8%"),
  ("HiuMaan_baseline",      "zh-HK-HiuMaanNeural", "+0Hz",   "+0%"),
  ("HiuMaan_brighter",      "zh-HK-HiuMaanNeural", "+35Hz",  "+0%"),
]
async def main():
    for label, voice, pitch, rate in CASES:
        c = edge_tts.Communicate(TEXT, voice, pitch=pitch, rate=rate)
        audio = b""
        async for ch in c.stream():
            if ch["type"] == "audio": audio += ch["data"]
        fn = f"samples/edgecmp/{label}.mp3"
        open(fn,"wb").write(audio)
        print(f"  {label:22s} {voice:22s} pitch={pitch:6s} rate={rate:5s} {len(audio):7d}B -> {fn}")
asyncio.run(main())
