#!/usr/bin/env python3
"""Build positive_train / positive_test for train.py from MiniMax clips.

train.py's --augment_clips path globs *.wav under:
  output_dir/<model_name>/positive_train/
  output_dir/<model_name>/positive_test/
We don't run --generate_clips (English piper path), so we place the MiniMax
16k wavs ourselves. Uses HARD LINKS (not copies) — same filesystem, zero
extra disk.

Stratified split: every voice × variant combo appears in both train and test,
so validation sees all acoustic families.
"""
from __future__ import annotations

import random
import re
from collections import defaultdict
from pathlib import Path

SRC = Path("/home/norty/workspace/voice-lab-data/data/wake-minimax-v2")
OUT = Path("/home/norty/workspace/voice-lab-data/oww-data/output/喂Nora")
TEST_FRAC = 0.111  # ~1700 / 15300

train_dir = OUT / "positive_train"
test_dir = OUT / "positive_test"
train_dir.mkdir(parents=True, exist_ok=True)
test_dir.mkdir(parents=True, exist_ok=True)

# existing placement check (resumable-ish: skip already hardlinked)
already = set(p.name for p in train_dir.glob("*.wav")) | set(p.name for p in test_dir.glob("*.wav"))

buckets: dict[tuple[str, str], list[Path]] = defaultdict(list)
for wav in SRC.rglob("*.wav"):
    # path: .../<voice>/<id>-<slug>-<rep>.wav  → strip id prefix and rep suffix
    parts = wav.parts
    voice = parts[-2]
    m = re.match(r"^\d+-(.+?)-\d+$", wav.stem)
    slug = m.group(1) if m else wav.stem
    buckets[(voice, slug)].append(wav)

rng = random.Random(20260915)
n_train = n_test = n_skip = 0
for key, files in buckets.items():
    rng.shuffle(files)
    n_test_i = max(1, round(len(files) * TEST_FRAC))
    for i, f in enumerate(files):
        dest = (test_dir if i < n_test_i else train_dir) / f.name
        if dest.exists():
            n_skip += 1
            continue
        try:
            dest.hardlink_to(f)
        except FileExistsError:
            n_skip += 1
            continue
        if i < n_test_i:
            n_test += 1
        else:
            n_train += 1

print(f"hardlinked: train={n_train} test={n_test} skipped={n_skip}")
print(f"on-disk: positive_train={len(list(train_dir.glob('*.wav')))} positive_test={len(list(test_dir.glob('*.wav')))}")
