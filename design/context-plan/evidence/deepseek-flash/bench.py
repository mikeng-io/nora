import json,pathlib,time,urllib.request,urllib.error,tomllib,statistics,math,uuid
HERE=pathlib.Path(__file__).resolve().parent
PAYLOAD=json.loads((HERE/'payload.json').read_text())
CONFIG=tomllib.loads(pathlib.Path('/home/norty/workspace/nora/config.toml').read_text())
env={}
for line in pathlib.Path('/home/norty/workspace/nora/.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);env[k.strip()]=v.strip().strip('\"\x27')
KEY=env[CONFIG['llm']['api_key_env']]
MODEL='ollamacloud/deepseek-v4.1-flash'
SYSTEM='''You select context for Nora. Answer only: what context does Nora need for this turn?
Select needed optional blocks and the smallest sufficient contiguous transcript window, using the supplied state and criteria. Do not answer the user's message, suggest a reply, decide whether Nora should engage, or call tools.
Return exactly one JSON object with exactly two keys:
{"blocks": [selected names], "transcript_window": 8}
blocks is a list containing zero or more unique names from peer_history, documents, computer, subagents. These are optional blocks only; mandatory Nora context is retained separately. transcript_window must be 8, 20, or 40. Return no explanation, markdown, or other keys.'''

def request(state):
 body={'model':MODEL,'messages':[{'role':'system','content':SYSTEM},
 {'role':'user','content':json.dumps({'state':state,'questions':PAYLOAD['QUESTIONS']},ensure_ascii=False)}],
 'reasoning_effort':'none','temperature':0,'max_tokens':256,'stream':False}
 req=urllib.request.Request('http://127.0.0.1:20128/v1/chat/completions',data=json.dumps(body,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+KEY,'Content-Type':'application/json'})
 start=time.perf_counter()
 try:
  with urllib.request.urlopen(req,timeout=30) as response:
   data=json.load(response);headers={k:v for k,v in response.headers.items() if k.lower() in ['x-request-id','x-provider','x-model','x-upstream-model','x-cache','x-cache-hit','x-omniroute-cache','x-omniroute-provider']}
  return {'seconds':time.perf_counter()-start,'response':data,'headers':headers}
 except urllib.error.HTTPError as error:
  return {'seconds':time.perf_counter()-start,'error':'HTTP '+str(error.code),'detail':error.read().decode()[:1000]}
 except Exception as error:
  return {'seconds':time.perf_counter()-start,'error':type(error).__name__+': '+str(error)}

def classify(row):
 if 'error' in row:return row
 try:
  msg=row['response']['choices'][0]['message'];content=msg.get('content')
  plan=json.loads(content)
  if not isinstance(plan,dict) or set(plan)!={'blocks','transcript_window'}:raise ValueError('wrong object keys')
  blocks=plan['blocks'];depth=plan['transcript_window']
  if not isinstance(blocks,list) or not all(isinstance(x,str) for x in blocks) or len(set(blocks))!=len(blocks) or not set(blocks)<=set(PAYLOAD['DEFS']):raise ValueError('invalid block list')
  if type(depth) is not int or depth not in [8,20,40]:raise ValueError('invalid transcript depth')
  row['plan']=plan
  row['got']={k:('A' if k in blocks else 'B') for k in PAYLOAD['DEFS']}
  row['got']['transcript_window']={8:'A',20:'B',40:'C'}[depth]
  row['reasoning_present']=bool(msg.get('reasoning_content') or msg.get('reasoning'))
 except Exception as error:row['error']='invalid_output: '+str(error)
 return row

rows=[]
for repeat in range(3):
 for name,msg,standing,cues,target,depth in PAYLOAD['CASES']:
  state={'clock':'2026-10-07 09:00 Asia/Hong_Kong','current_message':msg,'tagger':'Person A (synthetic)',
   'culture':'Friends use Cantonese and English, with informal direct requests.','standing':standing,
   'recent_transcript':'Oldest first; newest last. Recent tail concerns the same request.',
   'candidate_cues':cues,'available_blocks':PAYLOAD['DEFS'],
   'probe_sample_id':str(uuid.uuid4()),'probe_sample_id_meaning':'Transport uniqueness only; ignore when selecting context.'}
  expected={k:('A' if k==target else 'B') for k in PAYLOAD['DEFS']};expected['transcript_window']='C' if depth==40 else 'A'
  row=classify(request(state));row.update(case=name,repeat=repeat,expected=expected)
  if 'error' not in row:row['wrong']=[k for k in expected if row['got'].get(k)!=expected[k]]
  rows.append(row)
  (HERE/'result.json').write_text(json.dumps({'model':MODEL,'system':SYSTEM,'rows':rows},ensure_ascii=False,indent=2))
  print(repeat,name,round(row['seconds'],3),row.get('wrong',row.get('error')),flush=True)
  if 'error' in row and row['error'].startswith('HTTP'):
   print('Stopping after upstream HTTP error; no retry cascade.',flush=True);break
 if rows and rows[-1].get('error','').startswith('HTTP'):break
valid=[r for r in rows if 'error' not in r];times=sorted(r['seconds'] for r in rows)
summary={'model':MODEL,'cases':len(rows),'errors':len(rows)-len(valid),
 'warm_p50_seconds':statistics.median(times) if times else None,
 'warm_p95_seconds':times[math.ceil(.95*len(times))-1] if times else None,
 'optional_correct':sum(sum(r['got'].get(k)==r['expected'][k] for k in PAYLOAD['DEFS']) for r in valid),
 'optional_total':4*len(rows),'depth_correct':sum(r['got']['transcript_window']==r['expected']['transcript_window'] for r in valid),
 'depth_total':len(rows),'required_block_misses':[(r['repeat'],r['case'],k) for r in valid for k in PAYLOAD['DEFS'] if r['expected'][k]=='A' and r['got'].get(k)!='A'],
 'response_models':list(set(r['response'].get('model','') for r in rows if 'response' in r)),
 'reasoning_present':sum(r.get('reasoning_present',False) for r in rows)}
(HERE/'result.json').write_text(json.dumps({'model':MODEL,'system':SYSTEM,'summary':summary,'rows':rows},ensure_ascii=False,indent=2))
print(json.dumps(summary,indent=2),flush=True)
