import subprocess, numpy as np, glob, os
def f0(path):
    raw = subprocess.run(["ffmpeg","-v","error","-i",path,"-ac","1","-ar","16000",
                          "-f","s16le","-"],capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    sr=16000; win=1024; hop=512; vals=[]
    for i in range(0, len(x)-win, hop):
        fr = x[i:i+win]
        if np.sqrt((fr**2).mean()) < 200: continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, 'full')[win-1:]
        lo, hi = int(sr/400), int(sr/70)
        seg = ac[lo:hi]
        if len(seg)<2 or ac[0]<=0: continue
        lag = lo + int(np.argmax(seg))
        if ac[lag]/ac[0] > 0.3: vals.append(sr/lag)
    return np.median(vals) if vals else float('nan')
groups = [("MINIMAX (native yue)", "samples/Cantonese_*.mp3"),
          ("EDGE-TTS zh-HK",        "samples/edgecmp/*.mp3")]
rows=[]
for g,pat in groups:
    print(f"\n--- {g} ---")
    for f in sorted(glob.glob(pat)):
        v=f0(f); rows.append((os.path.basename(f)[:-4], v))
        print(f"  {os.path.basename(f)[:-4]:32s} {v:7.1f} Hz")
cg=[v for n,v in rows if 'CuteGirl' in n]
print("\n--- closest edge-tts voice to your pick (Cantonese_CuteGirl, %.1f Hz) ---" % cg[0])
for n,v in rows:
    if n.startswith(("HiuGaai","HiuMaan")):
        print(f"  {n:32s} delta {v-cg[0]:+7.1f} Hz")
