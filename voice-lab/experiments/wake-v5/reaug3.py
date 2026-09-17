"""Streaming-style PREFIX negatives: window ends at a random point inside the clip (>=0.3s in), left zero-padded —
exactly what the sliding window sees mid-utterance. usage: reaug3.py <list> <out.npy> <rir_prob>"""
import sys, os, time, numpy as np
sys.path.insert(0,"/home/norty/workspace/voice-lab-data/oww-src")
import openwakeword.data as D
from openwakeword.utils import compute_features_from_generator
listfile,out,RIRP=sys.argv[1],sys.argv[2],float(sys.argv[3])
def prefix_clip(x, n_samples, sr=16000, start=None, end_jitter=0.2):
    L=len(x); e=int(np.random.uniform(min(0.3*sr,L-1),L)) if L>0.3*sr else L
    x=x[:e]; x=x[-n_samples:] if len(x)>n_samples else x
    dat=np.zeros(n_samples); dat[n_samples-len(x):]=x.numpy() if hasattr(x,"numpy") else x
    return dat
D.create_fixed_size_clip=prefix_clip
clips=[l.strip() for l in open(listfile) if l.strip()]
LAB="/home/norty/workspace/voice-lab-data/oww-data"
rir=sorted(os.path.join(LAB,"rir/mono16k",f) for f in os.listdir(LAB+"/rir/mono16k") if f.endswith((".wav",".flac")))[:1000]
bg=[]
for d in ("background/fma_sample","background/fsd50k_sample"):
    bg+=sorted(os.path.join(LAB,d,f) for f in os.listdir(os.path.join(LAB,d)) if f.endswith((".wav",".flac")))
bg=bg[:600]
probs={"SevenBandParametricEQ":0.25,"TanhDistortion":0.25,"PitchShift":0.25,"BandStopFilter":0.25,"AddColoredNoise":0.25,"AddBackgroundNoise":0.75,"Gain":1.0,"RIR":RIRP}
np.random.seed(1); import random, torch; random.seed(1); torch.manual_seed(1); torch.set_num_threads(4)
gen=D.augment_clips(clips,total_length=32000,batch_size=16,background_clip_paths=bg,RIR_paths=rir,augmentation_probabilities=probs)
t0=time.time(); compute_features_from_generator(gen,n_total=len(clips),clip_duration=32000,output_file=out,device="cpu",ncpu=4)
open(out+".txt","w").write("\n".join(clips)); print(f"{out}: {len(clips)} PREFIX clips RIR={RIRP} in {time.time()-t0:.0f}s",flush=True)
