import json, os, subprocess, urllib.request
import numpy as np
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}
TEXT = "而家為你播放下一首歌，呢首歌係陳奕迅嘅經典作品，收錄喺佢嘅專輯入面，希望你會鍾意。"

def t2a(model, voice="Cantonese_CuteGirl"):
    p = {"model":model,"text":TEXT,"stream":False,
      "voice_setting":{"voice_id":voice,"speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue"}
    r = urllib.request.Request("https://api.minimaxi.com/v1/t2a_v2", data=json.dumps(p).encode(), headers=H)
    with urllib.request.urlopen(r, timeout=180) as x: return json.load(x)

def pcm(tag, model):
    d = t2a(model)
    open(f"/tmp/q_{tag}.mp3","wb").write(bytes.fromhex(d["data"]["audio"]))
    subprocess.run(["ffmpeg","-y","-v","error","-i",f"/tmp/q_{tag}.mp3",
                    "-ac","1","-ar","32000","-f","f32le",f"/tmp/q_{tag}.raw"], check=True)
    x = np.fromfile(f"/tmp/q_{tag}.raw", dtype=np.float32)
    return x, d.get("extra_info",{})

# 2 runs per model -> gives within-model noise floor
runs = {}
for tag, model in [("t1","speech-2.8-turbo"), ("t2","speech-2.8-turbo"),
                   ("h1","speech-2.8-hd"),    ("h2","speech-2.8-hd")]:
    x, ei = pcm(tag, model)
    runs[tag] = x
    print(f"  {tag} ({model:16s}) {len(x)/32000:6.3f}s  billed={ei.get('usage_characters')}  audio_len={ei.get('audio_length')}")

def compare(p, q, label):
    # align by cross-correlation over a bounded window
    n = min(len(p), len(q), 32000*12)
    a = p[:n] - p[:n].mean(); b = q[:n] - q[:n].mean()
    c = np.correlate(a[:n], b[:n], mode="full")
    lag = c.argmax() - (n-1)
    if lag >= 0: aa, bb = p[lag:], q[:len(p)-lag]
    else:        aa, bb = p[:len(q)+lag], q[-lag:]
    m = min(len(aa), len(bb)); aa, bb = aa[:m], bb[:m]
    cc = np.corrcoef(aa, bb)[0,1]
    rms = np.sqrt(((aa-bb)**2).mean()) / np.sqrt((aa**2).mean()) * 100
    print(f"  {label:16s} corr={cc:+.4f}  diffRMS={rms:6.2f}%  dur_delta={abs(len(p)-len(q))/32000*1000:7.1f}ms")
    return cc

print("\n=== CONTROL: same model, two runs (NOISE FLOOR) ===")
c_tt = compare(runs["t1"], runs["t2"], "turbo vs turbo")
c_hh = compare(runs["h1"], runs["h2"], "hd vs hd")

print("\n=== TEST: turbo vs hd (cross-model) ===")
c_th = compare(runs["t1"], runs["h1"], "turbo vs hd")
c_th2= compare(runs["t2"], runs["h2"], "turbo vs hd (2)")

print(f"\n=== VERDICT ===")
print(f"  within-model corr : turbo {c_tt:+.4f} / hd {c_hh:+.4f}  (avg {(c_tt+c_hh)/2:+.4f})")
print(f"  cross-model corr  : {c_th:+.4f} / {c_th2:+.4f}  (avg {(c_th+c_th2)/2:+.4f})")
noise = (c_tt+c_hh)/2
cross = (c_th+c_th2)/2
print(f"  -> {'NO measurable systematic difference (cross ≈ noise)' if abs(cross-noise)<0.15 else 'SYSTEMATIC difference between models'}")
