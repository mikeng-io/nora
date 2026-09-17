import glob, os, re, sys
from funasr import AutoModel
m = AutoModel(model="iic/SenseVoiceSmall", device="cpu", disable_update=True, trust_remote_code=False)
EXPECTED = "好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。"
# Cantonese-distinctive tokens: a Mandarin-biased ASR would normalise these away
MARKERS = ["我幫你","而家","嘅","咗","話我知","陳奕迅"]
for f in sorted(glob.glob("samples/retry/*.mp3")):
    d = m.generate(input=f, language="yue", use_itn=False, batch_size_s=60)
    t = re.sub(r"<\|[^|]*\|>", "", d[0]["text"]).strip()
    hits = [k for k in MARKERS if k in t]
    print(f"\n{os.path.basename(f)[:-4]}")
    print(f"   ASR: {t}")
    print(f"   yue markers kept: {len(hits)}/{len(MARKERS)}  {hits}")
print("\nreference text:", EXPECTED)
