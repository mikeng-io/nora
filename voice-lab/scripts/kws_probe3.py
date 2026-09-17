
import os, glob, json, tempfile, subprocess
os.environ.setdefault("MODELSCOPE_CACHE","/home/norty/workspace/voice-lab-data/models/ms")
from funasr import AutoModel
P="/home/norty/workspace/voice-lab-data/models/ms/models/iic--speech_charctc_kws_phone-xiaoyun/snapshots/master"
OUT=tempfile.mkdtemp(prefix="kwsout_")

def run(keywords, wavs):
    m = AutoModel(model=P, keywords=keywords, device="cpu", disable_update=True)
    res=[]
    for w in wavs:
        try:
            r = m.generate(input=w, cache={}, output_dir=OUT, disable_pbar=True, key=[os.path.basename(w)])
            res.append((os.path.basename(w), json.dumps(r, ensure_ascii=False)[:200]))
        except Exception as e:
            res.append((os.path.basename(w), f"ERR {type(e).__name__}: {str(e)[:120]}"))
    return res

# convert clips
for f in glob.glob("/home/norty/workspace/voice-lab-data/clips/wake/*.mp3"):
    w=f[:-4]+".wav"
    if not os.path.exists(w):
        subprocess.run(["ffmpeg","-v","error","-y","-i",f,"-ar","16000","-ac","1","-sample_fmt","s16",w])
wavs = sorted(glob.glob("/home/norty/workspace/voice-lab-data/clips/wake/*.wav"))

KW = "喂 nora,喂 你 好,小 云 小 云"
print("=== keywords:", KW, "===")
for n,r in run(KW, wavs):
    print(f"  {n:26s} {r}")
print("=== control: 小云小云 example through same path ===")
for n,r in run("小 云 小 云", [P+"/example/kws_xiaoyunxiaoyun.wav"]):
    print(f"  {n:26s} {r}")
