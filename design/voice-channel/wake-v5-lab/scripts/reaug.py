"""Re-augment a clip list locally with the SAME openwakeword pipeline as train.py, with controllable
end_jitter and RIR probability. Writes features .npy + a .txt of clip paths in row order.
usage: reaug.py <listfile> <out.npy> <end_jitter> <rir_prob>"""
import sys, os, functools, time, numpy as np
sys.path.insert(0,"/home/norty/workspace/voice-lab/oww-src")
import openwakeword.data as D
from openwakeword.utils import compute_features_from_generator
listfile,out,J,RIRP=sys.argv[1],sys.argv[2],float(sys.argv[3]),float(sys.argv[4])
clips=[l.strip() for l in open(listfile) if l.strip()]
orig=D.create_fixed_size_clip
D.create_fixed_size_clip=functools.partial(orig,end_jitter=J)
LAB="/home/norty/workspace/voice-lab/oww-data"
rir=sorted(os.path.join(LAB,"rir/mono16k",f) for f in os.listdir(LAB+"/rir/mono16k") if f.endswith((".wav",".flac")))[:1000]
bg=[]
for d in ("background/fma_sample","background/fsd50k_sample"):
    bg+=sorted(os.path.join(LAB,d,f) for f in os.listdir(os.path.join(LAB,d)) if f.endswith((".wav",".flac")))
bg=bg[:600]
probs={"SevenBandParametricEQ":0.25,"TanhDistortion":0.25,"PitchShift":0.25,"BandStopFilter":0.25,"AddColoredNoise":0.25,"AddBackgroundNoise":0.75,"Gain":1.0,"RIR":RIRP}
np.random.seed(0); import random, torch; random.seed(0); torch.manual_seed(0); torch.set_num_threads(4)
gen=D.augment_clips(clips,total_length=32000,batch_size=16,background_clip_paths=bg,RIR_paths=rir,augmentation_probabilities=probs)
t0=time.time()
compute_features_from_generator(gen,n_total=len(clips),clip_duration=32000,output_file=out,device="cpu",ncpu=4)
open(out+".txt","w").write("\n".join(clips))
print(f"{out}: {len(clips)} clips J={J} RIR={RIRP} in {time.time()-t0:.0f}s shape={np.load(out,mmap_mode='r').shape}",flush=True)
