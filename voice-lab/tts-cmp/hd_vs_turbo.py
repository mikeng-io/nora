import json, os, statistics, time, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}

TEXT = "而家為你播放下一首歌，呢首歌係陳奕迅嘅經典作品，收錄喺佢嘅專輯入面，希望你會鍾意。"

def t2a(model, sr=32000, br=128000, text=TEXT, voice="Cantonese_CuteGirl"):
    p = {"model":model,"text":text,"stream":False,
      "voice_setting":{"voice_id":voice,"speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":sr,"bitrate":br,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue"}
    r = urllib.request.Request("https://api.minimaxi.com/v1/t2a_v2", data=json.dumps(p).encode(), headers=H)
    t0 = time.time()
    with urllib.request.urlopen(r, timeout=180) as x: d = json.load(x)
    return d, time.time() - t0

print("="*68)
print("A/B: HD vs TURBO  (same text, same params)")
print(f"text: {len(TEXT)} hanzi -> ~{len(TEXT)*2} billed chars")
print("="*68)

results = {}
for model in ["speech-2.8-turbo", "speech-2.8-hd"]:
    times, sizes, billed, dur = [], [], [], []
    for i in range(3):
        d, el = t2a(model)
        if d["base_resp"]["status_code"] != 0:
            print(f"  {model} ERROR {d['base_resp']}"); break
        a = bytes.fromhex(d["data"]["audio"])
        ei = d.get("extra_info", {})
        times.append(el); sizes.append(len(a)); billed.append(ei.get("usage_characters")); dur.append(ei.get("audio_length"))
        open(f"/tmp/{model}_{i}.mp3","wb").write(a)
        time.sleep(1)
    results[model] = dict(t=times, sz=sizes, billed=billed[0] if billed else None,
                          dur=dur[0] if dur else None)
    mt = statistics.median(times)
    print(f"  {model:20s} median_latency={mt:5.2f}s  audio={dur[0]/1000 if dur[0] else 0:6.2f}s  bytes={sizes[0]:7d}  billed={billed[0]}")

print()
tur = results["speech-2.8-turbo"]; hd = results["speech-2.8-hd"]
if tur["t"] and hd["t"]:
    print(f"  latency: turbo {statistics.median(tur['t']):.2f}s  vs  hd {statistics.median(hd['t']):.2f}s"
          f"   -> hd is {statistics.median(hd['t'])/statistics.median(tur['t']):.2f}x slower")
    print(f"  bytes  : turbo {tur['sz'][0]}  vs  hd {hd['sz'][0]}  (same bitrate=128k requested)")
    print(f"  billed : turbo {tur['billed']}  vs  hd {hd['billed']}  (SAME text -> same char count)")
    print(f"  rate   : turbo CNY 2.00/10k  vs  hd CNY 3.50/10k  -> hd is 1.75x price")

print()
print("="*68)
print("SAMPLE-RATE SUPPORT (does HD allow higher fidelity?)")
print("="*68)
for model in ["speech-2.8-turbo", "speech-2.8-hd"]:
    ok = []
    for sr in [8000, 16000, 22050, 24000, 32000, 44100]:
        try:
            d, _ = t2a(model, sr=sr, text="測試音質。")
            sc = d["base_resp"]["status_code"]
            n = len(bytes.fromhex(d["data"]["audio"])) if d.get("data",{}).get("audio") else 0
            ok.append(f"{sr}:{'OK' if sc==0 else 'ERR'}({n}B)")
        except Exception as e:
            ok.append(f"{sr}:FAIL")
        time.sleep(0.4)
    print(f"  {model:20s} {'  '.join(ok)}")

print()
print("="*68)
print("BITRATE ceiling test @ 32000Hz")
print("="*68)
for model in ["speech-2.8-turbo", "speech-2.8-hd"]:
    out = []
    for br in [32000, 64000, 128000, 256000]:
        try:
            d, _ = t2a(model, sr=32000, br=br, text="測試音質。")
            n = len(bytes.fromhex(d["data"]["audio"])) if d.get("data",{}).get("audio") else 0
            sc = d["base_resp"]["status_code"]
            out.append(f"{br//1000}k:{'OK' if sc==0 else 'ERR'}({n}B)")
        except Exception as e:
            out.append(f"{br//1000}k:FAIL")
        time.sleep(0.4)
    print(f"  {model:20s} {'  '.join(out)}")
