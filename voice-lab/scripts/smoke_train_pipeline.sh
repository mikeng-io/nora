#!/usr/bin/env bash
# Mini smoke of the full train.py pipeline with a tiny dataset.
# Proves the OWW code path works on THIS host before the 12k-clip real run.
set -euo pipefail

SMOKE_DIR=/home/norty/workspace/voice-lab-data/oww-data/smoke
NESTED=$SMOKE_DIR/live/smoke
rm -rf "$SMOKE_DIR"
mkdir -p "$NESTED"/{positive_train,positive_test,negative_train,negative_test}

# copy 60 pos + 20 neg + 5 test pos + 5 test neg (hardlink to save disk)
SRC_POS=/home/norty/workspace/voice-lab-data/oww-data/output/喂Nora
i=0
for f in $(ls "$SRC_POS/positive_train"/*.wav | head -60); do ln "$f" "$NESTED/positive_train/smoke_$i.wav"; i=$((i+1)); done
for f in $(ls "$SRC_POS/negative_train"/*.wav | head -20); do ln "$f" "$NESTED/negative_train/smoke_$i.wav"; i=$((i+1)); done
j=0
for f in $(ls "$SRC_POS/positive_test"/*.wav | head -5); do ln "$f" "$NESTED/positive_test/smoke_$j.wav"; j=$((j+1)); done
for f in $(ls "$SRC_POS/negative_test"/*.wav | head -5); do ln "$f" "$NESTED/negative_test/smoke_$j.wav"; j=$((j+1)); done
echo "smoke set: $(ls "$NESTED"/positive_train | wc -l) pos-train, $(ls "$NESTED"/negative_train | wc -l) neg-train, $(ls "$NESTED"/positive_test | wc -l) pos-test, $(ls "$NESTED"/negative_test | wc -l) neg-test"

# smoke config: same as real but tiny counts + no RIR yet (BIRD not dl'd)
cat > "$SMOKE_DIR/smoke.yml" <<'YAML'
model_name: "smoke"
target_phrase: ["喂 Nora"]
custom_negative_phrases: []
n_samples: 60
n_samples_val: 5
tts_batch_size: 50
augmentation_batch_size: 8
augmentation_rounds: 1
piper_sample_generator_path: "/home/norty/workspace/voice-lab-data/oww-data/piper-sample-generator"
output_dir: "/home/norty/workspace/voice-lab-data/oww-data/smoke/live"
rir_paths: ["/home/norty/workspace/voice-lab-data/oww-data/rir/mono16k"]
background_paths: ["/home/norty/workspace/voice-lab-data/oww-data/background/fma_sample", "/home/norty/workspace/voice-lab-data/oww-data/background/fsd50k_sample"]
background_paths_duplication_rate: [1, 1]
false_positive_validation_data_path: ""
feature_data_files: {}
batch_n_per_class:
  "adversarial_negative": 10
  "positive": 10
model_type: "dnn"
layer_size: 32
steps: 100
max_negative_weight: 150
target_false_positives_per_hour: 0.2
YAML

# run pipeline on the tiny set
cd /home/norty/workspace/voice-lab
echo "=== augment+features ==="
timeout 900 .venv-train/bin/python oww-src/openwakeword/train.py \
  --training_config "$SMOKE_DIR/smoke.yml" \
  --augment_clips 2>&1 | tail -8
echo "AUG_EXIT=$?"
echo "=== train_model ==="
timeout 900 .venv-train/bin/python oww-src/openwakeword/train.py \
  --training_config "$SMOKE_DIR/smoke.yml" \
  --train_model 2>&1 | tail -12
echo "TRAIN_EXIT=$?"
