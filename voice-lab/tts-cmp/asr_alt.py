import glob, os, re
from funasr import AutoModel
m = AutoModel(model="iic/SenseVoiceSmall", device="cpu", disable_update=True, trust_remote_code=False)
MARKERS = ["我幫你","而家","嘅","話我知","陳奕迅"]
f = "samples/retry/CuteGirl_via_minimax_chat.mp3"
d = m.generate(input=f, language="yue", use_itn=False, batch_size_s=60)
t = re.sub(r"<\|[^|]*\|>", "", d[0]["text"]).strip()
print("CuteGirl via api.minimax.chat (the UNBLOCKED endpoint)")
print("  ASR:", t)
print("  yue markers:", [k for k in MARKERS if k in t])
print("  reference   : 好，我幫你播下一首。而家嘅歌係陳奕迅嘅《富士山下》，如果你想跳去第二首就話我知。")
