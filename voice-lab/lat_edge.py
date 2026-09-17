import asyncio, time, edge_tts
TEXT = "好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。"
async def one(v):
    t0 = time.perf_counter(); ttfa = None; total = 0
    c = edge_tts.Communicate(TEXT, v)
    async for chunk in c.stream():
        if chunk["type"] == "audio":
            if ttfa is None: ttfa = time.perf_counter() - t0
            total += len(chunk["data"])
    return ttfa, time.perf_counter() - t0, total
async def m():
    print(f"{'voice':30s} {'TTFA(ms)':>9s} {'total(ms)':>10s} {'bytes':>8s}")
    for v in ["zh-HK-HiuGaaiNeural", "zh-HK-HiuMaanNeural", "zh-HK-WanLungNeural"]:
        for _ in range(2):
            t, d, b = await one(v)
            print(f"{v:30s} {(t or 0)*1000:9.0f} {d*1000:10.0f} {b:8d}")
asyncio.run(m())
