import base64, json, os, struct, time, urllib.request

key = os.environ["MINIMAX_CN_API_KEY"]
PROFILE = "gezae3hoez"

def cloud(name):
    h = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    b = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    r = base64.urlsafe_b64encode(h + b + struct.pack(">HH",1,1)).decode().rstrip("=")
    try:
        with urllib.request.urlopen(f"https://76.76.2.22/{PROFILE}?dns={r}", timeout=15) as x:
            d = x.read()
    except Exception as e:
        return f"err {type(e).__name__}"
    an = struct.unpack(">H", d[6:8])[0]
    if an == 0: return "NOANSWER"
    o = 12
    while d[o] != 0: o += d[o] + 1
    o += 5
    out = []
    for _ in range(an):
        if d[o] & 0xC0 == 0xC0: o += 2
        else:
            while d[o] != 0: o += d[o] + 1
            o += 1
        t, _, _, l = struct.unpack(">HHIH", d[o:o+10]); o += 10
        dd = d[o:o+l]; o += l
        if t == 1: out.append(".".join(map(str, dd)))
        elif t == 5: out.append("CNAME")
    return ", ".join(out) or "none"

print("=== Control D CLOUD rule, 3 samples over 6s (was 0.0.0.0 before restart) ===")
for i in range(3):
    print(f"  cloud api.minimaxi.com -> {cloud('api.minimaxi.com')}")
    time.sleep(3)

TEXT = "好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。"
payload = {"model":"speech-2.8-turbo","text":TEXT,"stream":False,
  "voice_setting":{"voice_id":"Cantonese_CuteGirl","speed":1.0,"vol":1.0,"pitch":0},
  "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
  "language_boost":"Chinese,Yue"}

print("\n=== synthesis, NORMAL DNS, CN subscription ===")
for host in ["api.minimaxi.com", "api.minimax.chat"]:
    req = urllib.request.Request(f"https://{host}/v1/t2a_v2",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as x:
            d = json.load(x)
        sc = d["base_resp"]["status_code"]
        audio = bytes.fromhex(d["data"]["audio"])
        fn = f"samples/cnsub/FINAL_{host}.mp3"
        open(fn, "wb").write(audio)
        ei = d.get("extra_info", {})
        print(f"  {host:22s} status={sc} {len(audio):7d}B audio={ei.get('audio_length',0)/1000:.2f}s billed={ei.get('usage_characters')} {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"  {host:22s} FAIL {type(e).__name__}: {str(e)[:70]}")
