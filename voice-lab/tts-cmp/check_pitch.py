import subprocess, numpy as np, glob, os
def f0(path):
    # decode to mono 16k float via ffmpeg, autocorrelation F0 over voiced frames
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
print(f"{'sample':26s} {'median F0 (Hz)':>15s}")
res={}
for f in sorted(glob.glob("samples/edgecmp/*.mp3")):
    v=f0(f); res[os.path.basename(f)]=v
    print(f"{os.path.basename(f)[:-4]:26s} {v:15.1f}")
b=res.get("HiuGaai_baseline.mp3"); p=res.get("HiuGaai_brighter.mp3")
print(f"\npitch delta baseline->brighter: {p-b:+.1f} Hz  ({(p/b-1)*100:+.1f}%)")
print("PITCH SHIFT CONFIRMED" if p-b > 5 else "NO MEASURABLE PITCH SHIFT")
