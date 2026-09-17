#!/usr/bin/env python3
"""Generate 10k+ wake-word positives with MiniMax CN TTS (6 Cantonese voices).

Same RPM-aware core as gen_wake_minimax.py but scaled for real openWakeWord
training (official guidance: n_samples >= 10,000). Adds per-rep jitter:
  - speed: 0.9–1.1 (uniform)
  - pitch: -2..+2 Hz (uniform)
so the 170 repetitions of each variant carry acoustic diversity instead of
being identical (TTS is stochastic anyway, but explicit jitter widens it).

Cost estimate: ~10,200 clips x ~13 billed chars ≈ 133k chars ≈ ¥26 turbo,
~13% of the 5h general window (96% → ~83% measured 2026-09-15 03:xx).

Output: data/wake-minimax-v2/<voice>/<idx>-<slug>-<rep>.mp3 + meta.json
"""
from __future__ import annotations

import json
import os
import random
import sys
import urllib.request
from pathlib import Path

API_KEY = os.environ.get("MINIMAX_CN_API_KEY", "")
ENDPOINT = "https://api.minimaxi.com/v1/t2a_v2"
BACKUP = "https://api.minimax.chat/v1/t2a_v2"
MODEL = os.environ.get("MINIMAX_WAKE_MODEL", "speech-2.8-turbo")
OUT = Path(__file__).resolve().parent.parent / "data" / "wake-minimax-v2"
REPS = int(os.environ.get("MINIMAX_WAKE_REPS", "170"))

VOICES = [
    "Cantonese_CuteGirl",
    "Cantonese_GentleLady",
    "Cantonese_KindWoman",
    "Cantonese_PlayfulMan",
    "Cantonese_ProfessionalHost（F)",
    "Cantonese_ProfessionalHost（M)",
]

# Base 10 variants, plus a handful of natural Cantonese extensions for lexical
# diversity at this scale (kept wake-phrase-forward; commands as trailing context).
VARIANTS = [
    "喂 Nora。",
    "喂，Nora。",
    "喂Nora。",
    "喂 Nora 呀。",
    "喂 Nora 唔該。",
    "喂 Nora 下一首。",
    "喂 Nora 停一停。",
    "喂 Nora 幫我播歌。",
    "喂 Nora 而家幾點呀？",
    "喂 Nora，你喺度嗎？",
    "喂 Nora 大聲啲。",
    "喂 Nora 細聲啲。",
    "喂 Nora 暫停。",
    "喂 Nora 繼續。",
    "喂 Nora 我返嚟喇。",
]

rng = random.Random(20260915)


def tts(text: str, voice: str, speed: float, pitch: int) -> bytes:
    import time
    payload = {
        "model": MODEL,
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": voice, "speed": speed, "vol": 1.0, "pitch": pitch},
        "audio_setting": {"sample_rate": 16000, "bitrate": 64000, "format": "wav", "channel": 1},
        "language_boost": "Chinese,Yue",
        "subtitle_enable": False,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    attempt = 0
    while True:
        attempt += 1
        last_err = None
        for url in (ENDPOINT, BACKUP):
            try:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = json.loads(r.read())
                br = body.get("base_resp", {})
                if br.get("status_code") != 0:
                    last_err = f"API {br.get('status_code')}: {br.get('status_msg')}"
                    if br.get("status_code") == 1002:
                        wait = 60 * attempt
                        print(f"  rate-limited; waiting {wait}s (attempt {attempt})", flush=True)
                        time.sleep(wait)
                        break
                    continue
                hex_audio = body.get("data", {}).get("audio", "")
                if not hex_audio:
                    last_err = "empty audio"
                    continue
                return bytes.fromhex(hex_audio)
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
        else:
            raise RuntimeError(f"TTS failed for voice={voice}: {last_err}")


def main() -> int:
    if not API_KEY:
        print("MINIMAX_CN_API_KEY not set", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    meta = []
    idx = 0
    for voice in VOICES:
        vdir = OUT / voice
        vdir.mkdir(exist_ok=True)
        for variant in VARIANTS:
            for rep in range(REPS):
                slug = variant.strip("。，、？").replace(" ", "-")[:12]
                name = f"{idx:05d}-{slug}-{rep}"
                out = vdir / f"{name}.wav"
                speed = round(rng.uniform(0.9, 1.1), 2)
                pitch = rng.randint(-2, 2)
                if out.exists() and out.stat().st_size > 100:
                    print(f"skip {out.name}", flush=True)
                else:
                    audio = tts(variant, voice, speed, pitch)
                    out.write_bytes(audio)
                    print(f"ok {voice} / {variant} / rep={rep} speed={speed} pitch={pitch} -> {out.name} ({len(audio)}B)", flush=True)
                meta.append({
                    "file": str(out.relative_to(OUT)),
                    "text": variant,
                    "voice": voice,
                    "variant": variant,
                    "rep": rep,
                    "speed": speed,
                    "pitch": pitch,
                    "label": "positive",
                })
                idx += 1
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"done: {len(meta)} clips -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
