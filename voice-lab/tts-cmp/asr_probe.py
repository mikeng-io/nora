import base64, json, os, time, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}

def t2a(text, model="speech-2.8-turbo", voice="Cantonese_CuteGirl"):
    p = {"model":model,"text":text,"stream":False,
      "voice_setting":{"voice_id":voice,"speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue"}
    r = urllib.request.Request("https://api.minimaxi.com/v1/t2a_v2", data=json.dumps(p).encode(), headers=H)
    with urllib.request.urlopen(r, timeout=120) as x: return json.load(x)

SRC = "而家為你播放陳奕迅嘅富士山下。"
d = t2a(SRC)
mp3 = bytes.fromhex(d["data"]["audio"])
open("/tmp/asr_test.mp3","wb").write(mp3)
print(f"1) TTS made: {len(mp3)}B  source={SRC!r}")

# try the ASR endpoints MiniMax exposes
forms = []
b64 = base64.b64encode(mp3).decode()
forms.append(("v1/speech_to_text (base64)",
  "https://api.minimaxi.com/v1/speech_to_text",
  {"model":"speech-2.6-ASR","audio_base64":b64,"language_boost":"Chinese,Yue"}))
forms.append(("v1/audio/transcriptions",
  "https://api.minimaxi.com/v1/audio/transcriptions",
  {"model":"whisper-1","audio_base64":b64}))
for name, url, body in forms:
    try:
        r = urllib.request.Request(url, data=json.dumps(body).encode(), headers=H)
        with urllib.request.urlopen(r, timeout=60) as x:
            out = json.load(x)
        print(f"2) ASR {name}: {json.dumps(out, ensure_ascii=False)[:300]}")
    except Exception as e:
        print(f"2) ASR {name}: FAIL {type(e).__name__} {str(e)[:80]}")
