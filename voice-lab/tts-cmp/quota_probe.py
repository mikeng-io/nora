import json, os, time, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}

def quota():
    r = urllib.request.Request("https://api.minimaxi.com/v1/token_plan/remains", headers=H)
    with urllib.request.urlopen(r, timeout=25) as x: d = json.load(x)
    return {m["model_name"]: (m.get("current_interval_remaining_percent"),
                              m.get("current_weekly_remaining_percent")) for m in d["model_remains"]}

print("quota BEFORE:", quota())

# large TTS run — 30 x ~60 hanzi Cantonese = ~3600 billed chars
text = "而家為你播放陳奕迅嘅富士山下，呢首歌係二零零三年嘅作品，收錄喺《黑白灰》專輯入面，係好多人嘅青春回憶。"
print(f"\nper-call length: {len(text)} hanzi -> ~{len(text)*2} billed chars")
ok = 0; tot = 0
t0 = time.time()
for i in range(30):
    p = {"model":"speech-2.8-turbo","text":text,"stream":False,
      "voice_setting":{"voice_id":"Cantonese_CuteGirl","speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue"}
    r = urllib.request.Request("https://api.minimaxi.com/v1/t2a_v2",
        data=json.dumps(p).encode(), headers=H)
    try:
        with urllib.request.urlopen(r, timeout=60) as x: d = json.load(x)
        if d["base_resp"]["status_code"] == 0:
            ok += 1
            tot += d.get("extra_info",{}).get("usage_characters",0)
    except Exception as e:
        print("  fail:", type(e).__name__); break

print(f"\n30 calls: {ok} OK, {tot} billed chars, {time.time()-t0:.1f}s")
print(f"cost @ 2.00 CNY/10k chars = CNY {tot/10000*2.0:.4f}")

time.sleep(6)
print("\nquota AFTER: ", quota())

# MiniMax CLI — the documented way to use subscription key on multimodal
print("\n=== minimax-cli doc (how sub key reaches speech) ===")
