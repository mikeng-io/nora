"""Command-only negatives (the tails of our positive phrases, WITHOUT 喂 Nora) via edge-tts zh-HK.
Targets the real false fires seen on Miles's recordings (而家幾點, 你睇下, 幫我 check...). Scratchpad only."""
import asyncio, os, random, subprocess, sys, edge_tts
S="/home/norty/workspace/voice-lab-data/scratch/cmdneg/"
PHRASES=["而家幾點呀","而家幾點","下一首","暫停","繼續","唔該","大聲啲","細聲啲","停一停","幫我播歌","我返嚟喇","你喺度嗎","呀",
         "你好","你睇下","你睇下而家天氣","幫我 check 下個 email 先","上面有冇人 online","聽日我哋玩唔玩呀","好啦唔該","多謝","而家天氣點呀",
         "播歌","幫我","得唔得","幫手","停","開大聲啲","落一首","返嚟喇"]
VOICES=["zh-HK-HiuGaaiNeural","zh-HK-HiuMaanNeural","zh-HK-WanLungNeural"]
RATES=["-15%","-5%","+0%","+10%","+20%"]; PITCH=["-20Hz","-10Hz","+0Hz","+10Hz","+20Hz"]
random.seed(0); jobs=[]
for i,p in enumerate(PHRASES):
    for v in VOICES:
        for k in range(3):
            jobs.append((f"{i:02d}-{p.replace(' ','_')}-{v.split('-')[2][:6]}-{k}", p, v, random.choice(RATES), random.choice(PITCH)))
sem=asyncio.Semaphore(6)
async def one(name,text,voice,rate,pitch):
    out=S+"mp3/"+name+".mp3"
    if os.path.exists(S+"wav/"+name+".wav"): return
    async with sem:
        for attempt in range(3):
            try: await edge_tts.Communicate(text,voice,rate=rate,pitch=pitch).save(out); break
            except Exception as e:
                if attempt==2: print("FAIL",name,e); return
                await asyncio.sleep(2)
    subprocess.run(["ffmpeg","-loglevel","error","-y","-i",out,"-ac","1","-ar","16000","-sample_fmt","s16",S+"wav/"+name+".wav"],check=False)
async def main(): await asyncio.gather(*(one(*j) for j in jobs))
asyncio.run(main()); print("clips:",len(os.listdir(S+"wav")),"of",len(jobs))
