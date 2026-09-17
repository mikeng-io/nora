import json, os, struct, subprocess, urllib.request, math, time
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}
TEXT = "而家為你播放下一首歌，呢首歌係陳奕迅嘅經典作品，收錄喺佢嘅專輯入面，希望你會鍾意。"

def t2a(model, voice="Cantonese_CuteGirl", text=TEXT):
    p = {"model":model,"text":text,"stream":False,
      "voice_setting":{"voice_id":voice,"speed":1.0,"vol":1.0,"pitch":0},
      "audio_setting":{"sample_rate":32000,"bitrate":128000,"format":"mp3","channel":1},
      "language_boost":"Chinese,Yue"}
    r = urllib.request.Request("https://api.minimaxi.com/v1/t2a_v2", data=json.dumps(p).encode(), headers=H)
    with urllib.request.urlopen(r, timeout=180) as x: return json.load(x)

def to_wav(mp3, wav):
    subprocess.run(["ffmpeg","-y","-v","error","-i",mp3,"-ac","1","-ar","32000","-f","wav",wav], check=True)

def read_pcm(wav):
    d = open(wav,"rb").read()
    i = d.find(b"data"); i += 8
    n = struct.unpack("<I", d[i-4:i])[0]
    return [struct.unpack("<h", d[j:j+2])[0] for j in range(i, i+n-1, 2)]

print("Generating samples...")
for m in ["turbo","hd"]:
    model = f"speech-2.8-{m}"
    d = t2a(model)
    open(f"/tmp/cmp_{m}.mp3","wb").write(bytes.fromhex(d["data"]["audio"]))
    to_wav(f"/tmp/cmp_{m}.mp3", f"/tmp/cmp_{m}.wav")

a = read_pcm("/tmp/cmp_turbo.wav")
b = read_pcm("/tmp/cmp_hd.wav")
print(f"  turbo samples={len(a)}  hd samples={len(b)}  diff_len={abs(len(a)-len(b))}")

n = min(len(a), len(b))
diffs = [a[i]-b[i] for i in range(n)]
rms_a = math.sqrt(sum(x*x for x in a[:n])/n)
rms_d = math.sqrt(sum(x*x for x in diffs)/n)
max_d = max(abs(x) for x in diffs)
print(f"\n=== WAVEFORM COMPARISON (PCM 16-bit) ===")
print(f"  turbo RMS energy : {rms_a:10.1f}")
print(f"  DIFF RMS         : {rms_d:10.1f}")
print(f"  DIFF peak        : {max_d:10d}")
print(f"  relative diff    : {rms_d/rms_a*100:.4f}% of signal")
ident = sum(1 for x in diffs if x == 0)
print(f"  identical samples: {ident}/{n} = {ident/n*100:.2f}%")

# spectral centroid comparison (brightness/timbre proxy)
def centroid(sig, sr=32000, frame=1024):
    import cmath
    vals=[]
    for s in range(0, len(sig)-frame, frame*4):
        seg = sig[s:s+frame]
        # crude DFT magnitude via Goertzel over bins
        best=0; num=0; den=0
        for k in range(1, frame//2):
            re=im=0.0
            for i in range(0, frame, 8):
                ang = -2*math.pi*k*i/frame
                re += seg[i]*math.cos(ang); im += seg[i]*math.sin(ang)
            mag = math.hypot(re,im)
            num += (k*sr/frame)*mag; den += mag
        if den>0: vals.append(num/den)
    return sum(vals)/len(vals) if vals else 0

print(f"\n=== TIMBRE (spectral centroid, brightness proxy) ===")
print(f"  turbo centroid: {centroid(a):.1f} Hz")
print(f"  hd    centroid: {centroid(b):.1f} Hz")

# duration
def dur(p):
    o = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",p],
                       capture_output=True, text=True).stdout.strip()
    return float(o)
print(f"\n=== DURATION ===")
print(f"  turbo: {dur('/tmp/cmp_turbo.mp3'):.4f}s")
print(f"  hd   : {dur('/tmp/cmp_hd.mp3'):.4f}s")
