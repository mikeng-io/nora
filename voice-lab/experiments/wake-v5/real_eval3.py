"""Timeline real-audio metric on Miles's 2 Discord recordings.
Wake events = merged bursts whose STT transcript contains nora/nova/喂ra. For each: peak in [start-0.2, end+0.6].
False fires = debounced (2s) activations >=0.5 outside [wake_start-0.5, wake_end+1.5]. usage: real_eval3.py <onnx>..."""
import sys, json, re, wave, numpy as np
sys.path.insert(0,"/home/norty/workspace/voice-lab-data/oww-src")
from openwakeword.model import Model
S="/home/norty/workspace/voice-lab-data/scratch/real/"
man=json.load(open("/home/norty/workspace/voice-lab-data/data/realprobe/manifest.json"))
tr={l.split("\t")[0]:l.rstrip("\n").split("\t")[1] if "\t" in l else "" for l in open(S+"transcripts.tsv")}
SRC={"live-miles-repaired.wav":S+"live-miles-repaired.wav","100000000000000001-1789405579646.wav":S+"rec2.wav"}
POS=re.compile(r"nora|nova|喂 ?ra|諾拉|nara",re.I)
wakes={k:[] for k in SRC}
for e in man:
    if POS.search(tr.get(e["file"].split("/")[-1],"")): wakes[e["src"]].append([e["start_s"],e["start_s"]+e["duration_s"]])
for k in wakes:  # merge overlapping
    m=[]; 
    for a,b in sorted(wakes[k]):
        if m and a<=m[-1][1]+0.3: m[-1][1]=max(m[-1][1],b)
        else: m.append([a,b])
    wakes[k]=m
def read(p):
    with wave.open(p) as w: return np.frombuffer(w.readframes(w.getnframes()),np.int16)
def acts(t,s,th=0.5,deb=2.0):
    out=[];last=-1e9
    for ti,si in zip(t,s):
        if si>=th and ti-last>deb: out.append(ti); last=ti
    return out
for onnx in sys.argv[1:]:
    oww=Model(wakeword_models=[onnx],inference_framework="onnx"); peaks=[]; ff=[]; tot=0
    for src,p in SRC.items():
        pcm=read(p); oww.reset(); t=[];s=[]
        for i in range(0,len(pcm)-1279,1280): s.append(float(list(oww.predict(pcm[i:i+1280]).values())[0])); t.append((i+1280)/16000)
        t=np.array(t); s=np.array(s); tot+=t[-1]
        for a,b in wakes[src]:
            sel=(t>=a-0.2)&(t<=b+0.6); peaks.append(s[sel].max() if sel.any() else 0.0)
        for ta in acts(t,s):
            if not any(a-0.5<=ta<=b+1.5 for a,b in wakes[src]): ff.append((src[:8],round(ta,1)))
    P=np.array(peaks)
    print(f"{onnx.split('/')[-1]:24s} wakes={len(P)} min={P.min():.3f} med={np.median(P):.3f} >=0.6:{(P>=0.6).sum()}/{len(P)} | false fires={len(ff)} in {tot:.0f}s  {ff}")
