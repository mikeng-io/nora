#!/usr/bin/env bash
# After Phase 1: gate → lists → featurize → assemble V6 → train (3 seeds) → evaluate. Serial, capped.
set -u
S=/tmp/claude-1000/-home-norty-workspace-nora-wt-voice-design/1330d138-9f29-42c2-a55e-fcedfc5d26ea/scratchpad
VL=/home/norty/workspace/voice-lab; PY=$VL/.venv-train/bin/python; PYE=$VL/.venv/bin/python
run() { local cap=$1; shift; systemd-run --user --scope -p MemoryMax=${cap} -p MemorySwapMax=0 -q "$@"; }
cd $VL
echo "== 1. extract + merge Phase 1 shards"
rm -rf $S/v6/phase1 && mkdir -p $S/v6/phase1/gen
for t in $S/v6/phase1-base-cross_lingual-*.tar.gz; do tar xzf $t -C $S/v6/phase1 --strip-components=0; done
$PYE - <<'PY'
import json,glob,os,shutil
S="/tmp/claude-1000/-home-norty-workspace-nora-wt-voice-design/1330d138-9f29-42c2-a55e-fcedfc5d26ea/scratchpad/v6/phase1/"
# each shard extracted to S/gen (same dir) — manifests overwrite each other, so rebuild from the per-shard tars' manifests
man=[]
for t in sorted(glob.glob("/tmp/claude-1000/-home-norty-workspace-nora-wt-voice-design/1330d138-9f29-42c2-a55e-fcedfc5d26ea/scratchpad/v6/phase1-base-cross_lingual-*.tar.gz")):
    import tarfile
    with tarfile.open(t) as tf:
        m=json.load(tf.extractfile("gen/manifest.json")); man+=m
json.dump(man,open(S+"gen/manifest.json","w"),ensure_ascii=False)
print("merged manifest entries",len(man),"files on disk",len(glob.glob(S+"gen/*.wav")))
PY
echo "== 2. STT gate"
run 2G $PYE $S/v6/gate_stt.py $S/v6/phase1/gen 2>&1 | grep -E "^pos|^nokla|^trap|^mention|^command|^generic|overall"
echo "== 3. clip lists"
$PYE - <<'PY'
import json,os,collections
S="/tmp/claude-1000/-home-norty-workspace-nora-wt-voice-design/1330d138-9f29-42c2-a55e-fcedfc5d26ea/scratchpad/"
g=[e for e in json.load(open(S+"v6/phase1/gen/gate.json")) if e.get("file") and e["keep"]]
D=S+"v6/phase1/gen/"
pos=[(D+e["file"],e["sid"]) for e in g if e["kind"]=="pos"]; neg=[(D+e["file"],e["sid"]) for e in g if e["kind"]!="pos"]
spk=collections.Counter(e["sid"] for e in g if e["kind"]=="pos")
print(f"gated cloned positives {len(pos)} over {len(spk)} speakers; cloned negatives {len(neg)}")
# speaker-disjoint split by manifest sid (filenames carry the arm tag first): last 10% of speakers → test
spks=sorted(spk); test_spk=set(spks[int(len(spks)*0.9):])
def split(lst): return [p for p,s in lst if s not in test_spk],[p for p,s in lst if s in test_spk]
ptr,pte=split(pos); ntr,nte=split(neg)
open(S+"V6/clpos_train.txt","w").write("\n".join(ptr*3)); open(S+"V6/clpos_test.txt","w").write("\n".join(pte))
open(S+"V6/clneg_train.txt","w").write("\n".join(ntr)); open(S+"V6/clneg_test.txt","w").write("\n".join(nte))
print("train pos rows",len(ptr)*3,"test pos",len(pte),"train neg",len(ntr),"test neg",len(nte),"test speakers",len(test_spk))
PY
echo "== 4. featurize cloned clips (pos end-aligned, negs full+prefix)"
for n in clpos_test clpos_train; do run 6G $PY $S/reaug2.py $S/V6/$n.txt $S/V6/$n.npy 0.2 0.0 0.75 -10 2>&1 | grep "clips J=\|Error" | sed 's|.*scratchpad/||'; done
for n in clneg_test clneg_train; do run 6G $PY $S/reaug2.py $S/V6/$n.txt $S/V6/${n}_full.npy 0.2 0.0 0.75 -10 2>&1 | grep "clips J=\|Error" | sed 's|.*scratchpad/||'; run 6G $PY $S/reaug3.py $S/V6/$n.txt $S/V6/${n}_prefix.npy 0.0 2>&1 | grep "PREFIX\|Error" | sed 's|.*scratchpad/||'; done
echo "== 5. assemble V6"
run 4G $PY - <<'PY'
import numpy as np
S="/tmp/claude-1000/-home-norty-workspace-nora-wt-voice-design/1330d138-9f29-42c2-a55e-fcedfc5d26ea/scratchpad/"
def cat(parts,out):
    xs=[np.load(p) for p in parts]; x=np.concatenate(xs); np.save(out,x)
    open(out+".txt","w").write("\n".join(open(p+".txt").read().strip() for p in parts)); return x.shape
print("pos_train",cat([S+"V6/pos_minimax_train.npy",S+"V6/clpos_train.npy"],S+"V6/pos_train.npy"))
print("pos_test ",cat([S+"V6/pos_minimax_test.npy",S+"V6/clpos_test.npy"],S+"V6/pos_test.npy"))
print("neg_train",cat([S+"V5r/neg_train.npy",S+"V6/realneg_full_train.npy",S+"V6/realneg_prefix_train.npy",S+"V6/clneg_train_full.npy",S+"V6/clneg_train_prefix.npy"],S+"V6/neg_train.npy"))
print("neg_test ",cat([S+"V5r/neg_test.npy",S+"V6/realneg_full_test.npy",S+"V6/realneg_prefix_test.npy",S+"V6/clneg_test_full.npy",S+"V6/clneg_test_prefix.npy"],S+"V6/neg_test.npy"))
PY
echo "== 6. train V6 (3 seeds, pos 100/batch, weight 1500)"
for sd in 0 1 2; do run 5G --setenv=OWW_SEED=$sd --setenv=OWW_POS_N=100 $PY $S/train_local.py $S/V6 50000 1500 v6_s$sd 2>&1 | grep "Final Model\|Error\|Traceback" | sed "s/^/[v6 seed=$sd] /"; done
echo "== 7. evaluate"
for sd in 0 1 2; do run 3G $PYE $S/accept.py $S/V6/oww_v6_s$sd/喂Nora.onnx 300 2>&1 | grep "AUC\|recall@\|FP@\|runs\|debounced" | sed "s/^/[v6_s$sd] /"; run 2G $PYE $S/real_eval3.py $S/V6/oww_v6_s$sd/喂Nora.onnx 2>&1 | grep wakes | sed "s/^/[v6_s$sd] /"; done
echo "== 8. gated-cloned-positive recall (held-out speakers) per model"
run 3G $PY - <<'PY'
import numpy as np, onnxruntime as ort, glob
S="/tmp/claude-1000/-home-norty-workspace-nora-wt-voice-design/1330d138-9f29-42c2-a55e-fcedfc5d26ea/scratchpad/"
X=np.load(S+"V6/clpos_test.npy").astype(np.float32); M=np.load(S+"V6/pos_minimax_test.npy").astype(np.float32)
for m in sorted(glob.glob(S+"V6/oww_v6_s*/喂Nora.onnx"))+[S+"V5r/oww_v5j/喂Nora.onnx"]:
    s=ort.InferenceSession(m,providers=["CPUExecutionProvider"]); nm=s.get_inputs()[0].name
    f=lambda A: np.array([s.run(None,{nm:a[None]})[0].ravel()[0] for a in A])
    print(m.split("/")[-2], f"held-out cloned speakers recall@0.5={(f(X)>=0.5).mean():.3f} (n={len(X)}) | MiniMax test recall@0.5={(f(M)>=0.5).mean():.3f}")
PY
echo V6_PIPELINE_DONE
