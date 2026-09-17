#!/usr/bin/env python3
"""Split a slice of negative_train into negative_test.

train.py's validation path loads BOTH negative_features_test.npy (balanced
X_val) and false_positive_validation_data. With 0 test negatives the balanced
validation set has no '0' class — fix by moving a stratified slice of the
974 local-cut background negatives into negative_test.

Moves (doesn't copy) so disk usage stays flat; keeps uuid names.
"""
from __future__ import annotations

import random
from pathlib import Path

OUT = Path("/home/norty/workspace/voice-lab-data/oww-data/output/喂Nora")
N_TEST = 200

train_dir = OUT / "negative_train"
test_dir = OUT / "negative_test"
test_dir.mkdir(parents=True, exist_ok=True)

files = sorted(train_dir.glob("*.wav"))
rng = random.Random(7)
rng.shuffle(files)
chosen = files[:N_TEST]
for p in chosen:
    dest = test_dir / p.name
    if not dest.exists():
        p.rename(dest)
print(f"moved {len(chosen)} -> negative_test; train now {len(list(train_dir.glob('*.wav')))}, test {len(list(test_dir.glob('*.wav')))}")
