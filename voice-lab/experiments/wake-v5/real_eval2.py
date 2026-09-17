"""Real-audio eval: stream head over Miles's two Discord recordings; per STT-transcribed burst report peak score.
pos = transcript mentions nora/nova/喂ra ; neg = everything else. usage: real_eval2.py <onnx>..."""
import sys, json, re, wave, numpy as np
sys.path.insert(0,"/home/norty/workspace/voice-lab-data/oww-src")
from openwakeword.model import Model
S="/home/norty/workspace/voice-lab-data/scratch/real/"
man=json.load(open("/home/norty/workspace/voice-lab-data/data/realprobe/manifest.json"))
tr={l.split("\t")[0]:l.rstrip("\n").split("\t")[1] if "\t" in l else "" for l in open(S+"transcripts.tsv")}
SRC={"live-miles-repaired.wav":S+"live-miles-repaired.wav","100000000000000001-1789405579646.wav":S+"rec2.wav"}
def read(p):
    with wave.open(p) as w: return np.frombuffer(w.readframes(w.getnframes()),np.int16)
POS=re.compile(r"nora|nova|喂 ?ra|諾拉|nara",re.I)
for onnx in sys.argv[1:]:
    oww=Model(wakeword_models=[onnx],inference_framework="onnx"); streams={}
    for src,p in SRC.items():
        pcm=read(p); oww.reset(); t=[];s=[]
        for i in range(0,len(pcm)-1279,1280): s.append(float(list(oww.predict(pcm[i:i+1280]).values())[0])); t.append((i+1280)/16000)
        streams[src]=(np.array(t),np.array(s))
    pos=[];neg=[]
    for e in man:
        f=e["file"].split("/")[-1]; txt=tr.get(f,""); t,s=streams[e["src"]]
        a,b=e["start_s"],e["start_s"]+e["duration_s"]+0.4; pk=s[(t>=a)&(t<=b)].max() if ((t>=a)&(t<=b)).any() else 0
        (pos if POS.search(txt) else neg).append((pk,txt,f))
    P=np.array([p[0] for p in pos]); N=np.array([n[0] for n in neg])
    print(f"{onnx.split('/')[-1]}: POS n={len(P)} min={P.min():.3f} median={np.median(P):.3f} >=0.5: {(P>=0.5).sum()}/{len(P)} | NEG n={len(N)} max={N.max():.3f} >=0.5: {(N>=0.5).sum()}/{len(N)} median={np.median(N):.3f}")
    if "-v" in sys.argv[0] or len(sys.argv)==2:
        for pk,txt,f in sorted(pos): print(f"   POS {pk:.3f} {txt}")
        for pk,txt,f in sorted(neg,reverse=True)[:12]: print(f"   NEG {pk:.3f} {txt}")
