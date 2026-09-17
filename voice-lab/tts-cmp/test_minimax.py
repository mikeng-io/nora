import json, os, time, urllib.request
TEXT = "喂，我係 Nora。而家幫你播下一首，唔該等等。"
key = os.environ["MINIMAX_CN_API_KEY"]
def call(model, boost):
    payload = {
        "model": model, "text": TEXT, "stream": False,
        "voice_setting": {"voice_id": "female-shaonv", "speed": 1.0, "vol": 1.0, "pitch": 0},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        "language_boost": boost, "subtitle_enable": False,
    }
    req = urllib.request.Request(
        "https://api.minimaxi.com/v1/t2a_v2",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.load(r)
    dt = time.perf_counter() - t0
    return d, dt

for model in ("speech-2.8-turbo", "speech-2.8-hd"):
    for boost in ("Chinese,Yue", "auto"):
        try:
            d, dt = call(model, boost)
            br = d.get("base_resp", {})
            audio = (d.get("data") or {}).get("audio") or ""
            print(f"{model:18s} boost={boost:12s} {dt*1000:7.0f}ms  status={br.get('status_code')} msg={br.get('status_msg')!r} audio_hex_chars={len(audio)} extra={d.get('extra_info')}")
            if audio:
                fn = f"{model}_{boost.replace(',','-')}.mp3"
                open(fn, "wb").write(bytes.fromhex(audio))
        except Exception as e:
            print(f"{model} boost={boost} ERROR {type(e).__name__}: {e}")
