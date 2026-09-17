#!/usr/bin/env python3
"""Generate adversarial negative wake clips with MiniMax CN TTS.

train.py's --generate_clips path is English-only (piper + pronouncing + CMU
phonemes; data.py:899 "Currently only works for english texts"). For a
Cantonese wake word we hand-generate phonetically-close traps and place them
directly in the negative_train/negative_test dirs that train.py --augment_clips
expects.

Layout (mirrors what piper generation would have produced):
  oww-data/output/喂Nora/negative_train/<uuid>.wav
  oww-data/output/喂Nora/negative_test/<uuid>.wav

16 kHz mono WAV, same as positives (data.py:674 requires clip_sr == 16000).
"""
from __future__ import annotations

import json
import os
import random
import sys
import urllib.request
import uuid
from pathlib import Path

API_KEY = os.environ.get("MINIMAX_CN_API_KEY", "")
ENDPOINT = "https://api.minimaxi.com/v1/t2a_v2"
BACKUP = "https://api.minimax.chat/v1/t2a_v2"
MODEL = "speech-2.8-turbo"
OUT_DIR = Path("/home/norty/workspace/voice-lab-data/oww-data/output/喂Nora")

# HYBRID NEGATIVES (per Miles):
# - MiniMax pass: ONLY the 6 near-phonetic traps, using the same 6 Cantonese
#   voices as positives → model rejects near-miss 喂勞拉/露娜/羅拉 in the exact
#   acoustic space positives live in (best discriminator pressure).
# - edge-tts pass (gen_negatives_edge.py): all other generic traps. Parallel.
TRAPS = [
    # near-phonetic to 喂 Nora — must match positive voices
    "喂勞拉。", "喂露娜。", "喂羅拉。", "喂樂拉。", "喂娜娜。", "喂阿娜。",
]

N_TRAIN = 500  # per trap (6 × 500 = 3,000)
N_TEST = 100  # per trap (6 × 100 = 600)

VOICES = [
    "Cantonese_CuteGirl",
    "Cantonese_GentleLady",
    "Cantonese_KindWoman",
    "Cantonese_PlayfulMan",
    "Cantonese_ProfessionalHost（F)",
    "Cantonese_ProfessionalHost（M)",
]


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
            raise RuntimeError(f"TTS failed for text={text}: {last_err}")


def slugify(text: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKC", text)
    keep = "".join(c for c in s if c.isalnum() or c in "._-")
    return keep[:24] or "trap"


def main() -> int:
    if not API_KEY:
        print("MINIMAX_CN_API_KEY not set", file=sys.stderr)
        return 1
    rng = random.Random(20260915)
    # deterministic round-robin: each trap gets its samples spread across voices
    out_train = OUT_DIR / "negative_train"
    out_test = OUT_DIR / "negative_test"
    out_train.mkdir(parents=True, exist_ok=True)
    out_test.mkdir(parents=True, exist_ok=True)

    n_done = 0
    for trap in TRAPS:
        slug = slugify(trap)
        for split, n in ((out_train, N_TRAIN), (out_test, N_TEST)):
            # count only files carrying this trap's prefix (background cuts live
            # in the same dir with uuid-only names — do NOT let them count)
            have = len(list(split.glob(f"{slug}-*.wav")))
            for _ in range(n - have):
                voice = VOICES[rng.randrange(len(VOICES))]
                speed = round(rng.uniform(0.9, 1.1), 2)
                pitch = rng.randint(-2, 2)
                out = split / f"{slug}-{uuid.uuid4().hex[:12]}.wav"
                audio = tts(trap, voice, speed, pitch)
                out.write_bytes(audio)
                n_done += 1
                print(f"ok [{split.name}] {trap} via {voice} -> {out.name}", flush=True)
    print(f"done: {n_done} negative clips -> {OUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
