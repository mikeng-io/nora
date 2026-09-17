import http.client, json, os, socket, ssl, urllib.request, time
key=os.environ["MINIMAX_CN_API_KEY"]; HOST="api.minimaxi.com"; IP="47.79.2.234"
ctx=ssl.create_default_context()
class PC(http.client.HTTPSConnection):
    def connect(self): self.sock=ctx.wrap_socket(socket.create_connection((IP,443),timeout=30), server_hostname=HOST)
class PH(urllib.request.HTTPSHandler):
    def https_open(self,req): return self.do_open(PC,req)
op=urllib.request.build_opener(PH)
TEXT="好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。"
def gen(voice, model="speech-2.8-turbo", tag=""):
    payload={"model":model,"text":TEXT,"stream":False,
      "voice_setting":{"voice_id":voice,"speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue","subtitle_enable":False}
    req=urllib.request.Request(f"https://{HOST}/v1/t2a_v2",data=json.dumps(payload).encode(),
      method="POST",headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    t0=time.perf_counter()
    with op.open(req,timeout=120) as r: d=json.load(r)
    dt=time.perf_counter()-t0
    a=(d.get("data") or {}).get("audio") or ""
    info=d.get("extra_info") or {}
    if a:
        safe=voice.replace("（","(").replace("）",")").replace(" ","_").replace("(","").replace(")","")
        fn=f"samples/{safe}{tag}.mp3"
        open(fn,"wb").write(bytes.fromhex(a))
        print(f"  {voice:38s} {dt*1000:6.0f}ms  audio={info.get('audio_length',0)/1000:.2f}s -> {fn}")
    else:
        print(f"  {voice:38s} FAIL {d.get('base_resp')}")
print("=== NATIVE CANTONESE VOICES (speech-2.8-turbo, language_boost=Chinese,Yue) ===")
for v in ["Cantonese_GentleLady","Cantonese_ProfessionalHost（F)","Cantonese_KindWoman","Cantonese_CuteGirl","Cantonese_ProfessionalHost（M)","Cantonese_PlayfulMan"]:
    try: gen(v)
    except Exception as e: print(" ",v,"ERR",e)
print("\n=== CURRENT CONFIG (Mandarin voice, Yue boost) + HD comparison ===")
for v,m in [("female-shaonv","speech-2.8-turbo"),("Cantonese_GentleLady","speech-2.8-hd")]:
    try: gen(v,m,tag="_"+m.split("-")[-1])
    except Exception as e: print(" ",v,m,"ERR",e)
