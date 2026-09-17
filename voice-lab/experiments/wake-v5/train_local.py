"""Run the REAL openWakeWord trainer (Model.auto_train from oww-src, unmodified) locally on precomputed features,
with the FP-validation set fed in memory-safe chunks (train.py's own loader materializes ~3GB + copies).
usage: train_local.py <featdir> <steps> <max_negative_weight> <tag> [layer_size]"""
import sys, os, time, logging, numpy as np, torch
sys.path.insert(0,"/home/norty/workspace/voice-lab-data/oww-src")
from openwakeword.train import Model
from openwakeword.data import mmap_batch_generator
logging.basicConfig(level=logging.INFO, format="%(message)s")
D,STEPS,MNW,TAG=sys.argv[1],int(sys.argv[2]),int(sys.argv[3]),sys.argv[4]; LAYER=int(sys.argv[5]) if len(sys.argv)>5 else 32
FE="/home/norty/workspace/voice-lab-data/oww-data/features/"
SEED=int(os.environ.get("OWW_SEED","0")); torch.manual_seed(SEED); np.random.seed(SEED); torch.set_num_threads(6)
input_shape=(16,96)
oww=Model(n_classes=1,input_shape=input_shape,model_type="dnn",layer_dim=LAYER,seconds_per_example=1280*16/16000)
feature_files={"background":os.environ.get("OWW_BG",FE+"acav100m_background.npy"),"zhhk_speech":FE+"zhhk_speech_background.npy",
               "positive":f"{D}/pos_train.npy","adversarial_negative":f"{D}/neg_train.npy"}
def f(x,n=16):  # same shape guard as train.py
    if n!=x.shape[1]:
        x=np.vstack(x); return np.array([x[i:i+n,:] for i in range(0,x.shape[0]-n,n)])
    return x
data_transforms={k:f for k in feature_files}
label_transforms={k:(lambda x:[1 for i in x]) if k=="positive" else (lambda x:[0 for i in x]) for k in feature_files}
gen=mmap_batch_generator(feature_files,n_per_class={"adversarial_negative":50,"positive":int(os.environ.get("OWW_POS_N","50")),"background":20,"zhhk_speech":20},
                         data_transform_funcs=data_transforms,label_transform_funcs=label_transforms)
class IterDataset(torch.utils.data.IterableDataset):
    def __init__(s,g): s.g=g
    def __iter__(s): return s.g
X_train=torch.utils.data.DataLoader(IterDataset(gen),batch_size=None,num_workers=4,prefetch_factor=16)
class ChunkedFP:
    """Iterable of (x,y) chunks over the 11.3h validation features; zero-copy strided view, 20k windows per chunk."""
    def __init__(s,path,chunk=20000):
        s.F=np.load(path,mmap_mode="r"); s.N=s.F.shape[0]-15; s.chunk=chunk
    def __iter__(s):
        for a in range(0,s.N,s.chunk):
            b=min(a+s.chunk,s.N); blk=np.asarray(s.F[a:b+15])
            x=torch.from_numpy(np.ascontiguousarray(np.lib.stride_tricks.sliding_window_view(blk,(16,96)).squeeze(1)))
            yield x, torch.zeros(len(x))
X_val_fp=ChunkedFP(FE+"validation_set_features.npy")
pt=np.load(f"{D}/pos_test.npy"); nt=np.load(f"{D}/neg_test.npy")
X_val=torch.utils.data.DataLoader(torch.utils.data.TensorDataset(torch.from_numpy(np.vstack((pt,nt))),
      torch.from_numpy(np.hstack((np.ones(len(pt)),np.zeros(len(nt)))).astype(np.float32))),batch_size=len(pt)+len(nt))
t0=time.time()
best=oww.auto_train(X_train=X_train,X_val=X_val,false_positive_val_data=X_val_fp,steps=STEPS,max_negative_weight=MNW,target_fp_per_hour=0.2)
print(f"auto_train done in {time.time()-t0:.0f}s; checkpoints kept={len(oww.best_models)}",flush=True)
out=f"{D}/oww_{TAG}"; os.makedirs(out,exist_ok=True)
oww.export_model(model=best,model_name="喂Nora",output_dir=out)
import json; json.dump({k:[float(v) for v in vals] for k,vals in oww.history.items() if k.startswith("val")},open(f"{out}/history.json","w"))
print("saved",out+"/喂Nora.onnx",flush=True)
