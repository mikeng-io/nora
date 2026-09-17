
import os, json, numpy as np, wave
import openwakeword.utils as u
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupShuffleSplit
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

D="/home/norty/workspace/voice-lab-data/data/sepprobe"; W=D+"/wav"
SR=16000; WIN=int(3*SR); meta=json.load(open(f"{D}/meta.json"))
def read_wav(p):
    with wave.open(p) as w: return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
F=u.AudioFeatures(ncpu=8)
X=[];y=[];g=[]
for kind in ("pos","neg"):
    for m in meta[kind]:
        wav=m["f"].replace(".mp3",".wav").replace(D,W)
        a=read_wav(wav).astype(np.float32)
        buf=np.zeros(WIN,dtype=np.float32)
        if len(a)>=WIN: buf[:]=a[-WIN:]
        else: buf[WIN-len(a):]=a
        e=F.embed_clips(np.array([buf],dtype=np.int16), batch_size=1)
        X.append(e[0]); y.append(1 if kind=="pos" else 0); g.append(m["text"])
X=np.array(X); y=np.array(y); g=np.array(g); Xf=X.reshape(len(X),-1)

# TEST: positives WITHOUT 喂 (bare 'Nora ...') vs all negatives — is the learned signal carrying?
pos_mask = y==1
import re
texts_pos = g[pos_mask]
print("positive text variants:", set(texts_pos))
# weak-label experiment: does the embedding separate on the SPOKEN phrase, or on trivial cues?
# control: positives vs EASY negatives only (neg_gen, no Nora/no 喂-confusables), grouped
meta2=json.load(open(f"{D}/meta.json"))
easy = np.array([ ("nora" not in m["text"].lower() and "喂" not in m["text"]) if yy==0 else True for m,yy in zip(meta2["pos"]+meta2["neg"], y)])
gs=GroupShuffleSplit(n_splits=5,test_size=0.25,random_state=0); aucs=[]
for tr,te in gs.split(Xf[easy],y[easy],groups=g[easy]):
    if len(set(y[easy][te]))<2: continue
    sc=StandardScaler().fit(Xf[easy][tr]); clf=LogisticRegression(max_iter=5000).fit(sc.transform(Xf[easy][tr]),y[easy][tr])
    aucs.append(roc_auc_score(y[easy][te],clf.predict_proba(sc.transform(Xf[easy][te]))[:,1]))
print(f">>> EASY-NEG (no Nora/no 喂 negatives)  AUC {np.mean(aucs):.3f} (+-{np.std(aucs):.3f})")
