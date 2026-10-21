"""Gate generated clips with the STT container: positives must transcribe to the wake phrase, negatives must not.
usage: gate_stt.py <gen_dir with manifest.json>  → writes gate.json + prints per-text survival."""
import sys, os, json, re, subprocess, collections, wave
URL=os.environ.get("STT_URL")
if not URL:
    sys.exit("STT_URL is required; set it to the STT transcription endpoint.")
D=sys.argv[1]; man=json.load(open(os.path.join(D,"manifest.json")))
POS=re.compile(r"nora|nova|喂 ?ra|諾拉|娜拉|nara|lora",re.I)
def stt(p):
    r=subprocess.run(["curl","-sS","-F",f"file=@{p}","-F","language=yue",URL],capture_output=True,text=True)
    if r.returncode:
        sys.exit(f"STT curl failed (exit {r.returncode}): {r.stderr.strip()}")
    try: return json.loads(r.stdout).get("text","")
    except Exception: return ""
surv=collections.Counter(); tot=collections.Counter(); out=[]
for m in man:
    if not m.get("file"): continue
    p=os.path.join(D,m["file"]); txt=stt(p); hit=bool(POS.search(txt)); dur=m["dur"]
    if m["kind"]=="pos": ok = hit and 0.5<=dur<=2.2
    elif m["kind"]=="mention": ok = hit            # 「唔該Nora」 must contain the name to be a useful hard negative
    else: ok = not hit
    if "諾拉" in m["text"]:  # nok6-laai1 is NOT the name as Miles says it; separate bucket, never positive
        m["kind"]="nokla"; ok=not hit   # kept only as a trap negative if the STT does not hear "nora"
    key=(m["kind"],m["text"]); tot[key]+=1; surv[key]+=ok
    out.append({**m,"stt":txt,"keep":ok})
json.dump(out,open(os.path.join(D,"gate.json"),"w"),ensure_ascii=False,indent=1)
for k in sorted(tot): print(f"{k[0]:8s} {k[1]:14s} keep {surv[k]:3d}/{tot[k]:3d}  ({100*surv[k]/tot[k]:.0f}%)")
pos=[o for o in out if o["kind"]=="pos"]; print(f"POS overall {sum(o['keep'] for o in pos)}/{len(pos)} | sample STT:",[o["stt"] for o in pos[:8]])
neg=[o for o in out if o["kind"]!="pos"]; print(f"NEG overall {sum(o['keep'] for o in neg)}/{len(neg)} | leaked:",[(o['text'],o['stt']) for o in neg if not o['keep']][:6])
