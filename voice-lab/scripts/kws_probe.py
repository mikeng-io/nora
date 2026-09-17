
import os, glob, numpy as np, torch, time
os.environ.setdefault("MODELSCOPE_CACHE","/home/norty/workspace/voice-lab-data/models/ms")
from funasr import AutoModel
P="/home/norty/workspace/voice-lab-data/models/ms/models/iic--speech_charctc_kws_phone-xiaoyun/snapshots/master"
m = AutoModel(model=P, keywords="喂 諾 婷,喂 nora,小 云 小 云", device="cpu", disable_update=True)
print("MODEL LOADED")
ex = P+"/example/kws_xiaoyunxiaoyun.wav"
r = m.generate(input=ex, cache={})
print("example(小云小云) ->", r)
