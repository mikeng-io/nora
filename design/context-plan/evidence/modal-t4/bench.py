import json
import pathlib
import modal

HERE = pathlib.Path(__file__).resolve().parent
app = modal.App('nora-tev1-t4-probe')
image = modal.Image.from_registry('ollama/ollama:0.35.0', add_python='3.11').entrypoint([])

@app.function(image=image, gpu='T4', cpu=2, memory=16384, timeout=900)
def benchmark(payload):
    import math, os, statistics, subprocess, time, urllib.request
    env = os.environ.copy()
    env.update(OLLAMA_HOST='127.0.0.1:11434', OLLAMA_NUM_PARALLEL='1',
               OLLAMA_MAX_LOADED_MODELS='1', OLLAMA_VULKAN='0')
    log_path = '/tmp/ollama-t4.log'
    def call(path, body=None, timeout=180):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request('http://127.0.0.1:11434' + path, data=data,
                                     headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    gpu = subprocess.check_output(['nvidia-smi'], text=True)
    version = subprocess.check_output(['ollama','--version'], text=True)
    with open(log_path, 'w') as log:
        server = subprocess.Popen(['ollama','serve'], env=env, stdout=log, stderr=log)
        try:
            for _ in range(120):
                if server.poll() is not None:
                    raise RuntimeError(pathlib.Path(log_path).read_text()[-8000:])
                try:
                    call('/api/version', timeout=2)
                    break
                except Exception:
                    time.sleep(.5)
            start=time.perf_counter()
            subprocess.run(['ollama','pull','tev1:4b'], env=env, check=True, timeout=600)
            download_seconds=time.perf_counter()-start
            warm={'model':'tev1:4b','state':'Current message: Hello. Standing: no open task.',
                  'questions':{'greeting':{'type':'choice','instructions':'Does this message greet Nora?',
                  'criteria':{'A':'Yes','B':'No'}}},'keep_alive':'5m'}
            start=time.perf_counter(); warm_response=call('/v1/systemone',warm)
            cold_seconds=time.perf_counter()-start
            rows=[]
            for repeat in range(3):
                for name,msg,standing,cues,target,depth in payload['CASES']:
                    state={'clock':'2026-10-07 09:00 Asia/Hong_Kong','current_message':msg,
                           'tagger':'Person A (synthetic)',
                           'culture':'Friends use Cantonese and English, with informal direct requests.',
                           'standing':standing,'recent_transcript':'Oldest first; newest last. Recent tail concerns the same request.',
                           'candidate_cues':cues,'available_blocks':payload['DEFS']}
                    body={'model':'tev1:4b','state':state,'questions':payload['QUESTIONS'],'keep_alive':'5m'}
                    start=time.perf_counter()
                    try:
                        response=call('/v1/systemone',body); elapsed=time.perf_counter()-start
                        got={k:v.get('choice') for k,v in response['answers'].items()}
                        expected={k:('A' if k==target else 'B') for k in payload['DEFS']}
                        expected['transcript_window']='C' if depth==40 else 'A'
                        wrong=[k for k in expected if got.get(k)!=expected[k]]
                        row={'repeat':repeat,'case':name,'seconds':elapsed,'expected':expected,
                             'got':got,'wrong':wrong,'response':response}
                    except Exception as error:
                        row={'repeat':repeat,'case':name,'seconds':time.perf_counter()-start,'error':str(error)}
                    rows.append(row)
                    print(name,repeat,round(row['seconds'],3),row.get('wrong',row.get('error')),flush=True)
            resident=call('/api/ps')
            valid=[r for r in rows if 'error' not in r]; times=sorted(r['seconds'] for r in valid)
            result={'gpu':gpu,'ollama_version':version,'download_seconds':download_seconds,
                    'cold_first_question_seconds':cold_seconds,'warm_response':warm_response,
                    'warm_p50_seconds':statistics.median(times) if times else None,
                    'warm_p95_seconds':times[math.ceil(.95*len(times))-1] if times else None,
                    'errors':len(rows)-len(valid),'rows':rows,'resident':resident,
                    'model_details':call('/api/show',{'model':'tev1:4b'})}
            call('/api/generate',{'model':'tev1:4b','keep_alive':0})
        finally:
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
    result['backend_log']=pathlib.Path(log_path).read_text()
    return result

@app.local_entrypoint()
def main():
    result=benchmark.remote(json.loads((HERE/'payload.json').read_text()))
    (HERE/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:result[k] for k in ['warm_p50_seconds','warm_p95_seconds','errors','cold_first_question_seconds','download_seconds']},indent=2))
