
import os, glob, json, tempfile, subprocess
os.environ.setdefault("MODELSCOPE_CACHE","/home/norty/workspace/voice-lab-data/models/ms")
from funasr import AutoModel
P="/home/norty/workspace/voice-lab-data/models/ms/models/iic--speech_charctc_kws_phone-xiaoyun/snapshots/master"

def run(keywords, wavs, tag):
    OUT=tempfile.mkdtemp(prefix="kws_"+tag+"_")
    m = AutoModel(model=P, keywords=keywords, device="cpu", disable_update=True)
    out=[]
    for w in wavs:
        try:
            r = m.generate(input=w, cache={}, output_dir=OUT, disable_pbar=True)
            out.append((os.path.basename(w), json.dumps(r, ensure_ascii=False)[:200]))
        except Exception as e:
            out.append((os.path.basename(w), f"ERR {type(e).__name__}: {str(e)[:120]}"))
    return out

for f in glob.glob("/home/norty/workspace/voice-lab-data/clips/wake/*.mp3"):
    w=f[:-4]+".wav"
    if not os.path.exists(w):
        subprocess.run(["ffmpeg","-v","error","-y","-i",f,"-ar","16000","-ac","1","-sample_fmt","s16",w])
wavs = sorted(glob.glob("/home/norty/workspace/voice-lab-data/clips/wake/*.wav"))

# CONTROL FIRST: the model's own keyword on its own example audio.
print("=== CONTROL: keyword '小 云 小 云' on example wav ===")
for n,r in run("小 云 小 云", [P+"/example/kws_xiaoyunxiaoyun.wav"], "ctl"):
    print(f"  {n:26s} {r}")

print("=== TARGET: keyword '喂 諾 婷' (Cantonese name) on Cantonese clips ===")
for n,r in run("喂 諾 婷", wavs, "tgt"):
    print(f"  {n:26s} {r}")

print("=== TARGET2: keyword '喂 nora' on Cantonese clips ===")
for n,r in run("喂 nora", wavs, "tgt2"):
    print(f"  {n:26s} {r}")
