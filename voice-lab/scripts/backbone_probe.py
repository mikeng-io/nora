
import os, json, numpy as np, subprocess, tempfile
import openwakeword.utils as u
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.preprocessing import StandardScaler

D="/home/norty/workspace/voice-lab-data/data/sepprobe"
W="/home/norty/workspace/voice-lab-data/data/sepprobe/wav"; os.makedirs(W, exist_ok=True)
SR=16000; WIN=int(3*SR)
meta=json.load(open(f"{D}/meta.json"))

def to_wav(mp3, wav):
    subprocess.run(["ffmpeg","-v","error","-y","-i",mp3,"-ar",str(SR),"-ac","1","-sample_fmt","s16",wav], check=True)
def read_wav(p):
    import wave
    with wave.open(p) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)

items=[]
for kind in ("pos","neg"):
    for m in meta[kind]:
        wav=m["f"].replace(".mp3",".wav").replace(f"{D}/",f"{W}/")
        if not os.path.exists(wav): to_wav(m["f"], wav)
        a=read_wav(wav).astype(np.float32)
        items.append((kind, m["f"].split("/")[-1], a, m["text"]))

F=u.AudioFeatures(ncpu=8)
feats=[]; labels=[]; texts=[]
for kind, name, a, txt in items:
    # align: end of clip at end of window (oww convention), clip centred near end
    buf=np.zeros(WIN, dtype=np.float32)
    if len(a)>=WIN: buf[:]=a[-WIN:]
    else:
        off=WIN-len(a)
        buf[off:]=a
    e=F.embed_clips(np.array([buf],dtype=np.int16) if buf.dtype!=np.int16 else np.array([buf]), batch_size=1)
    feats.append(e[0]); labels.append(1 if kind=="pos" else 0); texts.append(txt)
X=np.array(feats); y=np.array(labels)
Xf=X.reshape(len(X),-1)
print("X", Xf.shape, "pos", int(y.sum()), "neg", int((1-y).sum()))

# ---- REAL TASK: 5-fold CV logistic regression on oww embeddings
skf=StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
aucs=[]; accs=[]
for tr,te in skf.split(Xf,y):
    sc=StandardScaler().fit(Xf[tr])
    clf=LogisticRegression(max_iter=3000, C=1.0).fit(sc.transform(Xf[tr]), y[tr])
    p=clf.predict_proba(sc.transform(Xf[te]))[:,1]
    aucs.append(roc_auc_score(y[te],p)); accs.append(accuracy_score(y[te],(p>0.5).astype(int)))
print(f"\n>>> REAL TASK (Cantonese '喂 Nora' vs Cantonese negatives)")
print(f"    AUC  {np.mean(aucs):.3f}  (+-{np.std(aucs):.3f})   ACC {np.mean(accs):.3f}")

# ---- CONTROL 1: shuffled labels (what 'no signal' looks like)
rng=np.random.default_rng(0); ctrl=[]
for _ in range(5):
    ys=rng.permutation(y)
    skf2=StratifiedKFold(n_splits=5, shuffle=True, random_state=1); a2=[]
    for tr,te in skf2.split(Xf,ys):
        sc=StandardScaler().fit(Xf[tr]); clf=LogisticRegression(max_iter=3000).fit(sc.transform(Xf[tr]),ys[tr])
        a2.append(roc_auc_score(ys[te],clf.predict_proba(sc.transform(Xf[te]))[:,1]))
    ctrl.append(np.mean(a2))
print(f">>> CONTROL (shuffled labels, no signal)  AUC {np.mean(ctrl):.3f} (+-{np.std(ctrl):.3f})")
