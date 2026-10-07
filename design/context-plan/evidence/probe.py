import json,urllib.request,time,statistics,subprocess,os,pathlib
ROOT=pathlib.Path('/tmp/nora-context-probe'); ROOT.mkdir(exist_ok=True)
CASES=[
('doc_en','Open the draft we created five minutes ago.','Nora created a draft titled Dinner invitation. No computer task or delegated job is active.','Documents: Dinner invitation draft.','documents',8),
('doc_zh','幫我開返頭先嗰份邀請文件。','五分鐘前 Nora 喺呢個房間建立咗聚餐邀請文件。冇電腦工作或委派工作。','Documents: 聚餐邀請草稿。','documents',8),
('recall_en','What food do I usually avoid?','No open task. The request concerns the tagger\'s established preferences.','Peer history: dated conclusions about the tagger\'s food preferences.','peer_history',8),
('recall_zh','你記唔記得我平時唔食啲咩？','冇未完成工作。問題係問標記 Nora 嗰個人一貫嘅飲食喜好。','Peer history: 該人過往飲食喜好。','peer_history',8),
('computer','繼續頭先幫我跑嗰個 script，睇下有冇報錯。','A Python script is still running on Nora\'s computer. No document editing or delegated job is active.','Computer: running script and terminal state.','computer',8),
('subagent','How is the research you delegated coming along?','Nora delegated research to a subagent. The job is running. No computer task or document is active.','Subagents: running research job and progress.','subagents',8),
('hello','早晨 Nora！','No open tasks or unresolved question.','All optional blocks are available but have no bearing on this greeting.','none',8),
('old_thread','Compare the three venues we discussed earlier, including the one rejected first.','Standing says the comparison remains open. Transcript metadata: three venue proposals at rows 2, 12 and 29; row 3 rejected the first. The newest 8 rows do not name the venues.','No optional block holds venue facts; use the transcript.','none',40),
]
DEFS={'peer_history':'Dated recall about the tagger, including their established preferences.','documents':'This room\'s document index, including document handles for opening or editing.','computer':'Current computer state and ongoing terminal/browser tasks in this room.','subagents':'This room\'s delegated jobs and progress.'}
QUESTIONS={k:{'type':'choice','instructions':'What context does Nora need for this turn? Decide only whether this context block is needed; do not answer the user.','criteria':{'A':'Include '+v,'B':'Omit this block; its information is not needed for the request.'}} for k,v in DEFS.items()}
QUESTIONS['transcript_window']={'type':'choice','instructions':'Which contiguous transcript window is needed to understand the current request? Select the smallest sufficient option, using the supplied row metadata.','criteria':{'A':'Newest 8 messages.','B':'Newest 20 messages.','C':'All 40 available messages.'}}
def call(base,path,body=None):
 data=None if body is None else json.dumps(body,ensure_ascii=False).encode()
 return json.load(urllib.request.urlopen(urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'}),timeout=60))
def percentile(xs,p):return sorted(xs)[min(len(xs)-1,int((len(xs)-1)*p))]
results=[]
def run(arm,base,model):
 print('START',arm,flush=True)
 warm={'model':model,'state':'Current message: Hello. Standing: no open task.','questions':{'greeting':{'type':'choice','instructions':'Does this message greet Nora?','criteria':{'A':'Yes','B':'No'}}},'keep_alive':'2m'}
 t=time.perf_counter();w=call(base,'/v1/systemone',warm);load=time.perf_counter()-t
 rows=[]
 for name,msg,standing,cues,target,depth in CASES:
  state={'clock':'2026-10-07 09:00 Asia/Hong_Kong','current_message':msg,'tagger':'Person A (synthetic)','culture':'Friends use Cantonese and English, with informal direct requests.','standing':standing,'recent_transcript':'Oldest first; newest last. Recent tail concerns the same request.','candidate_cues':cues,'available_blocks':DEFS}
  body={'model':model,'state':state,'questions':QUESTIONS,'keep_alive':'2m'}
  t=time.perf_counter()
  try:
   response=call(base,'/v1/systemone',body);elapsed=time.perf_counter()-t
   answers=response['answers']; expected={k:('A' if k==target else 'B') for k in DEFS};expected['transcript_window']='C' if depth==40 else 'A'
   got={k:v.get('choice') for k,v in answers.items()};wrong=[k for k in expected if got.get(k)!=expected[k]]
   row={'case':name,'seconds':elapsed,'expected':expected,'got':got,'wrong':wrong,'response':response}
  except Exception as e:row={'case':name,'seconds':time.perf_counter()-t,'error':str(e)}
  rows.append(row);print(arm,name,round(row['seconds'],3),row.get('wrong',row.get('error')),flush=True)
 ps=call(base,'/api/ps'); valid=[r for r in rows if 'error' not in r];times=[r['seconds'] for r in valid]
 summary={'arm':arm,'model':model,'first_call_seconds':load,'warm_p50_seconds':statistics.median(times) if times else None,'warm_p95_nearest_rank_seconds':sorted(times)[max(0,__import__('math').ceil(.95*len(times))-1)] if times else None,'cases_exact':sum(not r['wrong'] for r in valid),'cases_total':len(rows),'decisions_correct':sum(5-len(r['wrong']) for r in valid),'decisions_total':len(rows)*5,'errors':len(rows)-len(valid),'resident':ps,'rows':rows}
 results.append(summary);(ROOT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print('SUMMARY',json.dumps({k:v for k,v in summary.items() if k not in ('resident','rows')}),flush=True)
 call(base,'/api/generate',{'model':model,'keep_alive':0})
run('rocm-0.8b','http://127.0.0.1:11434','tev1:0.8b')
run('rocm-4b','http://127.0.0.1:11434','tev1:4b')
env=os.environ.copy();env.update({'OLLAMA_HOST':'127.0.0.1:11439','OLLAMA_MODELS':'/usr/share/ollama/.ollama/models','OLLAMA_VULKAN':'0','ROCR_VISIBLE_DEVICES':'-1','HIP_VISIBLE_DEVICES':'-1','CUDA_VISIBLE_DEVICES':'-1','OLLAMA_NUM_PARALLEL':'1','OLLAMA_MAX_LOADED_MODELS':'1'})
with open(ROOT/'cpu-server.log','w') as log:
 proc=subprocess.Popen(['/usr/local/bin/ollama','serve'],env=env,stdout=log,stderr=log)
 try:
  for _ in range(50):
   if proc.poll() is not None:raise RuntimeError('CPU server exited; inspect cpu-server.log')
   try:call('http://127.0.0.1:11439','/api/version');break
   except Exception:time.sleep(.1)
  run('cpu-0.8b','http://127.0.0.1:11439','tev1:0.8b')
 finally:
  proc.terminate()
  try:proc.wait(timeout=10)
  except subprocess.TimeoutExpired:proc.kill();proc.wait()
print('DONE',flush=True)
