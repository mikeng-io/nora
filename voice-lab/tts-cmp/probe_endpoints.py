import http.client, json, os, socket, ssl, urllib.request

key = os.environ["MINIMAX_CN_API_KEY"]

# (label, hostname, ip) — test every plausible CN/global endpoint
TARGETS = [
    ("api.minimaxi.com  (current)",     "api.minimaxi.com",   "47.79.117.67"),
    ("api-bj.minimaxi.com (backup)",    "api-bj.minimaxi.com","47.79.117.67"),
    ("api.minimax.chat  (legacy CN)",   "api.minimax.chat",   "47.79.2.234"),
    ("api.minimax.io    (global)",      "api.minimax.io",     None),
]

def probe(host, ip):
    ctx = ssl.create_default_context()
    class C(http.client.HTTPSConnection):
        def connect(self):
            target = (ip or host, 443)
            self.sock = ctx.wrap_socket(socket.create_connection(target, timeout=25),
                                        server_hostname=host)
    class H(urllib.request.HTTPSHandler):
        def https_open(self, req): return self.do_open(C, req)
    op = urllib.request.build_opener(H)
    payload = {"model":"speech-2.8-turbo","text":"好，搞掂。","stream":False,
      "voice_setting":{"voice_id":"Cantonese_CuteGirl","speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue"}
    req = urllib.request.Request(f"https://{host}/v1/t2a_v2",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    try:
        with op.open(req, timeout=60) as r:
            d = json.load(r)
        br = d.get("base_resp",{})
        audio = d.get("data",{}).get("audio") or ""
        return f"HTTP200 status_code={br.get('status_code')} msg={br.get('status_msg','')!r} audio={len(audio)//2}B"
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:90]}"

print(f"{'endpoint':34s} result")
for label, host, ip in TARGETS:
    print(f"{label:34s} {probe(host, ip)}", flush=True)
