"""Common Voice yue (real speakers): make 2s int16 windows, clean and music-mixed at SNR in [-5,10] dB,
compute oww features (no other augmentation). Row-group streaming to keep memory low."""
import io, os, sys, numpy as np, pyarrow.parquet as pq, soundfile as sf, librosa, wave, random
sys.path.insert(0,"/home/norty/workspace/voice-lab-data/oww-src")
from openwakeword.utils import compute_features_from_generator
S="/home/norty/workspace/voice-lab-data/scratch/"
PQ=S+"cv/data/dev-00000-of-00006.parquet"; NW=int(sys.argv[1]) if len(sys.argv)>1 else 3000
random.seed(0); np.random.seed(0)
LAB="/home/norty/workspace/voice-lab-data/oww-data/background/fma_sample/"
music=[]
for f in sorted(os.listdir(LAB))[:40]:
    d,sr=sf.read(LAB+f,dtype="float32"); d=d.mean(1) if d.ndim>1 else d
    if sr!=16000: d=librosa.resample(d,orig_sr=sr,target_sr=16000)
    music.append(d)
pf=pq.ParquetFile(PQ); print("schema:",[f.name for f in pf.schema_arrow]); acol="audio"
def windows():
    n=0
    for rg in range(pf.num_row_groups):
        t=pf.read_row_group(rg,columns=[acol])
        for rec in t.column(acol).to_pylist():
            d,sr=sf.read(io.BytesIO(rec["bytes"]),dtype="float32"); d=d.mean(1) if d.ndim>1 else d
            if sr!=16000: d=librosa.resample(d,orig_sr=sr,target_sr=16000)
            d=d/ (np.abs(d).max()+1e-6)*0.5
            for k in range(len(d)//32000):
                yield d[k*32000:(k+1)*32000]; n+=1
                if n>=NW: return
def gen_clean():
    b=[]
    for w in windows():
        b.append((w*32767).astype(np.int16))
        if len(b)==64: yield np.stack(b); b=[]
    if b: yield np.stack(b)
def gen_music():
    b=[]
    for w in windows():
        m=random.choice(music); st=random.randint(0,max(0,len(m)-32000)); m=m[st:st+32000]
        if len(m)<32000: m=np.pad(m,(0,32000-len(m)))
        snr=random.uniform(-5,10); ps=(w**2).mean()+1e-9; pm=(m**2).mean()+1e-9
        m=m*np.sqrt(ps/(pm*10**(snr/10))); x=np.clip(w+m,-1,1)
        b.append((x*32767).astype(np.int16))
        if len(b)==64: yield np.stack(b); b=[]
    if b: yield np.stack(b)
for name,g in (("cv_clean",gen_clean()),("cv_music",gen_music())):
    compute_features_from_generator(g,n_total=NW,clip_duration=32000,output_file=S+f"cv/{name}.npy",device="cpu",ncpu=4)
    print(name, np.load(S+f"cv/{name}.npy",mmap_mode="r").shape, flush=True)
