import json, os, statistics, subprocess, urllib.request
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

def centroid(mp3):
    raw = mp3.replace(".mp3",".raw")
    subprocess.run(["ffmpeg","-y","-v","error","-i",mp3,"-ac","1","-ar","32000","-f","f32le",raw], check=True)
    x = np.fromfile(raw, dtype=np.float32)
    w = x[:32000*8]
    if len(w) < 32000: return None
    f = np.abs(np.fft.rfft(w*np.hanning(len(w))))
    fr = np.fft.rfftfreq(len(w), 1/32000)
    return (fr*f).sum()/f.sum()

N = 6
res = {}
for model in ["speech-2.8-turbo","speech-2.8-hd"]:
    lat, durs, cents = [], [], []
    for i in range(N):
        import time; t0=time.time()
        d = t2a(model); el = time.time()-t0
        a = bytes.fromhex(d["data"]["audio"])
        p = f"/tmp/b_{model}_{i}.mp3"; open(p,"wb").write(a)
        lat.append(el); durs.append(len(a)); 
        c = centroid(p)
        if c: cents.append(c)
    res[model] = (lat, durs, cents)
    print(f"{model}")
    print(f"  latency  median={statistics.median(lat):5.2f}s  min={min(lat):5.2f} max={max(lat):5.2f}")
    print(f"  mp3 size median={statistics.median(durs):8.0f}  min={min(durs)} max={max(durs)}")
    print(f"  centroid median={statistics.median(cents):7.1f} Hz  sd={statistics.stdev(cents):6.1f}")

t, h = res["speech-2.8-turbo"], res["speech-2.8-hd"]
print("\n=== HD vs TURBO (n=%d each) ===" % N)
print(f"  latency  : turbo {statistics.median(t[0]):.2f}s vs hd {statistics.median(h[0]):.2f}s")
print(f"  mp3 bytes: turbo {statistics.median(t[1]):.0f} vs hd {statistics.median(h[1]):.0f}")
print(f"  centroid : turbo {statistics.median(t[2]):.1f}Hz (sd {statistics.stdev(t[2]):.0f}) vs hd {statistics.median(h[2]):.1f}Hz (sd {statistics.stdev(h[2]):.0f})")
# is centroid difference bigger than within-model sd?
gap = abs(statistics.median(t[2]) - statistics.median(h[2]))
pooled = (statistics.stdev(t[2]) + statistics.stdev(h[2]))/2
print(f"  centroid gap {gap:.0f}Hz vs within-model sd {pooled:.0f}Hz -> {'SEPARABLE' if gap > pooled else 'NOT separable (within noise)'}")
