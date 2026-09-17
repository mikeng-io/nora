import glob, re, os
from funasr import AutoModel
m = AutoModel(model="iic/SenseVoiceSmall", disable_update=True, device="cpu")
PAIRS = [("係","系"),("唔該","唔该"),("幫","帮"),("嘅","既","嘅")]
# yue-specific lexical markers: a Mandarin-voiced render tends to lose these
LEX = ["而家","唔該","唔该","嘅","下一首","幫","帮"]
print(f"{'sample':42s} {'yue_lex':>8s}  transcript")
for f in sorted(glob.glob("samples/*.mp3")):
    r = m.generate(input=f, language="yue", use_itn=True)
    txt = re.sub(r"<\|[^|]*\|>", "", r[0]["text"]).strip()
    hits = sum(1 for k in LEX if k in txt)
    print(f"{os.path.basename(f):42s} {hits:>8d}  {txt}", flush=True)
print("DONE")
