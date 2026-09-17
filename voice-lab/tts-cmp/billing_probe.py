import json, os, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]

def t2a(text, model="speech-2.8-turbo", voice="Cantonese_CuteGirl"):
    p = {"model":model,"text":text,"stream":False,
      "voice_setting":{"voice_id":voice,"speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue"}
    r = urllib.request.Request("https://api.minimaxi.com/v1/t2a_v2",
        data=json.dumps(p).encode(), method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    with urllib.request.urlopen(r, timeout=60) as x: return json.load(x)

# PROVE the "1 漢字 = 2 字符" billing rule
cases = [("測試", "2 hanzi"), ("你好世界", "4 hanzi"), ("AB", "2 latin"),
         ("測試AB", "2 hanzi + 2 latin")]
print("=== empirical character-billing rule ===")
for txt, label in cases:
    d = t2a(txt)
    uc = d.get("extra_info",{}).get("usage_characters")
    print(f"  {label:22s} text={txt!r:12s} usage_characters={uc}")

print("\n=== HD vs Turbo on same text (rate check) ===")
for m in ["speech-2.8-hd","speech-2.8-turbo"]:
    d = t2a("而家嘅歌係陳奕迅嘅《富士山下》。", model=m)
    ei = d.get("extra_info",{})
    print(f"  {m:18s} status={d['base_resp']['status_code']} voiced={ei.get('usage_characters')}")

print("\n=== does Token Plan key reach EVERY Cantonese voice? ===")
for v in ["Cantonese_CuteGirl","Cantonese_GentleLady","Cantonese_KindWoman",
          "Cantonese_PlayfulMan","Cantonese_ProfessionalHost（F)","Cantonese_ProfessionalHost（M)"]:
    try:
        d = t2a("測試", voice=v)
        sc = d["base_resp"]["status_code"]
        n = len(bytes.fromhex(d["data"]["audio"])) if d.get("data",{}).get("audio") else 0
        print(f"  {v:34s} status={sc} {n}B {'OK' if sc==0 else d['base_resp'].get('status_msg')}")
    except Exception as e:
        print(f"  {v:34s} FAIL {type(e).__name__}: {str(e)[:50]}")
