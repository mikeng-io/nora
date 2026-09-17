
import numpy as np, onnxruntime as ort, wave, time, os, subprocess
os.chdir(os.path.expanduser("~/workspace/voice-lab"))
P=["CPUExecutionProvider"]; so=ort.SessionOptions(); so.intra_op_num_threads=1; so.inter_op_num_threads=1
vad=ort.InferenceSession("models/silero_vad.onnx",so,providers=P)
mel=ort.InferenceSession("models/melspectrogram.onnx",so,providers=P)
emb=ort.InferenceSession("models/embedding_model.onnx",so,providers=P)
cls=ort.InferenceSession("models/alexa_v0.1.onnx",so,providers=P)
with wave.open("/tmp/b30.wav") as w:
    pcm=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(np.float32)/32768.0
dur=len(pcm)/16000.0

# --- REAL openWakeWord cadence: melspec over 12800-sample window, step 1280 (80ms); embed+classify each step ---
STEP=1280; WIN=12800
mellist=[]
t0=time.process_time()
for i in range(0,len(pcm)-WIN+1,STEP):
    m=mel.run(None,{"input":pcm[i:i+WIN].reshape(1,-1)})[0]
    mellist.append(m.reshape(-1,32)[-8:])          # 8 new mel frames per 80ms step
mel_cpu=time.process_time()-t0
melbuf=np.concatenate([np.zeros((8,32),dtype=np.float32)]*9+[mellist[0]])  # prime to 76
embs=[]
t0=time.process_time()
for chunk in mellist[1:]:
    melbuf=np.concatenate([melbuf,chunk])[-76:]
    e=emb.run(None,{"input_1":melbuf.reshape(1,76,32,1)})[0].reshape(96)
    embs.append(e)
emb_cpu=time.process_time()-t0
embs=np.stack(embs); scores=[]
t0=time.process_time()
for j in range(len(embs)-15):
    o=cls.run(None,{"onnx::Flatten_0":embs[j:j+16].reshape(1,16,96).astype(np.float32)})[0]
    scores.append(float(np.ravel(o)[0]))
cls_cpu=time.process_time()-t0

# VAD in series (ctx-64 silero v5), 512-sample frames
ctx=np.zeros(64,dtype=np.float32); st=np.zeros((2,1,128),dtype=np.float32); sr=np.array(16000,dtype=np.int64)
t0=time.process_time()
for i in range(0,len(pcm)-511,512):
    buf=np.concatenate([ctx,pcm[i:i+512]])
    o=vad.run(None,{"input":buf.reshape(1,-1),"state":st,"sr":sr}); st=o[1]; ctx=pcm[i+512-64:i+512]
vad_cpu=time.process_time()-t0

steps=len(mellist)
print(f"audio {dur:.1f}s | openWakeWord steps(80ms)={steps} embeddings={len(embs)} scores={len(scores)}")
print(f"  melspec  cpu={mel_cpu:.3f}s -> {mel_cpu/dur*100:.2f}% of 1 core")
print(f"  embed    cpu={emb_cpu:.3f}s -> {emb_cpu/dur*100:.2f}% of 1 core")
print(f"  classify cpu={cls_cpu:.3f}s -> {cls_cpu/dur*100:.2f}% of 1 core")
print(f"  VAD      cpu={vad_cpu:.3f}s -> {vad_cpu/dur*100:.2f}% of 1 core")
tot=mel_cpu+emb_cpu+cls_cpu+vad_cpu
print(f"  TOTAL front-end cpu={tot:.3f}s -> {tot/dur*100:.2f}% of ONE core realtime  ({tot/dur*100/8:.2f}% of this 8-core box)")
