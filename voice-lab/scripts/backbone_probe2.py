
import os, json, numpy as np, subprocess, wave
import openwakeword.utils as u
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold, GroupShuffleSplit
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.preprocessing import StandardScaler

D="/home/norty/workspace/voice-lab-data/data/sepprobe"; W=D+"/wav"
SR=16000; WIN=int(3*SR); meta=json.load(open(f"{D}/meta.json"))
def read_wav(p):
    with wave.open(p) as w: return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
items=[]
for kind in ("pos","neg"):
    for m in meta[kind]:
        wav=m["f"].replace(".mp3",".wav").replace(D, W)
        items.append((kind, m["text"], read_wav(wav).astype(np.float32)))
F=u.AudioFeatures(ncpu=8)
X=[];y=[];g=[]
for kind,txt,a in items:
    buf=np.zeros(WIN,dtype=np.float32)
    if len(a)>=WIN: buf[:]=a[-WIN:]
    else: buf[WIN-len(a):]=a
    e=F.embed_clips(np.array([buf],dtype=np.int16), batch_size=1)
    X.append(e[0]); y.append(1 if kind=="pos" else 0); g.append(txt)
X=np.array(X);y=np.array(y);g=np.array(g); Xf=X.reshape(len(X),-1)
print("pos",int(y.sum()),"neg",int((1-y).sum()),"| unique texts:",len(set(g)))

# GROUPED CV: no text may appear in both train and test -> real generalisation signal
gs=GroupShuffleSplit(n_splits=5, test_size=0.25, random_state=0); aucs=[];accs=[]
for tr,te in gs.split(Xf,y,groups=g):
    sc=StandardScaler().fit(Xf[tr]); clf=LogisticRegression(max_iter=5000).fit(sc.transform(Xf[tr]),y[tr])
    p=clf.predict_proba(sc.transform(Xf[te]))[:,1]
    aucs.append(roc_auc_score(y[te],p)); accs.append(accuracy_score(y[te],(p>0.5).astype(int)))
print(f"\n>>> GROUPED (unseen text) REAL TASK  AUC {np.mean(aucs):.3f} (+-{np.std(aucs):.3f})  ACC {np.mean(accs):.3f}")

# HARD NEGATIVE only: exclude negatives that literally contain 'Nora' (easier) -> keep confusable ones
hard=np.array([("Nora" not in t) if lab==0 else True for t,lab in zip(g,y)])
print("hard subset size", int(hard.sum()), "pos", int(y[hard].sum()), "neg", int((1-y[hard]).sum()))
gs2=GroupShuffleSplit(n_splits=5,test_size=0.25,random_state=0); a3=[]
for tr,te in gs2.split(Xf[hard],y[hard],groups=g[hard]):
    if len(set(y[hard][te]))<2: continue
    sc=StandardScaler().fit(Xf[hard][tr]); clf=LogisticRegression(max_iter=5000).fit(sc.transform(Xf[hard][tr]),y[hard][tr])
    a3.append(roc_auc_score(y[hard][te],clf.predict_proba(sc.transform(Xf[hard][te]))[:,1]))
print(f">>> HARD-NEG only (no 'Nora' in negatives)  AUC {np.mean(a3):.3f} (+-{np.std(a3):.3f})")
