# Shipped wake model

| field | value |
|---|---|
| file | `喂Nora.onnx` |
| sha256 | efb5c585763e58f20… (`sha256sum` to verify) |
| source | `voice-lab/models/v6_s2-喂Nora.onnx` (V6, seed 2) |
| trained | 2026-09-17, `voice-lab/experiments/wake-v5/train_local.py`, upstream openWakeWord `auto_train`, 50k steps, negative weight 1500, 100 positives/batch |
| data | MiniMax bare 「喂 Nora」 ×3 rounds + 989 CosyVoice2-cloned clips over 278 Common Voice yue speakers; negatives: edge-tts generic/trap/mention, MiniMax traps, command-only, real Common Voice speech, all as full + prefix windows (`voice-lab/manifests/`) |
| metrics | exp5 AUC 0.972 · trap FP 1.3 % · speech-neg FP 0 % · 3.1 frame-FP/hr, 1.7 debounced · Miles's recordings 7/7 wakes, 0 false fires |
| threshold | 0.5 (unchanged) |
| deployed | 2026-09-17 00:03 HKT, trial (Miles) |
| replaces | Sep-16 V3 artefact (speech-presence detector, sha 2a7d283a…) |

Update this table whenever the file changes; the file is tracked in git on purpose.
