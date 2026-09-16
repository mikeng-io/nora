---
type: design
title: "Voice Channel — Wake Word (Stage 3, Gated)"
description: "The synthetic probe passes at AUC 0.94–1.00 with zero false positives — but every clip is TTS, so the measurement does not yet cover the room the wake word must actually work in."
tags: [nora, wake-word, openwakeword, cantonese, probe, risk, gated]
timestamp: '2026-09-14'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# Voice Channel — Wake Word (Stage 3, Gated)

## Status: measured, not yet trusted

This stage is specified but **not authorised to start**. It carries the only
*epistemic* risk in the bundle: the probe result is real and reproducible, but
it was measured in a domain that is not the deployment domain. The gap is
named below, and closing it is the gate.

## The probe — reproduced live 2026-09-14

Setup: `~/workspace/voice-lab/data/sepprobe/` — 150 positive clips / 150
negative clips (10 positive phrase variants), openWakeWord's
`AudioFeatures.embed_clips` → `LogisticRegression`, **leave-one-variant-out**
group cross-validation so a held-out phrasing is never in training. Re-run
this session via `.venv-wake/bin/python scripts/backbone_probe4.py`:

| Held-out variant | AUC | FP / 150 | FN |
|---|---|---|---|
| 喂 Nora | 1.000 | 0 | 1/16 |
| 喂 Nora 下一首 | 0.941 | 0 | 1/10 |
| 喂 Nora 停一停 | 0.979 | 0 | **7/12** |
| 喂 Nora 呀 | 1.000 | 0 | 2/18 |
| 喂 Nora 唔該 | 1.000 | 0 | **6/16** |
| 喂 Nora 幫我 | 1.000 | 0 | 0/16 |
| 喂 Nora 幫手 | 1.000 | 0 | 1/17 |
| 喂 Nora 得唔得 | 1.000 | 0 | 0/13 |
| 喂 Nora 播歌 | 1.000 | 0 | 0/15 |
| 喂，Nora。 | 1.000 | 0 | 0/17 |

**Two findings, and the second is the more useful one.**

### Finding 1 — the boundary case is strong

**Zero false positives across all 150 negatives in every single fold.** The
negative set is not soft: it includes `Nora 係邊個` and `我琴日同 Nora 傾咗
好耐` — sentences where the name "Nora" is *present but not addressed*. The
classifier separates "the word appears" from "the assistant is being
addressed", which is the actual discrimination a wake word has to make.
AUC 1.000 on the bare wake phrase.

### Finding 2 — false negatives cluster on the phrase+command boundary

The misses are not spread randomly. They concentrate on the two variants where
a command follows immediately — `停一停` (7/12 missed) and `唔該` (6/16) —
while bare or single-token continuations are near-perfect (`幫我` 0/16,
`播歌` 0/15, `得唔得` 0/13).

The likely cause is **phrase-length normalisation**: `backbone_probe4.py`
pads or takes the **last 3 s** of each clip (`buf[:]=a[-WIN:]` when long
enough, else left-pads). For a *longer* phrase the discriminative "喂 Nora"
onset gets pushed toward the window edge or partly truncated, while the
command tail — which is not the wake phrase — dominates the embedding.

**The implication is design-relevant, not just a metric:** the wake word
should fire on the **onset** (`喂 Nora`) and let the *turn* carry the command,
rather than trying to match `喂 Nora <command>` as one utterance. A wake word
that must recognise the whole sentence-plus-command both misses more and
conflates waking with instructing. This matches how Siri is actually used —
you say the name, then you speak.

## The gap that gates the stage

**Every clip in the probe is `edge-tts` synthetic speech. The deployment audio
is not.** The probe therefore measures:

> synthetic TTS → synthetic TTS

and the stage needs:

> live microphone in a Discord voice channel, over *ducked music* → decision

Four concrete differences, each a plausible false-positive or false-negative
source, none of them measured:

1. **Music bleed.** In stage 1 the music is *attenuated, not stopped* — at
   `duck_gain` there is still musical audio in the same stream the wake word
   listens to. **No negative clip in the probe contains music.** This is the
   single largest untested risk and it exists *because* of stage 1's design.
2. **Codec.** Discord voice is Opus at low bitrate. Every probe clip is clean
   MP3/WAV from a TTS engine.
3. **Channel.** Discord applies noise suppression and AGC by default. Both
   alter exactly the spectral detail a wake word keys on.
4. **Human voice.** `edge-tts` voices are consistent and clean; real speakers
   differ in pitch, pace, accent, and mic quality. Miles's own voice was never
   in the probe.

**Gate to start stage 3: a real-audio probe.** Record Miles (and at least one
other speaker) saying the wake phrase and near-miss negatives **through the
actual Discord voice path, with music playing underneath**, then re-run the
same leave-one-out evaluation on that set. The stage starts when that probe's
false-positive count on music-bearing negatives is acceptable, and not before.
A synthetic 1.000 AUC is not evidence for a music-bearing channel.

## 2026-09-16: production post-mortem + V4 retrain (append)

**What happened.** Wake word shipped (Sep-15, `喂Nora.onnx`) but failed in
production: repeated "喂 Nora" misses, peaks ranged 0.596–0.832 and sometimes
dipped under threshold 0.5. Three-agent council (Opus/Codex/Kimi, independent
sandboxes) + Norty-verified evidence showed the deployed model is a
**speech-presence detector, not a wake-word discriminator**:

- Training log (`/tmp/modal-train.log:3456-3458`): `Final Model Accuracy:
  0.6784 | Recall: 0.3357 | FP/hour: 482.12` — target `0.2`/hr, off by 2400×.
  The ONNX was saved, **then** train.py crashed on `onnx_tf` conversion; the
  failure looked like a packaging error and nobody read the metrics 5 lines up.
  The bad artifact shipped.
- Independent score runs: trap negatives (「今日天氣幾好」0.786、「我好攰呀」
  0.779、「喂勞拉」0.796) all fire ≥0.5; true positives (喂Nora) scored 0.786 —
  *lower than the negatives*. No threshold separates the classes.
- Real recording (21.2s, production container): 14 fire bursts on ~10 speech
  bursts, scores 0.93-0.98 whenever speech present, 0.00 on silence.
- **Codec/AGC/volume exonerated** (Kimi): opus 32k/64k round-trip Δ≈0, −20dB
  Δ≈0, lowpass 3k Δ≈0, mild NS Δ≈0. Synthetic→real space/room (RIR) matters at
  the extreme (large-hall reverb → 0.055) but is NOT the binding constraint:
  the model fails on its own synthetic test set.

**Root cause chain.** (1) Training run failed (metrics disaster) but the
failure was masked by the unrelated `onnx_tf` traceback; (2) the trainer had a
known hard-negative contamination: `negative_train/` contains 500
`Nora-*.wav` clips (edge-tts phrases like 「喂Nora啦」) labelled negative — with
`max_negative_weight: 150` those ~1120 contradictory clips outweigh all 13,590
positives, so the optimiser's best move is to abandon wake-word direction and
fall back to speech-vs-background; (3) 20,000 steps ran though yml sets 50,000
(env default `MODAL_WAKE_STEPS=20000`).

**Lab tooling bug found (important).** `real_audio_gate.py:36` feeds
`float32/32768` but openwakeword `utils.py:194` requires **int16** PCM
(`astype(int16)` truncates [-1,1] to 0/1 → silence → 0.001). Pre-council
real-audio scores were invalid. All evaluation must use int16.

**Learnability check (Kimi exp8).** Mean-pooled LR probe on existing
featurizer + data: 92% recall @ 0.67% FPR. Data and featurizer are fine — the
artifact/training is broken.

### Fixes implemented

**A. Publish gate (Sep-16, `scripts/modal_train_wakeword.py`).** Train output
is now captured to `train-run.log`; after training, `_parse_final_metrics`
extracts the Final Model block and `_publish_gate` blocks publish unless
`recall >= 0.85` AND `fp_per_hour <= 1.0` (env overridable:
`MODAL_WAKE_RECALL_MIN` / `MODAL_WAKE_FPHR_MAX`). A blocked artifact is kept
in the volume but renamed `FAILED-recall{...}-fphr{...}.onnx` — it can never
be pulled as canonical `喂Nora.onnx`. Verified against the real Sep-15 log:
BLOCKED (recall 0.336, fphr 482.1).

**B. V4 retrain (Sep-16, in progress).** Changes vs V3:
- `negative_train/` + `negative_test/` packaging **excludes every clip whose
  filename contains "Nora"** — full audit found **6,500/15,274 train +
  780/2,180 test** contaminated clips (13 edge-tts phrase patterns × 500,
  incl. literal wake phrase 「喂Nora啦喂Nora啦」 and 「喂呀Nora你喺度」
  mislabelled negative). With `max_negative_weight: 150` the negative
  weight ratio is ~71:1 against positives — the V3 optimiser had no
  incentive to learn the wake phrase and collapsed to speech-presence
  detection. Files stay on disk; only the training tars exclude them —
  reversible.
- `steps` 20,000 → 50,000 (yml already said 50,000; env default was eating it).
- RIR subsample 300 → 1000 (BIRD has 10,000 rooms; 3% coverage was thin).
- `augmentation_rounds` 1 → 3 (each positive gets 3 room/space versions).

**Acceptance criteria for V4 (all must pass before deploy):**
- ROC-AUC > 0.97 on positive_test vs speech negatives
- FP/hr < 1.0 on validation_set_features
- trap-negative FP < 1% at threshold 0.5
- positive recall > 95% at threshold 0.5
- real Discord-recorded "喂 Nora" calls all score ≥ 0.6
- THEN the threshold calibration question (0.5 vs 0.45) becomes meaningful.

**Post-V4 (not started):** add 20-50 real-voice positives from Miles +
Discord-path recordings as hard positives/validation (closes the
synthetic→real domain gap properly); then re-run the full eval on real audio.

## What is settled regardless of the gate

- **The port and the placement are decided.** Wake detection is an inbound
  speech capability and belongs in `domain/ports.py` as a sibling of
  `Transcriber` — a `WakeDetector` protocol, with the openWakeWord
  implementation in `infra/`. The probe's models already load from
  `voice-lab/models/`.
- **The receive path underneath it is a Node sidecar** (`@discordjs/voice` +
  DAVE decrypt) forwarding per-user PCM to a Python wake process — see
  `05-sidecar-receive.md`. The wake word itself is Python; only the
  receive/decrypt is Node. Decided 2026-09-14 after a live comparison of
  the three receive routes (discord-ext-voice-recv / pycord / sidecar).
- **`喂 Nora` is the phrase.** Confirmed as the tested wake phrase; the probe's
  positives are all built around it.
- **Fire on onset, not on phrase+command** (Finding 2).
- **Receive must precede it.** No wake word without the voice receive path,
  which does not exist today (see `00-overview.md`).
