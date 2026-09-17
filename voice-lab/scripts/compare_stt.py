#!/usr/bin/env python3
"""Compare 4 STT engines on the same audio file.

Usage:
    .venv/bin/python scripts/compare_stt.py <audio.wav> [--skip-cloud]

Engines (all on the same file):
    1. SenseVoiceSmall  (local, funasr)     — current production model
    2. Whisper large-v3  (local, faster-whisper)
    3. Qwen3-ASR-1.7B    (local, funasr + qwen-asr)
    4. MiniMax asr-1.0   (cloud, needs MINIMAX_CN_API_KEY env)

Prints each engine's transcript + timing, then a compact summary.
Audio is converted to 16kHz mono for Whisper-compatible engines;
local engines get the file as-is. MiniMax needs MP3 (auto-converts).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

DEFAULT_AUDIO = Path("sidecar/recordings/live-miles-repaired.wav")


def timing(fn):
    t0 = time.time()
    result = fn()
    return result, time.time() - t0


def run_sensevoice(audio: Path) -> str:
    from funasr import AutoModel

    model = AutoModel(model="iic/SenseVoiceSmall", disable_update=True)
    res = model.generate(input=str(audio), language="yue", use_itn=False, batch_size_s=60)
    return res[0]["text"] if res else "(no result)"


def run_whisper(audio: Path, tmp: Path) -> str:
    from faster_whisper import WhisperModel

    # faster-whisper needs a known sample rate; convert to 16k mono first.
    wav16 = tmp / f"{audio.stem}_16k.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio), "-ar", "16000", "-ac", "1", str(wav16)],
        check=True, capture_output=True,
    )
    model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        str(wav16), language=None, beam_size=5, vad_filter=True,
    )
    return " ".join(s.text for s in segments)


def run_qwen(audio: Path) -> str:
    from funasr import AutoModel

    model = AutoModel(
        model="Qwen/Qwen3-ASR-1.7B", trust_remote_code=True,
        remote_code="./model.py", device="cpu", dtype="int8",
    )
    res = model.generate(input=str(audio), cache={})
    return res[0].get("text", str(res[0])) if res else "(no result)"


def run_minimax(audio: Path, tmp: Path, api_key: str) -> str:
    mp3 = tmp / f"{audio.stem}.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio), "-ar", "16000", "-ac", "1", str(mp3)],
        check=True, capture_output=True,
    )
    payload = mp3.read_bytes()
    boundary = uuid.uuid4().hex
    body = b""

    def part(h: bytes, d: bytes) -> bytes:
        return h + b"\r\n" + d + b"\r\n"

    body += part(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n'.encode(), b"asr-1.0")
    body += part(f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n'.encode(), b"yue")
    body += part(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.mp3"\r\nContent-Type: audio/mpeg\r\n'.encode(),
        payload,
    )
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        "https://api.minimaxi.com/v1/speech_to_text", data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            out = json.load(resp)
    except urllib.error.HTTPError as e:
        return f"(HTTP {e.code}: {e.read().decode()[:200]})"
    except Exception as e:  # noqa: BLE001
        return f"(FAIL {type(e).__name__}: {e})"

    result = out.get("result", out)
    if isinstance(result, list):
        return " ".join(str(r.get("text", r)) for r in result)
    return str(result)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    audio = Path(args[0]) if args else DEFAULT_AUDIO
    skip_cloud = "--skip-cloud" in sys.argv
    if not audio.exists():
        print(f"audio not found: {audio}", file=sys.stderr)
        return 1

    tmp = Path("/tmp/stt-compare")
    tmp.mkdir(exist_ok=True)
    results: dict[str, tuple[str, float]] = {}

    print(f"=== STT compare on {audio} ===\n", flush=True)

    engines = [
        ("SenseVoiceSmall", lambda: run_sensevoice(audio), False),
        ("Whisper large-v3", lambda: run_whisper(audio, tmp), False),
        ("Qwen3-ASR-1.7B", lambda: run_qwen(audio), False),
        ("MiniMax asr-1.0", lambda: run_minimax(audio, tmp, api_key), True),
    ]
    api_key = os.environ.get("MINIMAX_CN_API_KEY", "")

    for name, fn, needs_cloud in engines:
        if needs_cloud and (skip_cloud or not api_key):
            print(f"[{name}] skipped (cloud or no MINIMAX_CN_API_KEY)", flush=True)
            continue
        print(f"[{name}] running...", flush=True)
        text, elapsed = timing(fn)
        results[name] = (text, elapsed)
        print(f"[{name}] ({elapsed:.1f}s)\n  {text}\n", flush=True)

    print("=== SUMMARY ===", flush=True)
    for name, (text, elapsed) in results.items():
        print(f"- {name}: {elapsed:.1f}s", flush=True)
        print(f"    {text}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
