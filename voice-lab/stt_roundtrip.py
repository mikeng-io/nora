import glob, re, os
from funasr import AutoModel
m = AutoModel(model="iic/SenseVoiceSmall", disable_update=True, device="cpu")
# cantonese identity tokens, in BOTH scripts so ASR normalization can't fool us
KEY = {"唔該", "唔该", "而家", "係", "系", "下一首", "播", "幫", "帮", "點", "粤"}
PAIRS = [("係","系"),("唔該","唔该"),("幫","帮")]
for f in sorted(glob.glob("tts-cmp/*.mp3")):
    r = m.generate(input=f, language="yue", use_itn=True)
    txt = re.sub(r"<\|[^|]*\|>", "", r[0]["text"]).strip()
    hits = sorted({k for k in KEY if k in txt})
    core = sum(1 for a,b in PAIRS if a in txt or b in txt)
    print(f"{os.path.basename(f):34s} | yue-core {core}/3 | {txt}", flush=True)
print("DONE")
