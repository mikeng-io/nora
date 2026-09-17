# voice-lab — the wake-word / STT / TTS training lab

Code, manifests and *successful models* for Nora's voice channel live here, in the Nora
repo. The **data does not**: corpora, features, generated audio, virtualenvs and the
openWakeWord source clone live at one fixed host path outside every checkout:

    VOICE_LAB_DATA = ~/workspace/voice-lab-data      (default; scripts read this env var)

`~/workspace/voice-lab` is a compatibility symlink to that directory, so older absolute
paths keep working. Nothing under the data root is ever committed (43 GB; see
`.gitignore` here). Every data directory is regenerable — how, per directory:

| data dir | what | how to rebuild |
|---|---|---|
| `oww-data/output/喂Nora/*` | MiniMax + edge-tts wake/negative clips | `voice-lab/scripts/gen_wake_minimax*.py`, `voice-lab/scripts/gen_negatives_*.py` |
| `oww-data/features/*.npy` | ACAV100M / zh-HK / validation features | HF `davidscripka/openwakeword_features`; `voice-lab/scripts/extract_zhhk_features.py` |
| `oww-data/rir/`, `oww-data/background/` | BIRD RIRs, FMA/FSD50k backgrounds | `voice-lab/scripts/downmix_rir_mono.sh`, see `voice-lab/docs/RESULTS.md` |
| `oww-src/` | openWakeWord clone @ 368c037 + `oww-src-working-tree.patch` | `git clone https://github.com/dscripka/openWakeWord && git apply ../oww-src-working-tree.patch` |
| `scratch/` | feature arrays, seeds, cloned audio from the V5/V6 runs | `voice-lab/experiments/wake-v5/` (reaug*, make_seeds, modal_cosyvoice_yue_gen) |
| `sidecar/recordings/` | Miles's real Discord recordings | not regenerable — back up |

## Layout

    scripts/              team scripts: TTS generation, Modal trainer (`modal_train_wakeword.py`, has the publish gate), probes, gates
    experiments/
      wake-council/       exp1–exp8 (the acceptance definitions: exp5 AUC, exp7 FP/hr)
      wake-v5/            V5/V6 pipeline: reaug (augmentation variants), train_local (upstream auto_train,
                          chunked FP validation), accept (acceptance harness), real_eval3 (Miles's recordings),
                          gate_stt (STT gate), make_seeds + modal_cosyvoice_yue_gen (cloned speakers), assemble_v6
    models/               every candidate worth keeping (ONNX + auto_train history); `deployed-喂Nora.onnx` = what ships
    manifests/            clip lists, STT gate results, seeds, real-burst transcripts — the exact inputs of each model
    docs/                 audit briefs, RESULTS, research (openWakeWord alternatives, CosyVoice Cantonese plan, V6 plan)
    tts-cmp/              MiniMax vs edge-tts comparison (+ small mp3 samples)
    spikes/sidecar/       the original Node receive spike (production version: deploy/voice-sidecar)

The narrative record is `design/voice-channel/03-wake-word-training.md`.

## Rules that cost us before
- Never build the FP-validation tensor whole (3 GB + copies): run heavy jobs under
  `systemd-run --user --scope -p MemoryMax=… -p MemorySwapMax=0`, one at a time.
- Evaluate on raw clips with the peak statistic AND on real recordings; augmented-feature
  metrics alone hid a 43 % false-positive rate.
- A model ships only via `deploy/voice-wake/models/` + its `MODEL.md` manifest.
