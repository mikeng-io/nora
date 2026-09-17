
import asyncio, os, edge_tts
OUT="/home/norty/workspace/voice-lab-data/clips/wake"
os.makedirs(OUT, exist_ok=True)
VOICE="zh-HK-HiuMaanNeural"
LINES = {
 "pos_wai_nora":      "喂 Nora，幫我播陳奕迅。",
 "pos_wai_nora2":     "喂，Nora，下一首。",
 "pos_nora_alone":    "Nora 幫我暫停。",
 "neg_mention":       "我琴日同 Nora 傾咗好耐。",
 "neg_other":         "喂你幫我睇下呢個 file。",
 "neg_weinora_quote": "佢話「喂 Nora」嗰陣我真係笑咗。",
}
async def gen(k,t):
    c = edge_tts.Communicate(t, VOICE)
    await c.save(f"{OUT}/{k}.mp3")
async def main():
    for k,t in LINES.items():
        await gen(k,t); print("ok",k)
asyncio.run(main())
