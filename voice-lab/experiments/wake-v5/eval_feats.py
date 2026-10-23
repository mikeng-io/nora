"""Train vanilla MLP on a re-augmented feature set; report overall + per-pattern test metrics, trap-negative FPR,
chunked validation FP/hr. usage: eval_feats.py <featdir> <posmode all|bare> <steps> <tag>"""
import numpy as np, torch, torch.nn as nn, time, sys, os, re, collections
from numpy.lib.stride_tricks import sliding_window_view
torch.manual_seed(0); rng=np.random.default_rng(0); torch.set_num_threads(6)
D,MODE,STEPS,TAG=sys.argv[1],sys.argv[2],int(sys.argv[3]),sys.argv[4]
FE="/home/norty/workspace/voice-lab-data/oww-data/features/"
pat=lambda p: re.sub(r'^\d+-|-\d+\.wav$','',os.path.basename(p))
def load(n):
    X=np.load(f"{D}/{n}.npy"); names=[l.strip() for l in open(f"{D}/{n}.npy.txt")]
    if len(X)!=len(names): raise ValueError((n,len(X),len(names)))
    return X,names
pos,posn=load("pos_train"); neg,negn=load("neg_train"); pt,ptn=load("pos_test"); nt,ntn=load("neg_test")
BARE={"喂-Nora","喂，Nora","喂Nora"}
if MODE=="bare":
    keep=[i for i,n in enumerate(posn) if pat(n) in BARE]; pos=pos[keep]; posn=[posn[i] for i in keep]
bgm=np.load(FE+"acav100m_background.npy",mmap_mode='r'); bg=np.asarray(bgm[np.sort(rng.choice(len(bgm),30000,replace=False))],dtype=np.float32)
zh=np.load(FE+"zhhk_speech_background.npy")
print(f"[{TAG}] train pos={len(pos)} ({MODE}) neg={len(neg)} bg={len(bg)} zh={len(zh)} | test pos={len(pt)} neg={len(nt)}",flush=True)
class Net(nn.Module):
    def __init__(s,d=32):
        super().__init__(); s.f=nn.Flatten(); s.l1=nn.Linear(1536,d); s.n1=nn.LayerNorm(d); s.l2=nn.Linear(d,d); s.n2=nn.LayerNorm(d); s.o=nn.Linear(d,1)
    def forward(s,x):
        x=torch.relu(s.n1(s.l1(s.f(x)))); x=torch.relu(s.n2(s.l2(x))); return torch.sigmoid(s.o(x))
m=Net(); opt=torch.optim.Adam(m.parameters(),lr=1e-3); bce=nn.BCELoss()
T=lambda a,n: torch.from_numpy(a[rng.integers(0,len(a),n)])
def auc(p,q):
    allv=np.concatenate([p,q]); r=allv.argsort().argsort()+1; return (r[:len(p)].sum()-len(p)*(len(p)+1)/2)/(len(p)*len(q))
def evaluate(tag):
    m.eval()
    with torch.no_grad(): p=m(torch.from_numpy(pt)).squeeze().numpy(); q=m(torch.from_numpy(nt)).squeeze().numpy()
    m.train(); print(f"  [{tag}] test AUC={auc(p,q):.4f} recall@0.5={(p>=0.5).mean():.3f} advFPR@0.5={(q>=0.5).mean():.4f}",flush=True); return p,q
t0=time.time()
for step in range(STEPS):
    x=torch.cat([T(pos,50),T(neg,50),T(bg,20),T(zh,20)]); y=torch.cat([torch.ones(50),torch.zeros(90)])[:,None]
    opt.zero_grad(); loss=bce(m(x),y); loss.backward(); opt.step()
    if step%3000==0: evaluate(f"step {step} loss {loss.item():.3f}")
p,q=evaluate(f"final {time.time()-t0:.0f}s")
per=collections.defaultdict(list)
for s,n in zip(p,ptn): per[pat(n)].append(s)
print("  per-pattern recall@0.5:", {k:f"{(np.array(v)>=0.5).mean():.2f}" for k,v in sorted(per.items())})
bare_p=np.array([s for s,n in zip(p,ptn) if pat(n) in BARE]); print(f"  BARE test recall@0.5={(bare_p>=0.5).mean():.3f} n={len(bare_p)} | AUC bare-vs-neg={auc(bare_p,q):.4f}")
trap=np.array([s for s,n in zip(q,ntn) if not re.fullmatch(r'[0-9a-f]{32}\.wav',os.path.basename(n))]); hexn=np.array([s for s,n in zip(q,ntn) if re.fullmatch(r'[0-9a-f]{32}\.wav',os.path.basename(n))])
print(f"  trap-neg(edge-tts phrases) FPR@0.5={(trap>=0.5).mean():.4f} n={len(trap)} | piper-hex neg FPR@0.5={(hexn>=0.5).mean():.4f} n={len(hexn)}")
F=np.load(FE+"validation_set_features.npy",mmap_mode='r'); N=F.shape[0]-15; fp5=fp9=0; mx=0; m.eval()
with torch.no_grad():
    for a in range(0,N,20000):
        b=min(a+20000,N); W=np.ascontiguousarray(sliding_window_view(np.asarray(F[a:b+15]),(16,96)).squeeze(1))
        sc=m(torch.from_numpy(W)).squeeze().numpy(); fp5+=(sc>=0.5).sum(); fp9+=(sc>=0.9).sum(); mx=max(mx,sc.max())
print(f"  val FP@0.5={fp5} -> {fp5/11.3:.1f}/hr (train.py-style)  FP@0.9={fp9} max={mx:.3f}",flush=True)
torch.save(m.state_dict(), f"{D}/model_{TAG}.pt")
torch.onnx.export(m.cpu(), torch.rand(1,16,96), f"{D}/model_{TAG}.onnx", opset_version=13)
print(f"  saved {D}/model_{TAG}.onnx")
