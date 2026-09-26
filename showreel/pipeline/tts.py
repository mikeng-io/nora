#!/usr/bin/env python3
"""Synthesize the showreel's voice-over with MiniMax (T2A v2, CN endpoint).

    MINIMAX_CN_API_KEY=... python3 pipeline/tts.py --lang en [--audition DIR --voice V]

Reads the lines from src/copy/<lang>.json (the one place the words live) and
writes, per line, audio/vo/<lang>/<id>.mp3 plus audio/vo/<lang>/manifest.json:
text, voice, model, settings, measured duration, sha256 and MiniMax's own
subtitle timings. Synthesis is stochastic, so a kept take is committed and
never regenerated silently; rerunning overwrites on purpose.

Same wire contract as nora/infra/tts.py: an HTTP 200 is not yet a success,
`base_resp.status_code` must be 0 and `data.audio` must be non-empty hex.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOSTS = ("https://api.minimaxi.com", "https://api.minimax.chat")
BOOST = {"en": "English", "yue": "Chinese,Yue"}  # never "auto" for Yue (tts.py)


def post(path: str, body: dict, key: str) -> dict:
    last: Exception | None = None
    for host in HOSTS:
        req = urllib.request.Request(
            host + path, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except OSError as exc:        # transport only: a refusal would repeat on the other host
            last = exc
    raise RuntimeError(f"both hosts unreachable: {last}")


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return round(float(out.stdout.strip()), 3)


def synth(text: str, lang: str, voice: str, model: str, speed: float, emotion: str | None,
          pitch: int, key: str) -> tuple[bytes, dict]:
    body = {
        "model": model, "text": text, "stream": False,
        "voice_setting": {"voice_id": voice, "speed": speed, "vol": 1.0, "pitch": pitch,
                          **({"emotion": emotion} if emotion else {})},
        "audio_setting": {"sample_rate": 44100, "bitrate": 256000, "format": "mp3", "channel": 1},
        "language_boost": BOOST[lang],
        "subtitle_enable": True,
    }
    for attempt in range(6):
        payload = post("/v1/t2a_v2", body, key)
        base = payload.get("base_resp") or {}
        if base.get("status_code") == 1002:          # RPM window: wait it out, same clip
            time.sleep(20 * (attempt + 1)); continue
        if base.get("status_code", 0) != 0:
            raise RuntimeError(f"refused: {base}")
        data = payload.get("data") or {}
        audio = bytes.fromhex(data.get("audio") or "")
        if not audio:
            raise RuntimeError("empty audio")
        subs = None
        if data.get("subtitle_file"):
            try:
                with urllib.request.urlopen(data["subtitle_file"], timeout=30) as r:
                    subs = json.loads(r.read())
            except (OSError, ValueError):
                subs = None
        return audio, {"extra": payload.get("extra_info"), "subtitles": subs}
    raise RuntimeError("rate limited six times")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", required=True, choices=sorted(BOOST))
    ap.add_argument("--voice", required=True)
    ap.add_argument("--model", default="speech-2.8-hd")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--pitch", type=int, default=0)
    ap.add_argument("--emotion", default=None)
    ap.add_argument("--only", nargs="*", help="line ids to (re)make; default all")
    ap.add_argument("--audition", type=Path, help="write takes here instead of audio/vo/<lang>")
    a = ap.parse_args()
    key = os.environ.get("MINIMAX_CN_API_KEY", "")
    if not key:
        print("MINIMAX_CN_API_KEY is not set", file=sys.stderr)
        return 2
    copy = json.loads((ROOT / "src" / "copy" / f"{a.lang}.json").read_text())
    out = a.audition or (ROOT / "audio" / "vo" / a.lang)
    out.mkdir(parents=True, exist_ok=True)
    man_path = out / "manifest.json"
    manifest = json.loads(man_path.read_text()) if man_path.exists() else {"lang": a.lang, "lines": {}}
    for line in copy["vo"]:
        if a.only and line["id"] not in a.only:
            continue
        audio, meta = synth(line.get("say", line["text"]), a.lang, a.voice, a.model, a.speed, a.emotion, a.pitch, key)
        mp3 = out / f"{line['id']}.mp3"
        mp3.write_bytes(audio)
        manifest["lines"][line["id"]] = {
            "text": line["text"], "said": line.get("say", line["text"]), "voice": a.voice, "model": a.model, "speed": a.speed,
            "pitch": a.pitch, "emotion": a.emotion, "language_boost": BOOST[a.lang],
            "duration": duration(mp3), "sha256": hashlib.sha256(audio).hexdigest(),
            "subtitles": meta["subtitles"], "usage": meta["extra"],
            "made": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        print(f"{line['id']}\t{manifest['lines'][line['id']]['duration']}s\t{line['text']}")
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
