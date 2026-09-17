
import os, json, numpy as np, wave
import openwakeword.utils as u
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

D="/home/norty/workspace/voice-lab-data/data/sepprobe-mm"; W=D+"/wav"
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
pos_texts=sorted(set(t for t,l in zip(g,y) if l==1))
print(len(pos_texts),"positive variants; leave-one-variant-out (MiniMax 6-voice set):")
ok=0
for held_out in pos_texts:
    tr=np.array([(l==0) or (t!=held_out) for t,l in zip(g,y)])
    te=np.array([(l==1) and (t==held_out) for t,l in zip(g,y)])
    neg_idx=np.where(y==0)[0]
    te=np.concatenate([np.where(te)[0], neg_idx])
    yte=y[te]
    if len(set(yte))<2: continue
    sc=StandardScaler().fit(Xf[tr]); clf=LogisticRegression(max_iter=5000).fit(sc.transform(Xf[tr]),y[tr])
    p=clf.predict_proba(sc.transform(Xf[te]))[:,1]
    auc=roc_auc_score(yte,p)
    fp=int(((yte==0)&(p>=0.5)).sum()); fn=int(((yte==1)&(p<0.5)).sum())
    npos=int((yte==1).sum()); nneg=int((yte==0).sum())
    print(f"  '{held_out}': AUC {auc:.3f} | FP {fp}/{nneg} | FN {fn}/{npos}")
    ok+=1
print("variants evaluated:", ok)
