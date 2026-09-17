
import os, glob, numpy as np, torch, time, json
os.environ.setdefault("MODELSCOPE_CACHE","/home/norty/workspace/voice-lab-data/models/ms")
from funasr import AutoModel
from funasr.models.fsmn_kws.model import FsmnKWS
import funasr.models.fsmn_kws.model as _m

# upstream bug: writer attr missing on the model object (modelscope/FunASR issue) -> monkey patch
_orig = FsmnKWS.inference
def patched(self, *a, **kw):
    if not hasattr(self, "writer"):
        self.writer = None
    class W:
        def __setitem__(self, k, v): pass
        def __getitem__(self, k): return ""
    if self.writer is None:
        self.writer = W()
    return _orig(self, *a, **kw)
FsmnKWS.inference = patched

P="/home/norty/workspace/voice-lab-data/models/ms/models/iic--speech_charctc_kws_phone-xiaoyun/snapshots/master"
KW="喂 nora, nora, 喂 你 好"
m = AutoModel(model=P, keywords=KW, device="cpu", disable_update=True)
print("LOADED; keywords =", KW)

def score(wav):
    r = m.generate(input=wav, cache={}, disable_pbar=True)
    return r

clips = sorted(glob.glob("/home/norty/workspace/voice-lab-data/clips/wake/*.wav"))
if not clips:
    import subprocess
    for f in glob.glob("/home/norty/workspace/voice-lab-data/clips/wake/*.mp3"):
        w=f[:-4]+".wav"; subprocess.run(["ffmpeg","-v","error","-y","-i",f,"-ar","16000","-ac","1","-sample_fmt","s16",w]); clips.append(w)
    clips=sorted(clips)

for c in clips:
    try:
        r = score(c)
        print(f"{os.path.basename(c):24s} -> {json.dumps(r, ensure_ascii=False)[:220]}")
    except Exception as e:
        print(f"{os.path.basename(c):24s} -> ERR {type(e).__name__}: {str(e)[:160]}")
print("--- example (小云小云) control for pipeline sanity ---")
try:
    print(json.dumps(score(P+"/example/kws_xiaoyunxiaoyun.wav"), ensure_ascii=False))
except Exception as e: print("ERR", e)
