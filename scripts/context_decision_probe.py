import json,time,pathlib,sys,statistics,os,tempfile
ns={};exec(open('/tmp/nora-context-probe.py').read().split("run('rocm-0.8b'")[0],ns)
CASES=ns['CASES']; QUESTIONS=ns['QUESTIONS'];DEFS=ns['DEFS']
# A fresh 0700 directory: a fixed /tmp path would let another local account plant a link.
OUT=pathlib.Path(tempfile.mkdtemp(prefix='nora-local-models-'));print('RESULTS',OUT,flush=True)
import torch
torch.set_num_threads(4);torch.set_num_interop_threads(2)
kind=sys.argv[1];t=time.perf_counter()
if kind=='laya':
 import laya
 model=laya.load('convaiinnovations/laya',device='cpu')
 print('LOAD',time.perf_counter()-t,flush=True)
 def predict(state):return model.system_one(state,QUESTIONS)
elif kind=='strands':
 from strands_decider.infer import load_engine
 from strands_decider.schema import SystemOneRequest
 model=load_engine('StrandsAgents/strands-decider-2B-hobson-v19',device='cpu')
 print('LOAD',time.perf_counter()-t,flush=True)
 def predict(state):return model.evaluate(SystemOneRequest(state=state,questions=QUESTIONS)).model_dump()
else:raise ValueError(kind)
load_seconds=time.perf_counter()-t
rows=[]
for name,msg,standing,cues,target,depth in CASES:
 state={'clock':'2026-10-07 09:00 Asia/Hong_Kong','current_message':msg,'tagger':'Person A (synthetic)','culture':'Friends use Cantonese and English, with informal direct requests.','standing':standing,'recent_transcript':'Oldest first; newest last. Recent tail concerns the same request.','candidate_cues':cues,'available_blocks':DEFS}
 t=time.perf_counter()
 try:
  response=predict(state); elapsed=time.perf_counter()-t
  answers=response['answers'];expected={k:('A' if k==target else 'B') for k in DEFS};expected['transcript_window']='C' if depth==40 else 'A'
  got={k:v.get('choice') for k,v in answers.items()};wrong=[k for k in expected if got.get(k)!=expected[k]]
  row={'case':name,'seconds':elapsed,'expected':expected,'got':got,'wrong':wrong,'response':response}
 except Exception as e:row={'case':name,'seconds':time.perf_counter()-t,'error':type(e).__name__+': '+str(e)}
 rows.append(row);print(kind,name,round(row['seconds'],3),row.get('wrong',row.get('error')),flush=True)
 (OUT/(kind+'-results.json')).write_text(json.dumps({'model':kind,'load_seconds':load_seconds,'rows':rows},ensure_ascii=False,indent=2))
 if time.perf_counter()-t>60:break
print('DONE',flush=True)
