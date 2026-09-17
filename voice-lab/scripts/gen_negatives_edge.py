#!/usr/bin/env python3
"""Generate adversarial negative wake clips with edge-tts (free, no RPM cap).

Replaces the MiniMax trap generation (60 RPM bottleneck). Microsoft edge-tts
has 3 native zh-HK voices; output converted to 16kHz mono WAV via ffmpeg
(train.py data.py:674 requires clip_sr == 16000).

Layout mirrors MiniMax version so train.py --augment_clips finds them:
  oww-data/output/喂Nora/negative_train/<slug>-<uuid>.wav
  oww-data/output/喂Nora/negative_test/<slug>-<uuid>.wav
"""
from __future__ import annotations

import asyncio
import random
import subprocess
import sys
import unicodedata
import uuid
from pathlib import Path

import edge_tts

OUT_DIR = Path("/home/norty/workspace/voice-lab-data/oww-data/output/喂Nora")
N_TRAIN = 500  # per trap — train.py:706 requires ~n_samples (15,300) negatives total
N_TEST = 60  # per trap — train.py:729 requires ~n_samples_val (1,700)
VOICES = ["zh-HK-HiuGaaiNeural", "zh-HK-HiuMaanNeural", "zh-HK-WanLungNeural"]
CONCURRENCY = 8

TRAPS = [
    # near-phonetic to 喂 Nora
    "喂勞拉。", "喂露娜。", "喂羅拉。", "喂樂拉。", "喂娜娜。", "喂阿娜。",
    # partial phrase
    "喂。", "Nora。", "喂你。", "喂啊。", "喂喂。",
    # Nora in natural sentences without wake intent
    "Nora 你今日忙唔忙呀？", "Nora 幫我睇下嘢。", "你係咪 Nora 呀？",
    "Nora Nora 我鍾意你。", "聽講 Nora 好叻。", "我唔係 Nora。",
    "Nora 而家得唔得閒？", "喂，你係咪 Nora？", "喂 Nora 啦。喂 Nora 啦。",
    "喂呀 Nora 你喺度。", "唔該 Nora。", "拜拜 Nora。",
    # generic Cantonese speech, no wake
    "今日天氣幾好。", "聽日去食飯好冇？", "我返屋企喇。",
    "而家幾點？", "今晚打機呀。", "我好攰呀。",
]


def slugify(text: str) -> str:
    s = unicodedata.normalize("NFKC", text)
    keep = "".join(c for c in s if c.isalnum() or c in "._-")
    return keep[:24] or "trap"


def convert_mp3_to_wav(mp3: Path, wav: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
         "-ar", "16000", "-ac", "1", str(wav)],
        check=True, capture_output=True,
    )


async def gen_one(sem: asyncio.Semaphore, trap: str, voice: str, rate: str, pitch: str, out: Path, mp3: Path) -> bool:
    async with sem:
        try:
            tts = edge_tts.Communicate(trap, voice, rate=rate, pitch=pitch)
            await tts.save(str(mp3))
            convert_mp3_to_wav(mp3, out)
            mp3.unlink(missing_ok=True)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {trap} {voice}: {e}", flush=True)
            return False


async def main() -> int:
    out_train = OUT_DIR / "negative_train"
    out_test = OUT_DIR / "negative_test"
    out_train.mkdir(parents=True, exist_ok=True)
    out_test.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DIR / ".edge-tmp"
    tmp.mkdir(exist_ok=True)

    rng = random.Random(20260915)
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = []
    n_done = 0

    for trap in TRAPS:
        slug = slugify(trap)
        for split_dir, n in ((out_train, N_TRAIN), (out_test, N_TEST)):
            have = len(list(split_dir.glob(f"{slug}-*.wav"))) + len(list(tmp.glob(f"{slug}-*.wav")))
            for _ in range(max(0, n - have)):
                voice = VOICES[rng.randrange(len(VOICES))]
                rate = f"{rng.randint(-15, 15):+d}%"
                pitch = f"{rng.randint(-30, 30):+d}Hz"
                uid = uuid.uuid4().hex[:12]
                out = split_dir / f"{slug}-{uid}.wav"
                mp3 = tmp / f"{slug}-{uid}.mp3"
                tasks.append(gen_one(sem, trap, voice, rate, pitch, out, mp3))
                n_done += 1

    results = await asyncio.gather(*tasks)
    ok = sum(results)
    print(f"done: {ok}/{n_done} negative clips via edge-tts -> {OUT_DIR}", flush=True)
    return 0 if ok == n_done else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
