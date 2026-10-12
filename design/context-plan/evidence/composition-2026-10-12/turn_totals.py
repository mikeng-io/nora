"""Whole-turn input across every generation of each engagement trace (run in /tmp/ctxm on norty)."""
import json,glob,statistics as st
tot=[];calls=[];fresh=[]
for f in glob.glob('t_*.json'):
    t=json.load(open(f)); g=[o for o in t.get('observations',[]) if o['type']=='GENERATION']
    if not g: continue
    ins=[(o.get('usageDetails') or {}).get('input') or 0 for o in g]
    cr=[(o.get('usageDetails') or {}).get('cache_read_input_tokens') or 0 for o in g]
    tot.append(sum(ins)); calls.append(len(g)); fresh.append(sum(ins)-sum(cr))
q=lambda v:(st.median(v),sorted(v)[9*len(v)//10])
print("per turn: generations median/p90",q(calls)," input tokens all calls",q(tot)," uncached",q(fresh))
