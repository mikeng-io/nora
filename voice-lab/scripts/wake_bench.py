
import numpy as np, onnxruntime as ort, wave, time, os
os.chdir(os.path.expanduser("~/workspace/voice-lab"))
so = ort.SessionOptions(); so.intra_op_num_threads = 1
P=["CPUExecutionProvider"]
vad = ort.InferenceSession("models/silero_vad.onnx", so, providers=P)
mel = ort.InferenceSession("models/melspectrogram.onnx", so, providers=P)
emb = ort.InferenceSession("models/embedding_model.onnx", so, providers=P)
cls = ort.InferenceSession("models/alexa_v0.1.onnx", so, providers=P)
with wave.open("/tmp/b30.wav") as w:
    pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)/32768.0
dur=len(pcm)/16000.0

# --- silero v5 with 64-sample context prepend ---
def vad_stream(x):
    ctx = np.zeros(64, dtype=np.float32); st=np.zeros((2,1,128),dtype=np.float32); sr=np.array(16000,dtype=np.int64); out=[]
    for i in range(0,len(x)-511,512):
        buf = np.concatenate([ctx, x[i:i+512]])
        o = vad.run(None,{"input":buf.reshape(1,-1),"state":st,"sr":sr}); st=o[1]
        out.append(float(np.ravel(o[0])[0])); ctx = x[i+512-64:i+512]
    return np.array(out)
t0=time.process_time(); p=vad_stream(pcm); tv=time.process_time()-t0
print(f"VAD(ctx64): cpu={tv:.3f}s over {dur:.1f}s -> {tv/dur*100:.2f}% of 1 core | max={p.max():.3f} mean={p.mean():.3f} frac>0.5={(p>0.5).mean():.3f}")

# --- openWakeWord full pipeline: mel stream -> 96-d embeddings hop 8 -> 16-emb context classifier ---
def mel_stream(x, win=12800, hop=160):
    frames=[]
    for i in range(0, len(x)-win+1, hop):
        m = mel.run(None,{"input":x[i:i+win].reshape(1,-1)})[0]
        frames.append(m.reshape(-1,32)[:76])
    return np.stack(frames)
t0=time.process_time()
melw = mel_stream(pcm)
embs=[]
for i in range(len(melw)):
    e = emb.run(None,{"input_1":melw[i].reshape(1,76,32,1)})[0]
    embs.append(e.reshape(96))
embs=np.stack(embs)
scores=[]
for j in range(len(embs)-15):
    o = cls.run(None,{"onnx::Flatten_0":embs[j:j+16].reshape(1,16,96).astype(np.float32)})[0]
    scores.append(float(np.ravel(o)[0]))
tw=time.process_time()-t0
print(f"WAKE(mel+emb+cls): {len(embs)} emb, {len(scores)} scores | cpu={tw:.3f}s over {dur:.1f}s -> {tw/dur*100:.2f}% of 1 core | max score={max(scores):.4f}")
