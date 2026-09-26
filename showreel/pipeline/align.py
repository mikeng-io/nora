#!/usr/bin/env python3
"""Read the voice-over back and time it: the measure step for the VO.

    uvx --with mlx-whisper --with numpy python pipeline/align.py en yue

For every take in audio/vo/<lang>/ this
  1. transcribes it and scores it against the script: Whisper (large-v3-turbo)
     and word errors for English; Nora's own Cantonese STT on norty
     (pipeline/nora_stt.sh) and character errors for Cantonese, because
     Whisper rewrites spoken Cantonese as written Mandarin. A take
     whose error count is not 0 FAILS the run (exit 1): a caption must never
     show words the voice does not say.
  2. measures where the speech actually starts and ends (RMS above -40 dBFS),
     so placement is on the voice, not on the file's padding.
  3. times each caption token: Whisper's word timestamps for English (mapped
     one-to-one onto the script's words), and for Cantonese the phrase spans
     between punctuation spread over the measured speech.
Writes src/data/vo-<lang>.json, which the compositions and the mix both read.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import mlx_whisper
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODEL = "mlx-community/whisper-large-v3-turbo"
SR = 16000


def pcm(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1",
                          "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def speech_span(x: np.ndarray) -> tuple[float, float]:
    hop = SR // 100
    frames = x[: len(x) // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    on = np.flatnonzero(db > -40)
    return (round(on[0] * hop / SR, 3), round((on[-1] + 1) * hop / SR, 3)) if len(on) else (0.0, 0.0)


def edits(a: list[str], b: list[str]) -> int:
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
    return d[-1]


def tokens_en(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", "", s.lower().replace("’", "'")).split()


def chars_yue(s: str) -> list[str]:
    s = s.lower()
    return [c for c in re.sub(r"[^\w]", "", s)]


def main(langs: list[str]) -> int:
    failed = 0
    for lang in langs:
        copy = json.loads((ROOT / "src" / "copy" / f"{lang}.json").read_text())
        manifest = json.loads((ROOT / "audio" / "vo" / lang / "manifest.json").read_text())
        out = {"lang": lang, "model": MODEL, "lines": {}}
        for line in copy["vo"]:
            mp3 = ROOT / "audio" / "vo" / lang / f"{line['id']}.mp3"
            start, end = speech_span(pcm(mp3))
            r = mlx_whisper.transcribe(str(mp3), path_or_hf_repo=MODEL,
                                       language="en" if lang == "en" else "yue",
                                       word_timestamps=True)
            heard = r["text"].strip()
            if lang == "yue":   # Whisper writes Cantonese as Mandarin; Nora's own ear does not
                heard = subprocess.run([str(ROOT / "pipeline" / "nora_stt.sh")], stdin=mp3.open("rb"),
                                       capture_output=True, text=True, check=True).stdout.strip()
            if lang == "en":
                ref, hyp = tokens_en(line["text"]), tokens_en(heard)
            else:
                ref, hyp = chars_yue(line["text"]), chars_yue(heard)
            err = edits(ref, hyp)
            words = [w for s in r["segments"] for w in s.get("words", [])]
            script_tokens = line["text"].split() if lang == "en" else [
                p for p in re.split(r"(?<=[，。？！…])", line["text"]) if p.strip()]
            if lang == "en" and len(words) == len(script_tokens):
                timed = [{"t": tok, "start": round(w["start"], 3), "end": round(w["end"], 3)}
                         for tok, w in zip(script_tokens, words)]
            else:   # phrase spans spread over the measured speech, by length
                weights = np.array([max(1, len(re.sub(r"[^\w]", "", p))) for p in script_tokens], float)
                edges = start + (end - start) * np.concatenate([[0], np.cumsum(weights) / weights.sum()])
                timed = [{"t": p, "start": round(float(edges[i]), 3), "end": round(float(edges[i + 1]), 3)}
                         for i, p in enumerate(script_tokens)]
            verdict = "PASS" if err == 0 else "FAIL"
            failed += err != 0
            out["lines"][line["id"]] = {
                "text": line["text"], "file": f"audio/vo/{lang}/{line['id']}.mp3",
                "sha256": manifest["lines"][line["id"]]["sha256"],
                "duration": manifest["lines"][line["id"]]["duration"],
                "speechStart": start, "speechEnd": end, "heard": heard,
                "errors": err, "tokens": len(ref), "verdict": verdict, "timed": timed,
            }
            print(f"{lang} {line['id']} {verdict} err={err}/{len(ref)} speech={start}-{end}s heard={heard!r}")
        (ROOT / "src" / "data" / f"vo-{lang}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["en", "yue"]))
