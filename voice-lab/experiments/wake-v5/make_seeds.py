"""Pick one prompt clip per Common Voice yue speaker (3-10 s, up_votes>=2, down_votes==0, shortest sentence),
export 16k int16 wav + transcript; also export the speaker's OTHER clips as real-speech negatives.
usage: make_seeds.py <n_speakers> <outdir>   (memory-safe: streams parquet in small batches)"""
import sys, os, io, json, glob, collections, numpy as np, soundfile as sf, pyarrow.parquet as pq
N=int(sys.argv[1]); OUT=sys.argv[2]; os.makedirs(OUT,exist_ok=True)
S="/home/norty/workspace/voice-lab-data/scratch/cv/data/"
def decode(a):
    if a.get("array") is not None: x=np.asarray(a["array"],dtype=np.float32); sr=a["sampling_rate"]
    else: x,sr=sf.read(io.BytesIO(a["bytes"]),dtype="float32")
    if x.ndim>1: x=x.mean(1)
    if sr!=16000:
        import librosa; x=librosa.resample(x,orig_sr=sr,target_sr=16000)
    return x
# pass 1: metadata only
rows=[]
for f in sorted(glob.glob(S+"*.parquet")):
    pf=pq.ParquetFile(f)
    if "client_id" not in pf.schema_arrow.names: continue
    i=0
    for b in pf.iter_batches(batch_size=500,columns=["client_id","sentence","up_votes","down_votes","path"]):
        for r in b.to_pylist(): rows.append((f,i,r)); i+=1
by=collections.defaultdict(list)
for f,i,r in rows: by[r["client_id"]].append((f,i,r))
spk=sorted(by, key=lambda k:-len(by[k]))[:N*3]  # candidates with most clips first
print("speakers available",len(by),"| taking",N)
# pass 2: decode candidate speakers' clips
need={k for k in spk}; picked=0; meta=[]
for f in sorted(glob.glob(S+"*.parquet")):
    pf=pq.ParquetFile(f)
    if "client_id" not in pf.schema_arrow.names: continue
    i=0
    for b in pf.iter_batches(batch_size=32,columns=["client_id","sentence","up_votes","down_votes","audio"]):
        for r in b.to_pylist():
            if r["client_id"] in need and sum(1 for c in by[r["client_id"]] if c[0]=="dec")<8:  # cap 8 decoded clips/speaker (memory)
                x=decode(r["audio"]); d=len(x)/16000
                by[r["client_id"]].append(("dec",d,(np.clip(x,-1,1)*32767).astype(np.int16),r))
            i+=1
for k in spk:
    if picked>=N: break
    clips=[c for c in by[k] if c[0]=="dec"]
    good=[c for c in clips if 3.0<=c[1]<=10.0 and (c[3]["up_votes"] or 0)>=2 and (c[3]["down_votes"] or 0)==0]
    if not good: continue
    prompt=min(good,key=lambda c:len(c[3]["sentence"]))
    sid=f"spk{picked:03d}"; d=os.path.join(OUT,sid); os.makedirs(d,exist_ok=True)
    sf.write(os.path.join(d,"prompt.wav"),prompt[2],16000)
    others=[c for c in clips if c is not prompt][:6]
    for j,c in enumerate(others): sf.write(os.path.join(d,f"neg_real_{j}.wav"),c[2],16000)
    meta.append({"sid":sid,"client_id":k,"prompt_text":prompt[3]["sentence"],"prompt_dur":round(prompt[1],2),"n_real_negs":len(others)})
    picked+=1
json.dump(meta,open(os.path.join(OUT,"seeds.json"),"w"),ensure_ascii=False,indent=1)
print("picked",picked,"speakers; prompt dur median",np.median([m["prompt_dur"] for m in meta]),"| e.g.",meta[0])
