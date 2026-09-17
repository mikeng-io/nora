#!/usr/bin/env bash
# Downmix BIRD RIR impulses (8ch/48k) to mono 16k for openWakeWord augment pipeline.
# train.py data.py:694 torchaudio.load(random.choice(RIR_paths)) — augment_clips
# expects MONO clips (positive/negative are mono 16k). BIRD fold01 is 8ch FLAC;
# speechbrain reverberate then fails on multi-element direct_index on CUDA.
set -euo pipefail
SRC=~/workspace/voice-lab/oww-data/rir/flatten
DST=~/workspace/voice-lab/oww-data/rir/mono16k
mkdir -p "$DST"
n=0
shopt -s nullglob
for f in "$SRC"/*.flac; do
  base=$(basename "$f" .flac)
  out="$DST/$base.wav"
  if [ -s "$out" ]; then
    continue
  fi
  ffmpeg -v error -i "$f" -ac 1 -ar 16000 -c:a pcm_s16le "$out" -y
  n=$((n+1))
  if [ $((n % 100)) -eq 0 ]; then
    echo "downmixed $n/$(ls "$SRC"/*.flac | wc -l)"
  fi
done
echo "DONE downmixed $n total; DST=$(ls "$DST" | wc -l)"
