"""Voice-shortcut test: train on J02 bare positives with MiniMax (hex-named) negatives REMOVED, then compare FPR on
held-out MiniMax traps (same voices as positives) vs edge-tts traps (different voices)."""
import numpy as np, torch, torch.nn as nn, os, re
torch.manual_seed(0); rng=np.random.default_rng(0); torch.set_num_threads(6)
S="/home/norty/workspace/voice-lab-data/scratch/J02/"
FE="/home/norty/workspace/voice-lab-data/oww-data/features/"
pat=lambda p: re.sub(r'^\d+-|-\d+\.wav$','',os.path.basename(p)); ishex=lambda p: bool(re.fullmatch(r'[0-9a-f]{32}\.wav',os.path.basename(p)))
def load(n): return np.load(S+n+".npy"), [l.strip() for l in open(S+n+".npy.txt")]
pos,posn=load("pos_train"); neg,negn=load("neg_train"); pt,ptn=load("pos_test"); nt,ntn=load("neg_test")
BARE={"喂-Nora","喂，Nora","喂Nora"}
pos=pos[[i for i,n in enumerate(posn) if pat(n) in BARE]]
mm_tr=neg[[i for i,n in enumerate(negn) if ishex(n)]]; neg=neg[[i for i,n in enumerate(negn) if not ishex(n)]]
mm_te=nt[[i for i,n in enumerate(ntn) if ishex(n)]]; ed_te=nt[[i for i,n in enumerate(ntn) if not ishex(n)]]
bare_te=pt[[i for i,n in enumerate(ptn) if pat(n) in BARE]]
bgm=np.load(FE+"acav100m_background.npy",mmap_mode='r'); bg=np.asarray(bgm[np.sort(rng.choice(len(bgm),30000,replace=False))],dtype=np.float32)
zh=np.load(FE+"zhhk_speech_background.npy")
print(f"train pos(bare)={len(pos)} neg(edge only)={len(neg)} | heldout MiniMax traps train={len(mm_tr)} test={len(mm_te)} | edge traps test={len(ed_te)}")
class Net(nn.Module):
    def __init__(s,d=32):
        super().__init__(); s.f=nn.Flatten(); s.l1=nn.Linear(1536,d); s.n1=nn.LayerNorm(d); s.l2=nn.Linear(d,d); s.n2=nn.LayerNorm(d); s.o=nn.Linear(d,1)
    def forward(s,x):
        x=torch.relu(s.n1(s.l1(s.f(x)))); x=torch.relu(s.n2(s.l2(x))); return torch.sigmoid(s.o(x))
for mode in ("without_minimax_negs","with_minimax_negs"):
    torch.manual_seed(0); m=Net(); opt=torch.optim.Adam(m.parameters(),lr=1e-3); bce=nn.BCELoss()
    NEG=neg if mode=="without_minimax_negs" else np.vstack([neg,mm_tr])
    T=lambda a,n: torch.from_numpy(a[rng.integers(0,len(a),n)])
    for step in range(10000):
        x=torch.cat([T(pos,50),T(NEG,50),T(bg,20),T(zh,20)]); y=torch.cat([torch.ones(50),torch.zeros(90)])[:,None]
        opt.zero_grad(); loss=bce(m(x),y); loss.backward(); opt.step()
    m.eval()
    with torch.no_grad():
        f=lambda a: m(torch.from_numpy(a)).squeeze().numpy()
        print(f"[{mode}] bare-pos recall={(f(bare_te)>=0.5).mean():.3f} | FPR MiniMax traps(test)={(f(mm_te)>=0.5).mean():.3f} MiniMax traps(train,heldout)={(f(mm_tr)>=0.5).mean():.3f} | FPR edge traps={(f(ed_te)>=0.5).mean():.3f}")
