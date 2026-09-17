import http.client, json, os, socket, ssl, urllib.request, time

key = os.environ["MINIMAX_CN_API_KEY"]
HOST = "api.minimaxi.com"
CANDIDATE_IPS = ["47.79.117.67", "47.79.2.234"]
TEXT = "好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。"

def make_opener(ip):
    ctx = ssl.create_default_context()
    class PinnedConn(http.client.HTTPSConnection):
        def connect(self):
            self.sock = ctx.wrap_socket(
                socket.create_connection((ip, 443), timeout=30),
                server_hostname=HOST)
    class PinnedHandler(urllib.request.HTTPSHandler):
        def https_open(self, req): return self.do_open(PinnedConn, req)
    return urllib.request.build_opener(PinnedHandler)

# 1) find a working IP
opener = ip_used = None
for ip in CANDIDATE_IPS:
    try:
        o = make_opener(ip)
        req = urllib.request.Request(f"https://{HOST}/v1/get_voice",
            data=json.dumps({"voice_type":"all"}).encode(), method="POST",
            headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
        with o.open(req, timeout=30) as r:
            d = json.load(r)
        if d.get("base_resp",{}).get("status_code") == 0:
            opener, ip_used = o, ip
            print(f"CONNECTED via pinned IP {ip}  (HTTP 200, {len(d.get('system_voice',[]))} system voices)")
            break
    except Exception as e:
        print(f"  IP {ip} failed: {type(e).__name__}: {e}")

if not opener:
    raise SystemExit("NO WORKING IP — MiniMax genuinely unreachable")

# 2) regenerate the six native Cantonese voices + his pick's HD variant
VOICES = ["Cantonese_GentleLady","Cantonese_ProfessionalHost（F)","Cantonese_KindWoman",
          "Cantonese_CuteGirl","Cantonese_ProfessionalHost（M)","Cantonese_PlayfulMan"]
os.makedirs("samples/retry", exist_ok=True)
results = []
for v in VOICES:
    payload = {"model":"speech-2.8-turbo","text":TEXT,"stream":False,
      "voice_setting":{"voice_id":v,"speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue","subtitle_enable":False}
    t0=time.time()
    req = urllib.request.Request(f"https://{HOST}/v1/t2a_v2",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    try:
        with opener.open(req, timeout=120) as r:
            d = json.load(r)
        sc = d.get("base_resp",{}).get("status_code")
        audio = bytes.fromhex(d["data"]["audio"]) if d.get("data",{}).get("audio") else b""
        fn = f"samples/retry/{v.replace('（','_').replace(')','').replace('(','')}.mp3"
        open(fn,"wb").write(audio)
        info = d.get("extra_info",{})
        results.append((v, sc, len(audio), info.get("audio_length",0), info.get("usage_characters",0), round(time.time()-t0,2)))
        print(f"  {v:32s} sc={sc} {len(audio):7d}B  audio={info.get('audio_length',0)/1000:5.2f}s  billed={info.get('usage_characters')}  {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"  {v:32s} FAILED {type(e).__name__}: {e}")
        results.append((v, "ERR", 0,0,0,0))

ok = [r for r in results if r[1] == 0 and r[2] > 0]
print(f"\nSUCCESS: {len(ok)}/{len(VOICES)} voices generated via pinned IP {ip_used}")
print(f"total bytes: {sum(r[2] for r in ok)}")
