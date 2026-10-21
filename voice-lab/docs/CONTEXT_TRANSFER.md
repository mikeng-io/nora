# Wake Word V4 — Session Transfer (2026-09-16)

**Transfer to:** MacBook Claude Code session
**From:** Norty (norty host) — full investigation + partial fix, handoff for continued work
**Repos:**
- `nora` (design docs): branch `feat/wake-v4-postmortem` → https://github.com/mikeng-io/nora/tree/feat/wake-v4-postmortem (design doc: `design/voice-channel/03-wake-word-training.md` — has full post-mortem)
- `voice-lab` (lab): **NEW private repo** `opsuser-dev/voice-lab` → clone + 6 commits:
  - `voice-lab/scripts/modal_train_wakeword.py` — publish gate implemented
  - `wake-council-experiments/exp1-8 + lib_score.py` — Opus eval suite
  - `oww-src-working-tree.patch` — 194-line diff of openwakeword fixes

---

## 1. SITUATION

Nora wake word「喂 Nora」production misses. **Root cause (3-agent council + verified):**
deployed `喂Nora.onnx` is a **speech-presence detector, not a wake-word discriminator**.
Evidence: train log `Final Model Accuracy 0.6784 / Recall 0.3357 / FP-per-hour 482.12`
(target 0.2 — off 2400×); ONNX was saved, **then** train.py crashed on `onnx_tf`
conversion — the failure was masked as a packaging error. Bad artifact shipped.

**Contamination** (bigger than council estimated): `negative_train/` contains
**6,500/15,274** clips with "Nora" in filename (13 edge-tts phrase patterns × 500,
incl. literal wake phrase 「喂Nora啦喂Nora啦」 mislabelled negative); `negative_test/`
has 780/2,180. With `max_negative_weight: 150`, negative weight ratio ~71:1 against
13,590 positives — optimiser collapsed to speech-presence detection.

**Lab tooling bug**: `real_audio_gate.py` fed float32 but openwakeword `utils.py:194`
requires **int16** — all pre-council real-audio scores were invalid.

---

## 2. WHAT WAS DONE

**A. Publish gate** — `voice-lab/scripts/modal_train_wakeword.py`:
- train.py output captured to `train-run.log` (was lost on stdout)
- `_parse_final_metrics` extracts Final Model block → `_publish_gate` blocks
  publish unless recall ≥ 0.85 AND fp/hr ≤ 1.0 (env overridable)
- FAILED artifacts renamed `FAILED-recall{X}-fphr{Y}.onnx` — never canonical
- full train log bundled (bug fixed mid-session: was overwritten by note)
- **Verified against real Sep-15 log: BLOCKED**

**B. V4 retrain config**:
- negative_train/test exclude ALL "Nora"-containing clips (files stay on disk,
  only tar excludes — reversible)
- steps 20k → 50k (env default 20000 was eating yml's 50000)
- RIR subsample 300 → 1000
- augmentation_rounds 1 → 3

**C. V4 run (Sep-16 17:56)** — Modal T4, `ap-PwrqteweLqiCPMUM4iNr7H`:

```
METRICS: Accuracy 0.767 / Recall 0.619 / FP-per-hour 245.4
GATE: BLOCKED publish (FAILED-recall0.619-fphr245.4)
```

**Gate proved its value** (old pipeline would have shipped this). Contamination
removal helped (recall 0.336→0.619, FP 482→245) but NOT enough.

---

## 3. NEXT-STEP CANDIDATES (V4 still failing — investigate in order)

1. **Check `validation_set_features.npy` contamination** (481,345 frames) — is the FP
   counter itself polluted with wake phrase audio? If yes, FP/hr 245 is not trustworthy
   and the model may be better than it looks. → `voice-lab/oww-data/features/validation_set_features.npy`
2. **Weight-schedule death spiral** — openwakeword `auto_train` (train.py:261-323):
   sequence 2/3 double `max_negative_weight` when FP > target; with 71:1 baseline the
   weights keep doubling → model stays negative-biased, recall never reaches 0.85.
   Inspect `negative_weight_schedule` behavior + consider capping.
3. **oww-src uncommitted fixes** — `oww-src-working-tree.patch` has per-batch backward
   (replacing broken gradient accumulation) + validation-history guards. Apply and re-run;
   may or may not be correct.
4. **RIR 1000 + rounds 3 too aggressive** — augmentation may blur the positive signal.
   If recall still low after 1-3, test rounds 1 with RIR 300 as control.

**Acceptance for deploy:** AUC > 0.97 (exp5_auc.py), FP/hr < 1.0 (exp7_fphr.py),
trap negative FP < 1% at 0.5, positive recall > 95% at 0.5, real Discord "喂 Nora"
calls all ≥ 0.6.

---

## 4. COMMANDS TO RE-ESTABLISH (MacBook)

```bash
# clone lab repo
git clone git@github.com:opsuser-dev/voice-lab.git
cd voice-lab

# rebuild synthetic env (numpy/scipy/soundfile/openwakeword)
python3 -m venv .venv-wake
.venv-wake/bin/pip install numpy scipy soundfile openwakeword onnxruntime

# apply the openwakeword working-tree patch (fork repo)
git clone https://github.com/dscripka/openWakeWord.git oww-src
cd oww-src && git apply ../oww-src-working-tree.patch && cd ..

# data (LARGE — not in git, ~2.5GB+): rsync from norty host or regenerate
# rsync -av norty:/home/<deploy-user>/workspace/voice-lab/{oww-data,data,clips} ./
# - oww-data/output/喂Nora.onnx (SEP 15 BAD artifact - do NOT deploy)
# - /tmp/modal-train.log = V3 train log (forensic evidence)

# modal auth — MUST re-auth (token seen in session; do not reuse printed token)
# pip install modal && modal token new

# retrain
MODAL_WAKE_STEPS=50000 .venv/bin/modal run scripts/modal_train_wakeword.py::main
```

---

## 5. CONTEXT NOTES

- **Current production** (`nora-wt-voice-design` deploy target `/home/<deploy-user>/workspace/nora`):
  still running SEP-15 broken `喂Nora.onnx` (threshold 0.5). Do NOT deploy V4 until
  acceptance passes — the gate will refuse it at the pipeline level.
- **V4 V3 artifact mtime anomaly**: V4 bundle contained an onnx with mtime Sep-15 23:56;
  md5 differed from production (6ed39924 vs 776ef205). The gated path preserved the FAILED
  artifact rename correctly — verify with `modal volume ls wakeword-data output/喂Nora/`.
- **Council sandboxes** (`wake-council-opus/codex/kimi`) on norty host hold raw experiments;
  experiment scripts copied into repo (`wake-council-experiments/`).
- **Voice samples for space-gap demo**: `/tmp/wake-rir-samples/` (10 RIR + 1 clean) —
  generated for Miles, NOT part of training corpus.
- **Miles's real recording**: `/tmp/gate16k.wav` (21.2s, 14 model fire bursts — evidence
  of speech-presence behavior).
- **Threshold 0.42 suggestion withdrawn** — meaningless when distributions overlap.
- **V4 detailed train log was lost** (my bug — overwritten before bundling); fixed the
  code, but the V4 run's intermediate history is gone. Only final metrics + gate result
  survive. Do not trust any per-epoch claim about V4.
