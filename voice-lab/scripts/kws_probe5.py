
import os, glob, json, tempfile, subprocess, asyncio, edge_tts
os.environ.setdefault("MODELSCOPE_CACHE","/home/norty/workspace/voice-lab-data/models/ms")
from funasr import AutoModel
P="/home/norty/workspace/voice-lab-data/models/ms/models/iic--speech_charctc_kws_phone-xiaoyun/snapshots/master"
D="/home/norty/workspace/voice-lab-data/clips/kws"
os.makedirs(D, exist_ok=True)
LINES=[("mand_nihao","你好","zh-CN-XiaoxiaoNeural"),
       ("yue_wai_nihao","喂，你好。","zh-HK-HiuMaanNeural"),
       ("mand_nihaonora","你好 Nora","zh-CN-XiaoxiaoNeural")]
async def gen(k,t,v):
    await edge_tts.Communicate(t,v).save(f"{D}/{k}.mp3")
async def main():
    for k,t,v in LINES: await gen(k,t,v)
asyncio.run(main())
for k,_,_ in LINES:
    subprocess.run(["ffmpeg","-v","error","-y","-i",f"{D}/{k}.mp3","-ar","16000","-ac","1","-sample_fmt","s16",f"{D}/{k}.wav"])

def run(kw, wavs, tag):
    OUT=tempfile.mkdtemp(prefix="k_"+tag+"_")
    m=AutoModel(model=P, keywords=kw, device="cpu", disable_update=True)
    for w in wavs:
        try:
            r=m.generate(input=w, cache={}, output_dir=OUT, disable_pbar=True)
            print(f"  [{tag}] {os.path.basename(w):24s} {json.dumps(r,ensure_ascii=False)[:150]}")
        except Exception as e:
            print(f"  [{tag}] {os.path.basename(w):24s} ERR {type(e).__name__}: {str(e)[:100]}")

wavs=[f"{D}/{k}.wav" for k,_,_ in LINES]
print("=== keyword '你 好' (all chars IN vocab) ===")
run("你 好", wavs, "nihao")
print("=== keyword '喂 你 好' ===")
run("喂 你 好", wavs, "wai_nihao")
