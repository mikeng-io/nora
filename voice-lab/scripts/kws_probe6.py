
import os, glob, json, tempfile, subprocess, asyncio, edge_tts
os.environ.setdefault("MODELSCOPE_CACHE","/home/norty/workspace/voice-lab-data/models/ms")
from funasr import AutoModel
P="/home/norty/workspace/voice-lab-data/models/ms/models/iic--speech_charctc_kws_phone-xiaoyun/snapshots/master"
D="/home/norty/workspace/voice-lab-data/clips/kws2"; os.makedirs(D, exist_ok=True)
# Isolate the confound: same TEXT, only the LANGUAGE/pronunciation differs.
LINES=[("yue_nihao_only","你好。","zh-HK-HiuMaanNeural"),
       ("mand_nihao_only","你好。","zh-CN-XiaoxiaoNeural"),
       ("yue_xiaoyun","小雲小雲。","zh-HK-HiuMaanNeural"),
       ("mand_xiaoyun","小云小云。","zh-CN-XiaoxiaoNeural")]
async def main():
    for k,t,v in LINES:
        await edge_tts.Communicate(t,v).save(f"{D}/{k}.mp3")
asyncio.run(main())
for k,_,_ in LINES:
    subprocess.run(["ffmpeg","-v","error","-y","-i",f"{D}/{k}.mp3","-ar","16000","-ac","1","-sample_fmt","s16",f"{D}/{k}.wav"])

def run(kw, wavs, tag):
    OUT=tempfile.mkdtemp(prefix="k2_"+tag+"_")
    m=AutoModel(model=P, keywords=kw, device="cpu", disable_update=True)
    for w in wavs:
        r=m.generate(input=w, cache={}, output_dir=OUT, disable_pbar=True)
        print(f"  [{tag}] {os.path.basename(w):22s} {json.dumps(r,ensure_ascii=False)[:130]}")

wavs=[f"{D}/{k}.wav" for k,_,_ in LINES]
print("KEYWORD = '你 好'  (Mandarin vs Cantonese, same text)")
run("你 好", wavs, "nihao")
print("KEYWORD = '小 云 小 云'  (Mandarin vs Cantonese, same text) — the model's OWN word")
run("小 云 小 云", wavs, "xiaoyun")
