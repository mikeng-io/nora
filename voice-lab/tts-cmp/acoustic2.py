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

def pcm(short):
    model = f"speech-2.8-{short}"
    d = t2a(model)
    open(f"/tmp/z_{short}.mp3","wb").write(bytes.fromhex(d["data"]["audio"]))
    subprocess.run(["ffmpeg","-y","-v","error","-i",f"/tmp/z_{short}.mp3",
                    "-ac","1","-ar","32000","-f","f32le",f"/tmp/z_{short}.raw"], check=True)
    return np.fromfile(f"/tmp/z_{short}.raw", dtype=np.float32)

a = pcm("turbo"); b = pcm("hd")
print(f"samples: turbo={len(a)} ({len(a)/32000:.3f}s)  hd={len(b)} ({len(b)/32000:.3f}s)")

# cross-correlate to find best alignment lag
n = min(len(a), len(b))
corr = np.correlate(a[:n]-a[:n].mean(), b[:n]-b[:n].mean(), mode="full")
lag = corr.argmax() - (n - 1)
print(f"best alignment lag: {lag} samples ({lag/32000*1000:.1f} ms)")

# align and compare
if lag >= 0:
    aa, bb = a[lag:], b[:len(a)-lag]
else:
    aa, bb = a[:len(b)+lag], b[-lag:]
m = min(len(aa), len(bb))
aa, bb = aa[:m], bb[:m]

rms_a = np.sqrt((aa**2).mean()); rms_b = np.sqrt((bb**2).mean())
diff = aa - bb
rms_d = np.sqrt((diff**2).mean())
# correlation coefficient -> similarity
cc = np.corrcoef(aa, bb)[0,1]

print(f"\n=== ALIGNED WAVEFORM COMPARISON ===")
print(f"  turbo RMS      : {rms_a:.5f}")
print(f"  hd    RMS      : {rms_b:.5f}")
print(f"  diff  RMS      : {rms_d:.5f}   ({rms_d/rms_a*100:.2f}% of signal)")
print(f"  correlation    : {cc:.6f}")
print(f"  diff peak      : {np.abs(diff).max():.5f}")

# spectral comparison
def spec(x):
    w = x[:32000*8]
    f = np.abs(np.fft.rfft(w * np.hanning(len(w))))
    fr = np.fft.rfftfreq(len(w), 1/32000)
    return f, fr
fa, fr = spec(aa); fb, _ = spec(bb)
# spectral centroid
ca = (fr*fa).sum()/fa.sum(); cb = (fr*fb).sum()/fb.sum()
print(f"\n=== SPECTRAL ===")
print(f"  turbo centroid : {ca:.1f} Hz")
print(f"  hd    centroid : {cb:.1f} Hz   (diff {abs(ca-cb):.1f} Hz)")
# energy in bands
def band(f, fr, lo, hi): 
    m=(fr>=lo)&(fr<hi); return f[m].sum()/f.sum()*100
for lo,hi in [(0,1000),(1000,4000),(4000,8000),(8000,16000)]:
    print(f"  {lo:5d}-{hi:5d}Hz : turbo {band(fa,fr,lo,hi):5.2f}%   hd {band(fb,fr,lo,hi):5.2f}%")
