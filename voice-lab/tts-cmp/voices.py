import http.client, json, os, socket, ssl, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]
HOST="api.minimaxi.com"; IP="47.79.2.234"
ctx = ssl.create_default_context()
class PC(http.client.HTTPSConnection):
    def connect(self):
        self.sock = ctx.wrap_socket(socket.create_connection((IP,443),timeout=30), server_hostname=HOST)
class PH(urllib.request.HTTPSHandler):
    def https_open(self, req): return self.do_open(PC, req)
op = urllib.request.build_opener(PH)
req = urllib.request.Request(f"https://{HOST}/v1/get_voice",
    data=json.dumps({"voice_type":"all"}).encode(), method="POST",
    headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
with op.open(req, timeout=60) as r:
    d = json.load(r)
print("base_resp:", d.get("base_resp"))
for k in ("system_voice","voice_cloning","voice_generation"):
    items = d.get(k) or []
    print(f"\n=== {k}: {len(items)} ===")
    for v in items:
        vid = v.get("voice_id","")
        # surface anything Cantonese/Yue-flavoured
        blob = json.dumps(v, ensure_ascii=False)
        if any(t in blob for t in ("粤","廣","Cantonese","Yue","yue","HK","hk")) or vid.lower().startswith(("hk","cant")):
            print("  HIT:", json.dumps(v, ensure_ascii=False)[:300])
    if k=="system_voice":
        print(f"  (showing first 8 of {len(items)} for reference)")
        for v in items[:8]:
            print("   -", v.get("voice_id"), "|", v.get("voice_name"), "|", str(v.get("description",""))[:80])
