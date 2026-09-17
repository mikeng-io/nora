
import numpy as np, onnxruntime as ort, wave, time, os
os.chdir(os.path.expanduser("~/workspace/voice-lab"))
so = ort.SessionOptions(); so.intra_op_num_threads = 1
P=["CPUExecutionProvider"]
mel = ort.InferenceSession("models/melspectrogram.onnx", so, providers=P)
emb = ort.InferenceSession("models/embedding_model.onnx", so, providers=P)
cls = ort.InferenceSession("models/alexa_v0.1.onnx", so, providers=P)
vad = ort.InferenceSession("models/silero_vad.onnx", so, providers=P)

with wave.open("/tmp/b30.wav") as w:
    pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)/32768.0
W=12800
win = pcm[W*4:W*5]
m = mel.run(None, {"input": win.reshape(1,-1)})[0]
print("mel shape", m.shape)
mm = m.reshape(-1,32)[:76]
print("emb in", mm.shape, "->", emb.run(None, {"input_1": mm.reshape(1,76,32,1)})[0].shape)
e = emb.run(None, {"input_1": mm.reshape(1,76,32,1)})[0]
print("cls out", cls.run(None, {"onnx::Flatten_0": e.reshape(1,16,96)})[0].ravel())

# VAD probe on a loud mid-clip window + full-clip
def vad_probs(x):
    st=np.zeros((2,1,128),dtype=np.float32); sr=np.array(16000,dtype=np.int64); out=[]
    for i in range(0,len(x)-511,512):
        o=vad.run(None,{"input":x[i:i+512].reshape(1,-1),"state":st,"sr":sr}); st=o[1]; out.append(float(np.ravel(o[0])[0]))
    return np.array(out)
for label, seg in [("first30s",pcm[:480000]), ("mid10s",pcm[480000:640000])]:
    p=vad_probs(seg); print(label,"max",round(p.max(),3),"mean",round(p.mean(),3),"frac>0.5",round((p>0.5).mean(),3))
