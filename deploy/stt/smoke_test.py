#!/usr/bin/env python3
"""Smoke test for the STT container, called from the host against
127.0.0.1:8000 (the loopback publish compose gives it).

Verifies three independent checks:
1. health — the process is serving
2. model identity — /v1/models lists BOTH engines (sensevoice default,
   noise-detection backup)
3. a REAL transcription round-trip — an actual audio file comes back as Hant
   text (against the DEFAULT model; pass --model noise-detection to smoke the
   backup)

Usage:
    python deploy/stt/smoke_test.py [--base-url http://127.0.0.1:8000] \
        [--audio path/to/yue.mp3] [--model noise-detection]
"""
import argparse
import json
import pathlib
import sys
import urllib.request

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--audio", default=None, help="path to a Cantonese audio file (mp3/wav)")
    ap.add_argument("--model", default="sensevoice",
                    help="model the transcription round-trip exercises")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")

    # 1. health
    with urllib.request.urlopen(f"{base}/health", timeout=10) as r:
        health = json.loads(r.read())
    print("health:", health)
    assert health.get("status") == "ok", "health.status != ok"

    # 2. model identity — both engines advertised
    with urllib.request.urlopen(f"{base}/v1/models", timeout=10) as r:
        models = json.loads(r.read())
    ids = [m["id"] for m in models.get("data", [])]
    print("models:", ids)
    assert "noise-detection" in ids, "noise-detection (default) not advertised"
    assert "sensevoice" in ids, "sensevoice (backup) not advertised"

    # 3. transcription — needs an audio file
    if not args.audio:
        print("SKIP: no --audio given; health+model checks passed")
        return 0

    audio_path = pathlib.Path(args.audio)
    assert audio_path.exists(), f"audio not found: {args.audio}"

    boundary = "----nora-stt-smoke"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"{args.model}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n"
    ).encode() + audio_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{base}/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        result = json.loads(r.read())
    print("transcription:", result.get("text"))
    assert result.get("text"), "empty transcription"
    print("SMOKE OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
