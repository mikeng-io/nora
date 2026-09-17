import json, os, time, urllib.request, statistics
key = os.environ["MINIMAX_CN_API_KEY"]
# a realistic Nora reply in Cantonese, ~120 chars (typical short spoken answer)
TEXT = "好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。仲有三分鐘就完，我等緊你決定。"

def call(model, boost, stream=True):
    payload = {"model": model, "text": TEXT, "stream": stream,
      "voice_setting": {"voice_id":"female-shaonv","speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting": {"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost": boost, "subtitle_enable": False}
    req = urllib.request.Request("https://api.minimaxi.com/v1/t2a_v2",
      data=json.dumps(payload).encode(), method="POST",
      headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    t0=time.perf_counter(); ttfa=None; chunks=0; total=0
    with urllib.request.urlopen(req, timeout=120) as r:
        for line in r:
            line=line.decode("utf-8","ignore").strip()
            if not line.startswith("data:"): continue
            body=line[5:].strip()
            if body in ("","[DONE]"): continue
            try: d=json.loads(body)
            except Exception: continue
            a=(d.get("data") or {}).get("audio")
            if a:
                if ttfa is None: ttfa=time.perf_counter()-t0
                chunks+=1; total+=len(a)//2
            if d.get("extra_info"): info=d["extra_info"]
    dt=time.perf_counter()-t0
    return ttfa, dt, chunks, total, info

print(f"TEXT len={len(TEXT)} chars\n")
print(f"{'model':18s} {'boost':12s} {'TTFA(ms)':>9s} {'total(ms)':>10s} {'chunks':>7s} {'audio_s':>8s} {'chars':>6s}")
for model in ("speech-2.8-turbo","speech-2.8-hd"):
    for boost in ("Chinese,Yue","auto"):
        try:
            ttfa,dt,ch,tot,info = call(model,boost)
            print(f"{model:18s} {boost:12s} {ttfa*1000:9.0f} {dt*1000:10.0f} {ch:7d} {info.get('audio_length',0)/1000:8.2f} {info.get('usage_characters','?'):>6}")
        except Exception as e:
            print(f"{model} {boost} ERR {type(e).__name__}: {e}")
