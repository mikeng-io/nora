# Audit brief: wake word training produced a dead model

## Task
Technically deep-dive why the trained openWakeWord model for 「喂 Nora」 is dead (scores 0.0 on everything), verify the primary hypothesis (steps did not propagate), find ALL contributing bugs, and recommend the concrete fix. You MAY run read-only diagnostics locally (python, grep, onnx inspection) but MUST NOT modify any file.

## Shared context
Training an openWakeWord custom wake word on Modal T4. Training completed but metrics are bad:
- Final Model Accuracy: 0.678 | Recall: 0.336 | False Positives per Hour: 482
- Real-audio gate on Miles's 21.2s recording (contains 5 real wake utterances): 0 fires
- Known positive training clips score 0.0

Strong suspicion: `steps` config did NOT propagate — log shows **seq1 tqdm total = 2,000** while MODAL_WAKE_STEPS=20000. Verify by reading train.py's config flow: how `config["steps"]` reaches `auto_train(steps=...)`; check whether the Modal-rewritten YAML actually changed `steps`; check whether train.py has a default of 2000 that swallowed our value.

## Files (read everything relevant)
- /home/<deploy-user>/workspace/voice-lab/oww-src/openwakeword/train.py — official training script (auto_train at ~line 261; main at ~line 794)
- /home/<deploy-user>/workspace/voice-lab/oww-src/openwakeword/data.py — augmentation + clip gen
- /home/<deploy-user>/workspace/voice-lab/oww-data/喂Nora.yml — source config (steps: 50000)
- /home/<deploy-user>/workspace/voice-lab/scripts/modal_train_wakeword.py — Modal wrapper (cfg["steps"] = STEP_COUNT rewrite at ~line 140; run cmd at ~line 210)
- /tmp/modal-train.log — full training log (metrics at ~line 3456-3458; STEP_COUNT=20000 at line 10; "Starting training sequence 1..." at line 2153; seq1 tqdm totals near 3428-3452)
- /home/<deploy-user>/workspace/voice-lab/oww-data/output/喂Nora.onnx — trained model (206KB)
- /home/<deploy-user>/workspace/voice-lab/scripts/real_audio_gate.py — gate harness (uses Model(wakeword_models=[onnx], inference_framework="onnx", enable_speex_noise_suppression=True, vad_threshold=0.5) with 512-sample windows)

## Verified facts
- Data split: positive_train 13,590 / positive_test 1,710 / negative_train 15,274 / negative_test 2,180 — all 16k mono WAV.
- RIR downmixed to mono16k (BIRD fold01 was 8ch — already fixed and re-run).
- ONNX: input [1,16,96], output [1,1] — looks like standard openwakeword feature format.
- predict() on both real positives and negatives returns 喂Nora=0.0 (VAD on and off).
- Training ran 3 sequences (official auto_train behavior: seq1 = steps, seq2 = steps/10, seq3 = steps/100).
- batch_n_per_class {adversarial_negative: 50, positive: 50}; model_type dnn; layer_size 32.

## Deliverable
Report with: (1) root-cause chain with file:line evidence, (2) confirm/refute the 2000-vs-20000 hypothesis, (3) every other bug you find (config keys, ONNX export, metrics, data issues, any "model trained on garbage" indicators), (4) recommended exact fix, (5) if the fix means re-training, estimate cost/time on T4. English, concise bullets, no file modifications.
