#!/usr/bin/env python3
"""Generate wake-word training positives with MiniMax CN TTS (6 Cantonese voices).

Multi-voice, multi-variant synthetic positives for the 「喂 Nora」 openWakeWord
classifier. Replaces the single-voice edge-tts set with the 6 real Cantonese
voices (all verified available with the sk-cp- Token Plan key, per
tts-cmp/MINIMAX_TOKEN_PLAN_SPEECH_FINDINGS.md §五).

Output: data/wake-minimax/<voice>/<idx>-<variant>.mp3 + meta.json
Layout mirrors data/sepprobe/ so re-evaluation tooling can load it.

Cost: ~300 clips x ~10 chars x 2 chars/char → ~6,000 billed chars ≈ ¥1.2.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

API_KEY = os.environ.get("MINIMAX_CN_API_KEY", "")
ENDPOINT = "https://api.minimaxi.com/v1/t2a_v2"
BACKUP = "https://api.minimax.chat/v1/t2a_v2"
MODEL = os.environ.get("MINIMAX_WAKE_MODEL", "speech-2.8-turbo")
OUT = Path(__file__).resolve().parent.parent / "data" / "wake-minimax"

# The 6 Cantonese voices, verified available (findings doc §五).
VOICES = [
    "Cantonese_CuteGirl",
    "Cantonese_GentleLady",
    "Cantonese_KindWoman",
    "Cantonese_PlayfulMan",
    "Cantonese_ProfessionalHost（F)",
    "Cantonese_ProfessionalHost（M)",
]

# Variants to synthesize. Keep them wake-phrase-forward and Cantonese-true;
# a couple with trailing command tokens (fire-on-onset evaluation).
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
]

REPS = int(os.environ.get("MINIMAX_WAKE_REPS", "5"))


def tts(text: str, voice: str) -> bytes:
    """One TTS call with RPM-aware retry/backoff.

    The Token Plan's 60 RPM ceiling (findings §3) is far tighter than the
    latency would suggest — consecutive calls hit `API 1002 rate limit
    exceeded(RPM)` even at 1.2 s spacing (observed 2026-09-15). On 1002 we
    wait out the window (~60 s) then retry the SAME clip, instead of dying
    and forcing the operator to babysit batches.
    """
    import time
    payload = {
        "model": MODEL,
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": voice, "speed": 1.0, "vol": 1.0, "pitch": 0},
        "audio_setting": {"sample_rate": 32000, "bitrate": 64000, "format": "mp3", "channel": 1},
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
                        # RPM window — wait then retry this same clip.
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
            # Both endpoints failed without a rate-limit retry break.
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
                name = f"{idx:04d}-{variant[:6].strip('。，、')}-{rep}"
                out = vdir / f"{name}.mp3"
                if out.exists() and out.stat().st_size > 100:
                    print(f"skip {out.name}", flush=True)
                else:
                    audio = tts(variant, voice)
                    out.write_bytes(audio)
                    print(f"ok {voice} / {variant} / {rep} -> {out.name} ({len(audio)}B)", flush=True)
                meta.append({
                    "file": str(out.relative_to(OUT)),
                    "text": variant,
                    "voice": voice,
                    "variant": variant,
                    "rep": rep,
                    "label": "positive",
                })
                idx += 1
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"done: {len(meta)} clips -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
