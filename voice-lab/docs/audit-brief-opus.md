# Attack audit: wake word training — is the "re-train with real negatives" fix correct?

You are an adversarial reviewer. Given everything below, ATTACK the current diagnosis and fix plan. Try to falsify it. Find what everyone missed. Verify with real evidence where possible (read files, run read-only commands). Do NOT modify any files.

## Current state of knowledge (3 auditors so far)

**Symptom:** trained openWakeWord model 「喂Nora」 (20k steps on Modal T4) evaluated on real audio gave 0.0 scores everywhere → initially thought dead.

**Auditor findings (verified):**
1. Harness bug: gate fed float32 [-1,1] PCM; openWakeWord streaming path does astype(int16) internally → truncation to digital silence → 0.0. With int16 input the model DOES respond: positives peak 0.71-0.75, negatives peak 0.001-0.13. Real 21.2s gate: 77 window-fires ≈ 10 events (5 real wake + 2 traps = 7 expected → ~3 extra ghost events under music).
2. Training quality issues: `feature_data_files: {}` → no real-world background negatives (official uses ACAV100M features); `best_val_fp` never updated in train.py → negative weights double unconditionally to 6000; argparse `default="False"` string truthiness → tflite conversion always runs → onnx_tf ModuleNotFoundError after training → output tar never bundled (ONNX survived only because it hit volume first); steps YAML 50000 vs wrapper 20000 mismatch.
3. Metrics (acc 0.678, recall 0.336, FP/hr 482) told the truth: model weak, not dead.

**Current fix plan:** (a) fix harness (int16, chunk 1280); (b) add real negatives (ACAV100M features ~17GB or subsample) to feature_data_files + batch_n_per_class 50/25/25; (c) patch best_val_fp; (d) pick one steps value (50k); (e) fix argparse defaults; (f) rerun on T4 (~$0.5).

## Your attack targets

1. Is "missing real negatives" actually the PRIMARY model-quality problem? Or is the model actually good enough for production right now (ghost events might be the music, not the model)? Estimate real-world FPR from the gate data if you can.
2. Would adding ACAV100M features (a 17GB download) actually help, or would it slow convergence / need feature-extractor parity? Is there a cheaper equivalent (our 1,200 local background clips)?
3. Is rerunning with more steps (50k) the right move, or is the bigger lever the negative weight schedule (max_negative_weight 1000→lower)?
4. Anything structurally wrong with openWakeWord's auto_train for this task (batch<128 gradient accumulation bug Codex flagged; val metrics on final batch only)?
5. What would you do completely differently (fresh eyes)?

## Files
- /home/<deploy-user>/workspace/voice-lab/oww-src/openwakeword/train.py (auto_train ~261, best_val_fp at 37/291/311, train_model ~434-521, export_model 419)
- /home/<deploy-user>/workspace/voice-lab/oww-src/openwakeword/data.py
- /home/<deploy-user>/workspace/voice-lab/oww-data/喂Nora.yml
- /home/<deploy-user>/workspace/voice-lab/scripts/modal_train_wakeword.py
- /tmp/modal-train.log (metrics line 3456-3458)
- /home/<deploy-user>/workspace/voice-lab/scripts/real_audio_gate.py, gate_int16.py, discrimination_check.py, verify_truncation.py
- /home/<deploy-user>/workspace/voice-lab/oww-data/output/喂Nora.onnx
- /home/<deploy-user>/workspace/voice-lab/oww-data/features/validation_set_features.npy (481345,96)
- Python: /home/<deploy-user>/workspace/voice-lab/.venv-train/bin/python (torch/onnx/onnxruntime/numpy)

## Deliverable
Terse English bullets, evidence-first, verdict on each attack target, then your own recommendation ranked by impact/cost. No file modifications. If you cannot verify, say UNVERIFIED.
