import json, os, time, urllib.request

key = os.environ["MINIMAX_CN_API_KEY"]
TEXT = "好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。"

# NO pinned IP — plain hostname, normal system DNS. This is the real path.
payload = {"model":"speech-2.8-turbo","text":TEXT,"stream":False,
  "voice_setting":{"voice_id":"Cantonese_CuteGirl","speed":1.0,"vol":1.0,"pitch":0},
  "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
  "language_boost":"Chinese,Yue"}

for host in ["api.minimaxi.com", "api.minimax.chat"]:
    req = urllib.request.Request(f"https://{host}/v1/t2a_v2",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    t0=time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.load(r)
        el=time.time()-t0
        audio = bytes.fromhex(d["data"]["audio"])
        fn = f"samples/cnsub/{host}.mp3"
        open(fn,"wb").write(audio)
        ei=d.get("extra_info",{})
        print(f"  {host:22s} status={d['base_resp']['status_code']} {len(audio):7d}B "
              f"audio={ei.get('audio_length',0)/1000:.2f}s billed={ei.get('usage_characters')} {el:.2f}s -> {fn}")
    except Exception as e:
        print(f"  {host:22s} FAILED {type(e).__name__}: {str(e)[:80]}")
