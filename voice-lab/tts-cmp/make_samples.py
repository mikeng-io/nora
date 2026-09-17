import json, os, subprocess, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}

# a longer, natural Nora-style line so the difference is actually audible
LINE = ("你好 Miles，今日嘅天氣報告話下晝會有驟雨，記得帶遮呀。"
        "另外你部遊戲伺服器而家運作正常，玩家有十二個，延遲平均二十三毫秒。")

def t2a(model, voice, out):
    p = {"model":model,"text":LINE,"stream":False,
      "voice_setting":{"voice_id":voice,"speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue"}
    r = urllib.request.Request("https://api.minimaxi.com/v1/t2a_v2", data=json.dumps(p).encode(), headers=H)
    with urllib.request.urlopen(r, timeout=180) as x: d = json.load(x)
    a = bytes.fromhex(d["data"]["audio"])
    open(out,"wb").write(a)
    print(f"  {model:18s} {voice:26s} -> {out} ({len(a)} bytes)")

D = "/home/norty/workspace/voice-lab-data/tts-cmp/samples"
os.makedirs(D, exist_ok=True)
print("=== generating audible A/B for the 6 Cantonese voices ===")
for m in ["speech-2.8-turbo","speech-2.8-hd"]:
    for v in ["Cantonese_CuteGirl","Cantonese_GentleLady","Cantonese_KindWoman",
              "Cantonese_PlayfulMan","Cantonese_ProfessionalHost（F)","Cantonese_ProfessionalHost（M)"]:
        safe = v.replace("（","(").replace("）",")")
        t2a(m, v, f"{D}/{m.replace('speech-2.8-','')}__{safe}.mp3")

print("\n=== convert to a single combined A/B mp3 for easy listening ===")
# concat turbo then hd for the same voice
subprocess.run(["ffmpeg","-y","-v","error",
  "-i",f"{D}/turbo__Cantonese_CuteGirl.mp3","-i",f"{D}/hd__Cantonese_CuteGirl.mp3",
  "-filter_complex","[0:a][1:a]concat=n=2:v=0:a=1",
  f"{D}/AB_cutegirl_turbo_then_hd.mp3"], check=True)
print("  AB_cutegirl_turbo_then_hd.mp3 created")
