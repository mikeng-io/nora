import json, os, time, urllib.request
key=os.environ["MINIMAX_CN_API_KEY"]
TEXT="好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。"
def call(host, model, boost):
    payload={"model":model,"text":TEXT,"stream":True,
      "voice_setting":{"voice_id":"female-shaonv","speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":boost,"subtitle_enable":False}
    req=urllib.request.Request(f"https://{host}/v1/t2a_v2",
      data=json.dumps(payload).encode(),method="POST",
      headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    t0=time.perf_counter(); ttfa=None; ch=0; info={}
    with urllib.request.urlopen(req,timeout=120) as r:
        for line in r:
            line=line.decode("utf-8","ignore").strip()
            if not line.startswith("data:"): continue
            b=line[5:].strip()
            if b in ("","[DONE]"): continue
            try: d=json.loads(b)
            except Exception: continue
            if (d.get("data") or {}).get("audio"):
                if ttfa is None: ttfa=time.perf_counter()-t0
                ch+=1
            if d.get("extra_info"): info=d["extra_info"]
    return ttfa, time.perf_counter()-t0, ch, info
for host in ["api-uw.minimax.io"]:
    for model in ("speech-2.8-turbo","speech-2.8-hd"):
        for boost in ("Chinese,Yue",):
            try:
                t,d,ch,info=call(host,model,boost)
                print(f"{host:22s} {model:18s} {boost:12s} TTFA={t*1000:6.0f}ms total={d*1000:6.0f}ms chunks={ch} audio={info.get('audio_length',0)/1000:.2f}s")
            except Exception as e:
                print(f"{host} {model} {boost} ERR {type(e).__name__}: {e}")
