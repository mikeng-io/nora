import json, os, urllib.request

key = os.environ["MINIMAX_CN_API_KEY"]

def post(host, path, payload):
    req = urllib.request.Request(f"https://{host}{path}",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

# 1) does the alternative CN endpoint expose the SAME voice roster?
print("=== voice roster parity ===")
for host in ["api.minimax.chat"]:
    d = post(host, "/v1/get_voice", {"voice_type":"all"})
    sv = d.get("system_voice", []) or []
    yue = [v for v in sv if "anton" in json.dumps(v, ensure_ascii=False).lower()]
    print(f"  {host}: status={d.get('base_resp',{}).get('status_code')} "
          f"system_voices={len(sv)} cantonese={len(yue)}")
    ids = []
    for v in yue:
        for vv in (v.get("voice_list") or []):
            if "antonese" in vv.get("voice_id",""):
                ids.append(vv["voice_id"])
    print(f"  cantonese ids: {sorted(ids)}")
    print(f"  CuteGirl present: {'Cantonese_CuteGirl' in ids}")

# 2) full-length synthesis + latency on the unblocked host
import time
TEXT = "好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。"
print("\n=== synthesis on api.minimax.chat (UNBLOCKED — normal DNS) ===")
payload = {"model":"speech-2.8-turbo","text":TEXT,"stream":False,
  "voice_setting":{"voice_id":"Cantonese_CuteGirl","speed":1.0,"vol":1.0,"pitch":0},
  "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
  "language_boost":"Chinese,Yue"}
t0=time.time(); d = post("api.minimax.chat","/v1/t2a_v2",payload); el=time.time()-t0
audio = bytes.fromhex(d["data"]["audio"])
open("samples/retry/CuteGirl_via_minimax_chat.mp3","wb").write(audio)
ei = d.get("extra_info",{})
print(f"  status={d['base_resp']['status_code']} bytes={len(audio)} "
      f"audio={ei.get('audio_length',0)/1000:.2f}s billed={ei.get('usage_characters')} latency={el:.2f}s")
