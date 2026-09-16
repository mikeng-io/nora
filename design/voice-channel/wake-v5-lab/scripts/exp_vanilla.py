"""Vanilla MLP (same arch as oww train.py 'dnn') on V4 augmented features, plain BCE, no schedule/hard-mining.
Memory-frugal: bg subsample, chunked validation scoring, never materializes the full window matrix."""
import numpy as np, torch, torch.nn as nn, time, sys
from numpy.lib.stride_tricks import sliding_window_view
torch.manual_seed(0); rng=np.random.default_rng(0); torch.set_num_threads(8)
S="/tmp/claude-1000/-home-norty-workspace-nora-wt-voice-design/1330d138-9f29-42c2-a55e-fcedfc5d26ea/scratchpad/v4/"
FE="/home/norty/workspace/voice-lab/oww-data/features/"
STEPS=int(sys.argv[1]) if len(sys.argv)>1 else 8000
pos=np.load(S+"positive_features_train.npy"); neg=np.load(S+"negative_features_train.npy")
bgm=np.load(FE+"acav100m_background.npy",mmap_mode='r'); bg=np.asarray(bgm[np.sort(rng.choice(len(bgm),30000,replace=False))],dtype=np.float32)
zh=np.load(FE+"zhhk_speech_background.npy")
pt=torch.from_numpy(np.load(S+"positive_features_test.npy")); nt=torch.from_numpy(np.load(S+"negative_features_test.npy"))
print("rows pos",len(pos),"neg",len(neg),"bg",len(bg),"zh",len(zh), flush=True)
class Net(nn.Module):
    def __init__(s,d=32):
        super().__init__(); s.f=nn.Flatten(); s.l1=nn.Linear(1536,d); s.n1=nn.LayerNorm(d); s.l2=nn.Linear(d,d); s.n2=nn.LayerNorm(d); s.o=nn.Linear(d,1)
    def forward(s,x):
        x=torch.relu(s.n1(s.l1(s.f(x)))); x=torch.relu(s.n2(s.l2(x))); return torch.sigmoid(s.o(x))
m=Net(); opt=torch.optim.Adam(m.parameters(),lr=1e-3); bce=nn.BCELoss()
T=lambda a,n: torch.from_numpy(a[rng.integers(0,len(a),n)])
def evaluate(tag):
    m.eval()
    with torch.no_grad():
        p=m(pt).squeeze().numpy(); q=m(nt).squeeze().numpy()
        allv=np.concatenate([p,q]); r=allv.argsort().argsort()+1; auc=(r[:len(p)].sum()-len(p)*(len(p)+1)/2)/(len(p)*len(q))
        print(f"[{tag}] test AUC={auc:.4f} recall@0.5={(p>=0.5).mean():.3f} advFPR@0.5={(q>=0.5).mean():.4f} | @0.9 recall={(p>=0.9).mean():.3f} FPR={(q>=0.9).mean():.4f}", flush=True)
    m.train(); return p,q
t0=time.time()
for step in range(STEPS):
    x=torch.cat([T(pos,50),T(neg,50),T(bg,20),T(zh,20)]); y=torch.cat([torch.ones(50),torch.zeros(90)])[:,None]
    opt.zero_grad(); loss=bce(m(x),y); loss.backward(); opt.step()
    if step%2000==0 or step==STEPS-1: print(f"step {step} loss {loss.item():.4f} {time.time()-t0:.0f}s",flush=True); evaluate(step)
p,q=evaluate("final")
# chunked validation FP: window starts in chunks of 20k -> 20k*1536*4 = 123MB
F=np.load(FE+"validation_set_features.npy",mmap_mode='r'); N=F.shape[0]-15; fp5=fp9=0; mx=0
m.eval()
with torch.no_grad():
    for a in range(0,N,20000):
        b=min(a+20000,N); W=np.ascontiguousarray(sliding_window_view(np.asarray(F[a:b+15]),(16,96)).squeeze(1))
        sc=m(torch.from_numpy(W)).squeeze().numpy(); fp5+=(sc>=0.5).sum(); fp9+=(sc>=0.9).sum(); mx=max(mx,sc.max())
hrs=N*0.08/3600
print(f"val windows={N} FP@0.5={fp5} ({fp5/11.3:.1f}/hr train.py-style, {fp5/hrs:.1f}/hr true) FP@0.9={fp9} max={mx:.3f}", flush=True)
torch.save(m.state_dict(), S+"exp_vanilla.pt")
