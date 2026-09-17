import json, os, time, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}

def q():
    r = urllib.request.Request("https://api.minimaxi.com/v1/token_plan/remains", headers=H)
    with urllib.request.urlopen(r, timeout=25) as x: d = json.load(x)
    return {m["model_name"]: (m.get("current_interval_remaining_percent"),
                              m.get("current_weekly_remaining_percent")) for m in d["model_remains"]}

def t2a(text, model="speech-2.8-turbo", voice="Cantonese_CuteGirl"):
    p = {"model":model,"text":text,"stream":False,
      "voice_setting":{"voice_id":voice,"speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue"}
    r = urllib.request.Request("https://api.minimaxi.com/v1/t2a_v2", data=json.dumps(p).encode(), headers=H)
    with urllib.request.urlopen(r, timeout=180) as x: return json.load(x)

base = "而家為你播放下一首歌，呢首歌係陳奕迅嘅經典作品，收錄喺佢嘅專輯入面。"
big = base * 60                      # 60 x 34 = 2040 hanzi
print(f"big text: {len(big)} hanzi -> ~{len(big)*2} billed chars")

b4 = q(); print("BEFORE:", b4)
d = t2a(big)
ei = d.get("extra_info",{})
sc = d["base_resp"]["status_code"]
print(f"sync status={sc} billed={ei.get('usage_characters')} audio={ei.get('audio_length',0)/1000:.1f}s")
time.sleep(8)
af = q(); print("AFTER: ", af)

for k in af:
    if b4[k][0] != af[k][0]:
        print(f"  -> {k} 5h window moved {b4[k][0]}% -> {af[k][0]}%  (delta {b4[k][0]-af[k][0]} pts)")

# also test HD rate vs turbo (should cost 1.75x more)
print("\n=== HD vs Turbo quota cost, smaller text ===")
for m in ["speech-2.8-hd","speech-2.8-turbo"]:
    d = t2a(base*10, model=m)
    ei = d.get("extra_info",{})
    print(f"  {m:18s} billed={ei.get('usage_characters')}  (rate {'3.50' if 'hd' in m else '2.00'} CNY/10k)")
