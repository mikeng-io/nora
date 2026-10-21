# Audit brief: wake word training produced a dead model

## Task
Attack the claim "the model is dead because steps didn't propagate (seq1 ran 2,000 of 20,000)". Try to falsify it. Also attack the training/setup itself: is 0.678 accuracy / 0.336 recall / 482 FP/hr consistent with a 2,000-step model? Or is something else wrong — data leakage, feature mismatch, ONNX export bug, wrong input normalization, silent config swallow? Also attack our fix plan (re-run at 50,000 steps on T4) — would it actually fix it? What would YOU do differently if given 1 hour and $5? You may run read-only local diagnostics; MUST NOT modify files.

## Context
openWakeWord custom wake word 「喂 Nora」 trained on Modal T4 (torch 2.1.2 CUDA, speechbrain 0.5.14 with a runtime source patch int(rotation_index) on reverberate, RIR mono16k 300, bg 550, pos 13,590/1,710, neg 15,274/2,180, dnn layer 32, batch 50/50, augmentation_rounds 1). Final metrics 0.678 acc / 0.336 recall / 482 FP-hr. Real gate 0 fires, all predicts 0.0.

## Files
- /home/<deploy-user>/workspace/voice-lab/oww-src/openwakeword/train.py
- /home/<deploy-user>/workspace/voice-lab/oww-src/openwakeword/data.py
- /home/<deploy-user>/workspace/voice-lab/oww-data/喂Nora.yml
- /home/<deploy-user>/workspace/voice-lab/scripts/modal_train_wakeword.py
- /tmp/modal-train.log
- /home/<deploy-user>/workspace/voice-lab/oww-data/output/喂Nora.onnx
- /home/<deploy-user>/workspace/voice-lab/scripts/real_audio_gate.py

## Deliverable (English, terse, adversarial)
1. Attack verdict on the steps hypothesis (evidence)
2. Other failure modes you can confirm or strongly suspect
3. What the "right" fix is
4. What you would do differently in 1h/$5
