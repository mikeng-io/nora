"""Acceptance eval = the user's exp5 (peak-score AUC on raw test wavs) + exp7 (debounced FP/hr on the 11h validation set)
+ trap-negative FP@0.5 + positive recall@0.5, on an arbitrary ONNX. Memory-safe (chunked). usage: accept.py <onnx> [n_per_class]"""
import sys, os, re, glob, wave, time, numpy as np, onnxruntime as ort
sys.path.insert(0,"/home/norty/workspace/voice-lab-data/oww-src")
from openwakeword.model import Model
from numpy.lib.stride_tricks import sliding_window_view
ONNX=sys.argv[1]; N=int(sys.argv[2]) if len(sys.argv)>2 else 400
CORP="/home/norty/workspace/voice-lab-data/oww-data/output/喂Nora"; VAL="/home/norty/workspace/voice-lab-data/oww-data/features/validation_set_features.npy"
oww=Model(wakeword_models=[ONNX],inference_framework="onnx")
def read(p):
    with wave.open(p) as w:
        if w.getsampwidth()!=2 or w.getframerate()!=16000: raise ValueError(f"{p}: not 16-bit 16 kHz")
        pcm=np.frombuffer(w.readframes(w.getnframes()),np.int16)
    return pcm.reshape(-1,w.getnchannels()).mean(1).astype(np.int16) if w.getnchannels()>1 else pcm
def peak(p):
    pcm=read(p); oww.reset(); s=[float(list(oww.predict(pcm[i:i+1280]).values())[0]) for i in range(0,len(pcm)-1279,1280)]
    return max(s) if s else None
rng=np.random.default_rng(0)
def sample(files,n): files=sorted(files); return [files[i] for i in sorted(rng.choice(len(files),min(n,len(files)),replace=False))]
pos_all=glob.glob(CORP+"/positive_test/*.wav"); pat=lambda p: re.sub(r'^\d+-|-\d+\.wav$','',os.path.basename(p))
BARE={"喂-Nora","喂，Nora","喂Nora","喂-Nora-呀"}
pos_bare=[p for p in pos_all if pat(p) in BARE]
negs_all=[p for p in glob.glob(CORP+"/negative_test/*.wav") if not re.fullmatch(r'[0-9a-f]{32}\.wav',os.path.basename(p))]
negs_nonora=[p for p in negs_all if "Nora" not in os.path.basename(p)]
trap_pats=("喂老啦","喂勞拉","喂樂拉","喂娜娜","喂露娜","喂羅拉","喂安樂","喂多啦","喂阿娜")
traps=[p for p in negs_all if os.path.basename(p).startswith(trap_pats)]+glob.glob(CORP+"/negative_test/[0-9a-f]*.wav")
traps=[p for p in traps if re.fullmatch(r'[0-9a-f]{32}\.wav',os.path.basename(p)) or os.path.basename(p).startswith(trap_pats)]
def auc(a,b):
    allv=np.concatenate([a,b]); r=allv.argsort().argsort()+1; return (r[:len(a)].sum()-len(a)*(len(a)+1)/2)/(len(a)*len(b))
t0=time.time()
P_all=np.array([peak(p) for p in sample(pos_all,N)]); P_bare=np.array([peak(p) for p in sample(pos_bare,N)])
Nn=np.array([peak(p) for p in sample(negs_nonora,N)]); Na=np.array([peak(p) for p in sample(negs_all,N)]); T=np.array([peak(p) for p in sample(traps,N)])
print(f"{os.path.basename(ONNX)}  ({time.time()-t0:.0f}s)")
print(f"  exp5 AUC pos(all 15 patterns) vs speech-neg(all, incl Nora-mentions) = {auc(P_all,Na):.4f}   [user's exp5 definition]")
print(f"  exp5 AUC pos(bare wake)        vs speech-neg(no Nora-mentions)      = {auc(P_bare,Nn):.4f}")
print(f"  recall@0.5: all-pattern={(P_all>=0.5).mean():.3f}  bare-wake={(P_bare>=0.5).mean():.3f}   (n={len(P_all)},{len(P_bare)})")
print(f"  FP@0.5: speech-neg(no Nora)={(Nn>=0.5).mean():.3f}  speech-neg(all)={(Na>=0.5).mean():.3f}  TRAP(phonetic)={(T>=0.5).mean():.3f} (n={len(T)})")
# exp7: debounced FP/hr on validation set, chunked
s=ort.InferenceSession(ONNX,providers=["CPUExecutionProvider"]); name=s.get_inputs()[0].name
F=np.load(VAL,mmap_mode="r"); Nw=F.shape[0]-15; sc=np.empty(Nw,np.float32)
for a in range(0,Nw,20000):
    b=min(a+20000,Nw); W=np.ascontiguousarray(sliding_window_view(np.asarray(F[a:b+15]),(16,96)).squeeze(1))
    for i in range(len(W)): sc[a+i]=s.run(None,{name:W[i][None]})[0].ravel()[0]
hrs=Nw*0.08/3600
def acts(th,deb=25):
    n,last=0,-10**9
    for i in np.where(sc>=th)[0]:
        if i-last>deb: n+=1; last=i
    return n
def runs(th,minlen):
    idx=np.where(sc>=th)[0]
    if len(idx)==0: return 0
    br=np.where(np.diff(idx)>1)[0]; starts=np.r_[0,br+1]; ends=np.r_[br,len(idx)-1]
    return int(((ends-starts+1)>=minlen).sum())
print(f"  exp7 consecutive-frame runs >=0.5: any={runs(0.5,1)} ({runs(0.5,1)/hrs:.2f}/hr)  len>=2: {runs(0.5,2)} ({runs(0.5,2)/hrs:.2f}/hr)  len>=3: {runs(0.5,3)} ({runs(0.5,3)/hrs:.2f}/hr)")
print(f"  exp7 validation {hrs:.2f}h: raw frames>=0.5 = {(sc>=0.5).sum()} ({(sc>=0.5).sum()/11.3:.1f}/hr train.py-style) | debounced activations @0.5={acts(0.5)} -> {acts(0.5)/hrs:.2f}/hr  @0.7 -> {acts(0.7)/hrs:.2f}/hr  @0.9 -> {acts(0.9)/hrs:.2f}/hr")
