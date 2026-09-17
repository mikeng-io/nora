import asyncio, edge_tts
TEXT = "喂，我係 Nora。而家幫你播下一首，唔該等等。"
async def m():
    for v in ["zh-HK-HiuGaaiNeural","zh-HK-HiuMaanNeural","zh-HK-WanLungNeural"]:
        c = edge_tts.Communicate(TEXT, v)
        await c.save(f"tts-cmp/edge_{v}.mp3")
        print("wrote", v)
asyncio.run(m())
