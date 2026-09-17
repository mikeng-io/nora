import http.client, json, os, time, urllib.request, socket, ssl
key = os.environ["MINIMAX_CN_API_KEY"]
HOST = "api.minimaxi.com"
IP = "47.79.2.234"
TEXT = "好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。"

ctx = ssl.create_default_context()

class PinnedHTTPConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = socket.create_connection((IP, 443), timeout=30)
        self.sock = ctx.wrap_socket(sock, server_hostname=HOST)

class PinnedHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PinnedHTTPConnection, req)

op = urllib.request.build_opener(PinnedHTTPS)

def call(model, boost, stream):
    payload = {"model": model, "text": TEXT, "stream": stream,
      "voice_setting": {"voice_id": "female-shaonv", "speed": 1.0, "vol": 1.0, "pitch": 0},
      "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
      "language_boost": boost, "subtitle_enable": False}
    req = urllib.request.Request(f"https://{HOST}/v1/t2a_v2",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    t0 = time.perf_counter(); ttfa = None; ch = 0; info = {}
    with op.open(req, timeout=120) as r:
        if not stream:
            d = json.load(r); return None, time.perf_counter()-t0, 1, d.get("extra_info") or {}, d.get("base_resp")
        for line in r:
            line = line.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"): continue
            b = line[5:].strip()
            if b in ("", "[DONE]"): continue
            try: d = json.loads(b)
            except Exception: continue
            if (d.get("data") or {}).get("audio"):
                if ttfa is None: ttfa = time.perf_counter()-t0
                ch += 1
            if d.get("extra_info"): info = d["extra_info"]
    return ttfa, time.perf_counter()-t0, ch, info, None

print("HOST", HOST, "IP", IP, "| text", len(TEXT), "chars\n")
print(f"{'model':18s} {'boost':12s} {'TTFA(ms)':>9s} {'total(ms)':>10s} {'chunks':>7s} {'audio_s':>8s} {'chars billed':>13s}")
for model in ("speech-2.8-turbo", "speech-2.8-hd"):
    for boost in ("Chinese,Yue", "auto"):
        try:
            t, d, ch, info, br = call(model, boost, True)
            print(f"{model:18s} {boost:12s} {(t or 0)*1000:9.0f} {d*1000:10.0f} {ch:7d} {info.get('audio_length',0)/1000:8.2f} {info.get('usage_characters','?'):>13}")
        except Exception as e:
            print(f"{model:18s} {boost:12s} ERR {type(e).__name__}: {e}")
