
import asyncio, edge_tts, os, random, subprocess, json
random.seed(7)
D="/home/norty/workspace/voice-lab-data/data/sepprobe"; os.makedirs(D, exist_ok=True)
VOICES=["zh-HK-HiuGaaiNeural","zh-HK-HiuMaanNeural","zh-HK-WanLungNeural"]
POS=["喂 Nora","喂，Nora。","喂 Nora 呀","喂 Nora 幫我","喂 Nora 播歌","喂 Nora 下一首","喂 Nora 得唔得","喂 Nora 幫手","喂 Nora 唔該","喂 Nora 停一停"]
NEG_WORD=["喂","喂你幫我睇下","我琴日同 Nora 傾咗好耐","佢話喂 Nora 嗰陣我笑咗","Nora 係邊個","你搵 Nora 做咩","同 Nora 講聲","我聽日搵 Nora","Nora 今日冇上線","喂你食咗飯未"]
NEG_GEN=["今日天氣唔錯","我想約你去食飯","聽日三點地鐵站等","呢個 file 幫我睇下","我而家喺公司","開會開到六點","你部電話幾多錢","記得帶八達通","落雨記得帶傘","我聽朝早八點起身"]
RATES=["+0%","-10%","+10%","-20%","+15%"]
PITCH=["+0Hz","-20Hz","+20Hz","-30Hz","+30Hz"]

async def gen(text, voice, rate, pitch, path):
    for a in range(3):
        try:
            c=edge_tts.Communicate(text, voice, rate=rate, pitch=pitch); await c.save(path); return True
        except Exception as e:
            await asyncio.sleep(0.6*a)
    return False

async def main():
    tasks=[]
    pos_meta=[]; neg_meta=[]
    for i in range(150):
        t=random.choice(POS); v=random.choice(VOICES); r=random.choice(RATES); p=random.choice(PITCH)
        fn=f"{D}/pos_{i:04d}.mp3"; pos_meta.append({"f":fn,"text":t,"voice":v,"rate":r,"pitch":p})
        tasks.append(gen(t,v,r,p,fn))
    for i in range(150):
        t=random.choice(NEG_WORD+NEG_GEN); v=random.choice(VOICES); r=random.choice(RATES); p=random.choice(PITCH)
        fn=f"{D}/neg_{i:04d}.mp3"; neg_meta.append({"f":fn,"text":t,"voice":v,"rate":r,"pitch":p})
        tasks.append(gen(t,v,r,p,fn))
    # sequential-with-semaphore to avoid throttling
    sem=asyncio.Semaphore(6)
    async def run(t):
        async with sem: return await t
    res=await asyncio.gather(*[run(t) for t in tasks])
    print("generated", sum(res), "/", len(res))
    json.dump({"pos":pos_meta,"neg":neg_meta}, open(f"{D}/meta.json","w"))
asyncio.run(main())
