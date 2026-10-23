#!/usr/bin/env python3
"""Mix music, sound design and one language's voice-over into the final track.

    uv run --no-project --with numpy --with scipy --with pyloudnorm python pipeline/mix.py en yue

For each language: every VO take is placed so its MEASURED speech onset
(src/data/vo-<lang>.json, from pipeline/align.py) lands on its cue in
src/copy/<lang>.json, which is also when the captions reveal it. The music
ducks under the voice, the sound design a little less. The result is
normalised to -14 LUFS integrated with a -1.0 dBTP ceiling (the level the
social platforms play at), trimmed to the picture, and written to
audio/mix/<lang>.wav. Prints the measured loudness and peak so the claim is
read off the file, not assumed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
from scipy import signal
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parent.parent
SR = 48000
TL = json.loads((ROOT / "src" / "data" / "timeline.json").read_text())
N = int(TL["duration"] * SR)
TARGET_LUFS = -14.0
CEILING_DBTP = -1.0


def load_wav(path: Path) -> np.ndarray:
    sr, x = wavfile.read(path)
    if sr != SR:
        raise ValueError(f"{path} is {sr} Hz")
    x = x.astype(np.float64)
    return x[:N] if len(x) >= N else np.pad(x, ((0, N - len(x)), (0, 0)))


def load_vo(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f64le", "-ac", "1",
                          "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float64)


def voice_chain(x: np.ndarray) -> np.ndarray:
    """High-pass the rumble, a little presence, a gentle compressor, a small room."""
    x = signal.sosfilt(signal.butter(2, 90, btype="high", fs=SR, output="sos"), x)
    pres = signal.sosfilt(signal.butter(2, [2500, 6000], btype="band", fs=SR, output="sos"), x)
    x = x + 0.25 * pres
    env = np.sqrt(signal.sosfilt(signal.butter(1, 12, fs=SR, output="sos"), x ** 2) + 1e-12)
    thr = 10 ** (-24 / 20)
    gain = np.where(env > thr, (env / thr) ** (1 / 2.5 - 1), 1.0)
    x = x * gain
    n = int(0.35 * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(7)
    ir = rng.standard_normal(n) * np.exp(-t * 14)
    ir[: int(0.008 * SR)] = 0
    wet = signal.fftconvolve(x, ir / np.sqrt((ir ** 2).sum()))[: len(x)]
    return x + 0.07 * wet


def mix(lang: str) -> None:
    music = load_wav(ROOT / "audio" / "stems" / "music.wav")
    sfx = load_wav(ROOT / "audio" / "stems" / "sfx.wav")
    copy = json.loads((ROOT / "src" / "copy" / f"{lang}.json").read_text())
    vo_meta = json.loads((ROOT / "src" / "data" / f"vo-{lang}.json").read_text())["lines"]
    voice = np.zeros(N)
    duck_key = np.zeros(N)
    for line in copy["vo"]:
        m = vo_meta[line["id"]]
        clip = voice_chain(load_vo(ROOT / m["file"]))
        at = int(round((line["cue"] - m["speechStart"]) * SR))
        j = min(N, at + len(clip))
        voice[at:j] += clip[: j - at]
        s0 = int((line["cue"] - 0.05) * SR)
        s1 = int((line["cue"] - m["speechStart"] + m["speechEnd"] + 0.05) * SR)
        duck_key[max(0, s0):min(N, s1)] = 1.0
        print(f"  {lang} {line['id']} speech {line['cue']:.2f}-"
              f"{line['cue'] - m['speechStart'] + m['speechEnd']:.2f}s  {line['text']}")
    # voice level: -18 LUFS short-term-ish before the master, so it rides above the bed
    meter = pyln.Meter(SR)
    vl = meter.integrated_loudness(np.stack([voice, voice], axis=1))
    voice *= 10 ** ((-17.0 - vl) / 20)
    # ducking: 80 ms attack, 350 ms release on the key
    att, rel = np.exp(-1 / (0.08 * SR)), np.exp(-1 / (0.35 * SR))
    env = np.zeros(N)
    e = 0.0
    for i in range(0, N, 64):
        k = duck_key[i]
        c = att if k > e else rel
        c = c ** 64
        e = k + (e - k) * c
        env[i:i + 64] = e
    music_g = 10 ** (-7.5 * env / 20)
    sfx_g = 10 ** (-3.0 * env / 20)
    bed = music * 10 ** (-5.0 / 20) * music_g[:, None] + sfx * 10 ** (-7.0 / 20) * sfx_g[:, None]
    out = bed + np.stack([voice, voice], axis=1)
    # loudness to target, then a peak ceiling with 4x oversampled true peak
    lufs = meter.integrated_loudness(out)
    out *= 10 ** ((TARGET_LUFS - lufs) / 20)
    ceiling = 10 ** (CEILING_DBTP / 20)
    for _ in range(4):
        tp = np.abs(signal.resample_poly(out, 4, 1, axis=0)).max()
        if tp <= ceiling:
            break
        # soft-knee limit only the overs: tanh above 70% of the ceiling
        knee = 0.7 * ceiling
        a = np.abs(out)
        over = a > knee
        out[over] = np.sign(out[over]) * (knee + (ceiling - knee) * np.tanh((a[over] - knee) / (ceiling - knee)))
    # a 25 ms fade at both ends so the file never clicks
    f = int(0.025 * SR)
    out[:f] *= np.linspace(0, 1, f)[:, None]
    out[-f:] *= np.linspace(1, 0, f)[:, None]
    dst = ROOT / "audio" / "mix" / f"{lang}.wav"
    dst.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(out * 32767, -32768, 32767).astype(np.int16)
    wavfile.write(dst, SR, pcm)
    final = pcm.astype(np.float64) / 32768
    tp = np.abs(signal.resample_poly(final, 4, 1, axis=0)).max()
    print(f"{dst.relative_to(ROOT)}: {meter.integrated_loudness(final):.1f} LUFS, "
          f"true peak {20 * np.log10(tp):.1f} dBTP, {len(final) / SR:.2f}s")


if __name__ == "__main__":
    for lang in sys.argv[1:] or ["en", "yue"]:
        mix(lang)
