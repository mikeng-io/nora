# Audit brief: wake word training produced a dead model

## Task
Deep-dive why the trained openWakeWord model for 「喂 Nora」 is dead (scores 0.0 on everything — including true positive clips), verify/falsify the primary hypothesis (steps=20000 did not propagate, seq1 ran only 2,000 steps), find ALL contributing bugs, recommend the exact fix, and estimate re-training cost on T4. You may run read-only diagnostics locally but MUST NOT modify any file.

## Context
Training an openWakeWord custom wake word via Modal T4. Training completed but:
- Final Model Accuracy: 0.678 | Recall: 0.336 | False Positives per Hour: 482
- Real-audio gate (21.2s recording with 5 real "喂 Nora" utterances): 0 fires
- Known positive training clips score 0.0 (VAD on and off)

## Files (read all)
- /home/<deploy-user>/workspace/voice-lab/oww-src/openwakeword/train.py (auto_train ~line 261; main ~line 794; steps default? search "def auto_train" and "steps=")
- /home/<deploy-user>/workspace/voice-lab/oww-data/喂Nora.yml (source config, steps: 50000)
- /home/<deploy-user>/workspace/voice-lab/scripts/modal_train_wakeword.py (Modal wrapper; cfg["steps"] = STEP_COUNT rewrite; run cmd)
- /tmp/modal-train.log (metrics at ~3456-3458; STEP_COUNT=20000 at line 10; seq markers 2153/3221/3343; tqdm seq1 totals 3428-3452)
- /home/<deploy-user>/workspace/voice-lab/oww-data/output/喂Nora.onnx
- /home/<deploy-user>/workspace/voice-lab/scripts/real_audio_gate.py

## Verified
- Splits: pos_train 13,590 / pos_test 1,710 / neg_train 15,274 / neg_test 2,180 — all 16k mono WAV.
- RIR mono16k (was 8ch, downmixed, re-uploaded — volume cleaned).
- ONNX input [1,16,96] output [1,1] — standard format.
- predict() returns 0.0 for everything.
- 3 training sequences ran (official behavior seq1/10/100).
- batch_n_per_class 50/50 dnn layer 32.

## Deliverable (English, bullets, evidence per claim)
1. Root-cause chain with file:line
2. Confirm/refute steps=2000 vs 20000 hypothesis
3. Every other bug found
4. Exact fix
5. Re-train estimate ($/time on T4)
