import http.client, json, os, socket, ssl, urllib.request, time
key=os.environ["MINIMAX_CN_API_KEY"]; HOST="api.minimaxi.com"; IP="47.79.2.234"
ctx=ssl.create_default_context()
class PC(http.client.HTTPSConnection):
    def connect(self): self.sock=ctx.wrap_socket(socket.create_connection((IP,443),timeout=30), server_hostname=HOST)
class PH(urllib.request.HTTPSHandler):
    def https_open(self,req): return self.do_open(PC,req)
op=urllib.request.build_opener(PH)
# three realistic Nora reply lengths
TEXTS = {
 "short (8ch)":  "好，搞掂。",
 "typical (35ch)": "好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》。",
 "long (90ch)": "好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。仲有三分鐘就完，我等緊你決定，唔該。",
}
print(f"{'case':16s} {'chars_in':>8s} {'billed':>7s} {'ratio':>6s} {'audio_s':>8s} {'cost/reply $':>13s} {'cost/1k $':>10s}")
for label, t in TEXTS.items():
    payload={"model":"speech-2.8-turbo","text":t,"stream":False,
      "voice_setting":{"voice_id":"Cantonese_GentleLady","speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue","subtitle_enable":False}
    req=urllib.request.Request(f"https://{HOST}/v1/t2a_v2",data=json.dumps(payload).encode(),
      method="POST",headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    with op.open(req,timeout=120) as r: d=json.load(r)
    info=d.get("extra_info") or {}
    billed=info.get("usage_characters",0)
    cin=len(t)
    c_turbo=billed*60/1_000_000; c_hd=billed*100/1_000_000
    print(f"{label:16s} {cin:8d} {billed:7d} {billed/cin:6.2f} {info.get('audio_length',0)/1000:8.2f} {c_turbo:13.5f} {c_turbo*1000:10.3f}")
print("\n(price: turbo $60/M chars, hd $100/M chars — MiniMax paygo docs)")
print("edge-tts: $0 (only bandwidth/host cpu)")
