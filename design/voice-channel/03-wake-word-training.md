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

**A. Publish gate (Sep-16, `voice-lab/scripts/modal_train_wakeword.py`).** Train output
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

## 2026-09-16 (evening): why V4 still failed, and the V5 recipe (append)

V4 (Modal run, contamination removed, 50k steps) reported **Accuracy 0.767 /
Recall 0.619 / FP-per-hour 245.4** and was correctly blocked by the publish
gate. This section is the experiment-driven diagnosis. Every number below was
measured in this session on this host; scripts, clip lists, transcripts and
the candidate model are checked in under `voice-lab/` at the repo root.
The V4 artefacts (ONNX + the four feature arrays) were recovered from the
Modal volume (`wakeword-data:/output/`); the V4 `train-run.log` really is lost
(the older script overwrote it with a one-line note).

### The trainer was not the culprit — the features/labels were

A plain 32-unit MLP (no negative-weight schedule, no hard-example mining, no
merge) trained on the **same V4 feature arrays** tops out at test AUC 0.90,
recall 0.81, 18 % adversarial-negative FPR and ~3,900 frame-FP/hr on the 11 h
validation set. The openWakeWord schedule actually did *better* on FP (245/hr)
by trading recall. So candidates (b) weight-schedule spiral and (c) the
per-batch-backward patch are **not** the root cause: the data the head was
given was not separable enough. Candidate (a) — wake audio inside
`validation_set_features.npy` — is refuted by the fact that the final model
fires on only 0.45 % of validation frames and those fires are 2-frame blips
spread across the whole 10.7 h.

(Two upstream `train.py` facts, verified in source, worth knowing: `best_val_fp`
is initialised to 1000 and **never updated**, so `max_negative_weight` doubles
unconditionally in sequences 2 and 3; and `self.fp` counts every 80 ms frame
≥ 0.5, so "FP/hr" is frame-level, ~2× the debounced event rate.)

### Five defects in the V4 data, each measured by ablation

Ablations re-augment the corpus locally (CPU, ~70 clips/s, same
`augment_clips` pipeline) and train the plain MLP, unless stated otherwise.

| # | Defect | Evidence | Effect when fixed |
|---|---|---|---|
| 1 | **Uncommitted `end_jitter` 0.2 → 1.5 s in `oww-src/data.py`.** Positives float anywhere in the 2 s window instead of ending at it; a flat MLP has to learn 16 shifted copies. | V4 features (jitter 1.5) vs same corpus re-augmented at 0.2 | AUC 0.90 → 0.93, bare-phrase recall 0.95 |
| 2 | **12 of 15 positive patterns end on a command** (「喂 Nora 而家幾點呀」…). End-aligned, the head learns the command tail. | On Miles's real recordings the top false alarms were literally the tails: 「而家幾點」「你睇下」「幫我 check…」. Training on the 3 bare patterns only: real-audio false fires 36/56 → 11/56 bursts, validation FP 3,411 → 322/hr | 10× validation FP |
| 3 | **RIR p=0.5 over 1,000 BIRD rooms** smears the onset for no measured gain. | Same corpus, RIR off | validation FP 2,798 → 631/hr, AUC unchanged (0.931 → 0.935) |
| 4 | **Negatives were only ever shown end-aligned (whole 2 s window)**, but the sliding window in production also sees "silence, then the first 60 % of 「喂勞拉」" — which looks exactly like the positive template (silence + 喂 Nora onset). | Per-clip peak on raw trap wavs: 33 of 44 false peaks were mid-clip; a window ending 60 % into a trap clip fired 28 % vs 10 % for the full clip. Adding *prefix* negatives (window ends at a random point ≥ 0.3 s into the clip, left-padded) | raw-clip speech FP 43 % → 1.7 %, trap FP 61 % → 2.3 %, frame-FP 24.7 → 10.5/hr |
| 5 | **`max_negative_weight: 150`** while `auto_train`'s own default is 1000 and the upstream example uses 1500. | Same features, 150 vs 1500 | frame-FP 193 → 24.7/hr (recall 0.83 → 0.78) |

Things that did **not** help, so nobody re-tries them: raising the background
SNR floor to 5 dB (FP up 1.5×); the full 2,000 h ACAV pool instead of the 150k
subsample (identical FP — 50k steps touch only ~1M windows anyway);
`layer_size` 64 (worse); 100k steps (worse); random-alignment *whole-clip*
negatives (jitter 1.5 on negatives — no change, because 2 s clips fill the
window regardless of jitter). Same-voice MiniMax trap negatives matter a
little (held-out same-voice trap FPR 24 % → 7.5 %), so keep the 774 we have.
`augmentation_rounds: 3` was a **no-op** in V3/V4: `compute_features_from_generator`
is passed `n_total=len(os.listdir(dir))`, so the memmap holds one pass
(13,590 rows, verified on the recovered V4 array).

### V5 recipe (all experiments local, ~3 min per training run)

- **Positives**: bare wake only — MiniMax 「喂 Nora」「喂，Nora」「喂Nora」「喂 Nora 呀」
  (3,624) + edge-tts 「喂Nora啦喂Nora啦」 (500, previously mislabelled negative);
  3 augmentation rounds → 12,372 rows; `end_jitter` 0.2 (upstream); RIR off;
  background mix p=0.75 at −10…15 dB (unchanged).
- **Negatives**: 8,000 edge-tts generic/trap + 774 MiniMax traps + 180 new
  edge-tts *command-only* clips (the tails, without 喂 Nora), each as a
  full-window row **and** two prefix-window rows → 26,862 rows; plus the
  usual ACAV (150k) and zh-HK speech (20k) pools.
- **Trainer**: upstream `Model.auto_train`, unmodified, 50k steps,
  `max_negative_weight` 1500, layer 32; FP-validation fed in 20k-window chunks
  (the stock loader materialises ~3 GB and copies it — that is what took the
  host down earlier today).

### Where V5 stands against the acceptance bar

Measured with `voice-lab/experiments/wake-v5/accept.py` (the exp5 peak-score AUC on raw
test wavs, 300/class; exp7 debounced activations on the 10.7 h validation
set) and `real_eval3.py` (timeline over Miles's two Discord recordings, 38 s,
7 wake utterances located by the STT container's transcripts).

| Metric (bar) | Production Sep-15 | V4 | **V5e** (recall side) | **V5h** (= V5e + Nora-mention negatives) |
|---|---|---|---|---|
| exp5 AUC, bare wake vs speech negatives (> 0.97) | 0.25 | — | **0.983** ✅ | 0.941 ❌ |
| exp5 AUC, user's definition (all 15 patterns vs all speech negs incl. 「唔該Nora」-style mentions) | 0.59 | — | 0.88 ❌ (mentions never trained as negatives; see V5h) | 0.932 |
| positive recall @0.5, bare wake (> 95 %) | 0.93 | — | 0.91 ❌ (misses concentrate in ProfessionalHost-F/M and PlayfulMan voices; 5 % of clips score < 0.01) | 0.73 ❌ |
| speech-negative FP @0.5 (raw clips) | 99.7 % | — | 1.7 % | 1.3 % |
| trap-negative FP @0.5 (< 1 %) | 74 % | — | 2.3 % ❌ | 2.0 % ❌ |
| validation FP/hr, frame-level (train.py gate ≤ 1.0) | 482 | 245 | 10.5 ❌ | **0.53** ✅ |
| validation FP/hr, debounced 2 s (< 1.0) | 168 | 106 | 5.5 ❌ | **0.37** ✅ (no run ≥ 3 frames in 10.7 h) |
| real 「喂 Nora」 peak ≥ 0.6 (7 utterances) | 7/7 (but fires on everything) | 7/7 | **7/7, min 0.89** ✅ | 4/7 ❌ (min 0.07) |
| real non-wake false fires in 38 s | 3 | 1 | **0** ✅ | **0** ✅ |

**Two Pareto probes on the V5h negative set:** `max_negative_weight` 500 (V5i)
→ frame-FP 6.3/hr, bare recall 0.84, real wakes 6/7; positives doubled to
100 per batch (V5j) → frame-FP 5.9/hr (debounced 3.3/hr, 0.84/hr for runs
≥ 3 frames), bare recall 0.83, trap FP 2.3 %, **real wakes 7/7 (min 0.78), 0
false fires**. V5j is the best single compromise so far and is checked in as
`voice-lab/models/v5j-喂Nora.onnx`; it still fails recall (> 95 %) and FP/hr
(< 1.0).

**Seed variance (V5e config, seeds 0/1/2):** frame-FP 10.5 / 12.0 / 9.6 per hour,
augmented-test recall 0.744 / 0.742 / 0.734, all 0 false fires on the real
recordings, real wakes ≥ 0.6: 7/7, 6/7, 6/7. The recipe is stable; the real-audio
wake margin is not yet (7 utterances is a thin sample).

**Deploy recommendation: do not deploy either V5 artefact yet.** V5h is the
first model that would *pass the publish gate's FP criterion* (0.53 frame-FP/hr,
zero runs ≥ 3 frames in 10.7 h) and is silent on Miles's non-wake speech, but
the Nora-mention negatives buy that by suppressing "Nora" itself: bare-wake
recall drops to 0.73 and 3 of 7 real wakes fall under 0.6. V5e keeps the wakes
(7/7 ≥ 0.89) at 10 FP/hr. The two sit on a Pareto front whose recall end is
capped by the **positive set**: misses concentrate in three of the six MiniMax
voices and 5 % of bare clips score < 0.01 regardless of training. The next
lever is therefore not another negative set but **more and realer positives**
— Miles's and at least one other speaker's 「喂 Nora」 through the Discord path
(the Post-V4 item above), or at minimum more TTS voices — after which the V5h
negative set should be re-applied. Threshold stays 0.5; nothing here justifies
moving it.

### Operational notes from this session

- **Never build the FP-validation tensor whole.** `train.py:875` builds a
  481k × 16 × 96 float32 array (~3 GB) and then copies it; two such jobs in
  parallel froze the host (24 GB swap, no OOM-kill). Every heavy job now runs
  under `systemd-run --user --scope -p MemoryMax=… -p MemorySwapMax=0`.
- The `/tmp` evidence (`gate16k.wav`, V3 `modal-train.log`) did not survive
  the reboot; the real recordings are still in `~/workspace/voice-lab-data/sidecar/recordings/`
  and the STT container (`/v1/audio/transcriptions`, model `sensevoice`)
  labels bursts well enough to build a real-audio eval.
- openWakeWord upstream: last release v0.6.0 (2024-02), last commit 2025-12,
  README "English only", adversarial-phrase generator is CMU-phoneme based.
  Alternatives survey (Porcupine has no Cantonese; sherpa-onnx KWS has no yue
  model; STT-as-wake via SenseVoice is the only path with verified Cantonese
  today) is in `voice-lab/docs/oww-and-alternatives.md`.

## 2026-09-17 (overnight): V6 — 278 cloned Cantonese speakers (append)

**Why.** V5's recall ceiling was the positive set: six MiniMax voices, and the head
partly learned timbre. Cantonese TTS catalogues top out at ~13 speakers, so speaker
diversity has to come from zero-shot cloning seeded by real speakers. Plan and the
verified model facts: `voice-lab/docs/PLAN-v6-cloned-voices.md`,
`voice-lab/docs/cosyvoice-cantonese-plan.md`.

**Pilots (Modal T4).** `ASLP-lab/Cosyvoice2-Yue` renders every Cantonese negative
perfectly but not the Latin name (2/120 positives pass the STT gate; 諾拉 → nok-laai,
= the trap, never a positive — Miles's call). Stock `CosyVoice2-0.5B` in
**cross-lingual** mode is the only working arm (50 % of 「喂，Nora。」/「喂Nora。」 pass;
the 呀 variant 0 %). Gate = SenseVoice transcript must contain nora/nova/喂ra; control:
28/30 MiniMax positives pass, **1/10 edge-tts 「喂Nora啦」 pass** → those 500 V5
positives were mispronounced and are removed from V6.

**Phase 1.** 278 Common Voice yue speakers (one 3–10 s prompt each, transcript as
prompt text), 2 texts × 3 speeds + 8 negatives per speaker, 4 containers, 3,892 clips,
0 errors, 2.2 s/utterance. Gate: **989/1,668 positives over all 278 speakers**,
2,070 negatives (traps/commands 100 %, mentions 66–78 %). Speaker-disjoint split
(28 held-out speakers). Same-speaker real Common Voice clips added as real-speech
negatives (1,626). Final V6: 13,545 positive rows / 43,376 negative rows.

**V6 results** (upstream `auto_train`, 50k steps, weight 1500, 100 positives/batch, three seeds):

| Metric (bar) | V5j | V6 s0 | V6 s1 | V6 s2 |
|---|---|---|---|---|
| exp5 AUC, user's definition (> 0.97) | 0.94 | 0.970 | 0.971 | **0.972** ✅ |
| bare-wake recall @0.5 (> 95 %) | 0.83 | 0.80 | 0.81 | 0.83 ❌ |
| trap FP @0.5 (< 1 %) | 2.3 % | 0.7 % ✅ | **0.0 %** ✅ | 1.3 % |
| speech-negative FP @0.5 | 2.0 % | 0.0 % | 0.3 % | 0.0 % |
| frame FP/hr (train.py gate ≤ 1.0) | 5.9 | **0.97** ✅ | 2.2 | 3.1 |
| debounced FP/hr (< 1.0) | 3.3 | **0.65** ✅ | 1.5 | 1.7 |
| real wakes ≥ 0.6 (of 7) | 7 | 4 | 7 (min 0.75) | 7 (min 0.79) |
| real false fires / 38 s | 0 | 0 | 0 | 0 |
| held-out cloned-speaker recall @0.5 | 0.27 | 0.49 | 0.58 | 0.58 |

**Where the remaining recall goes.** The hard misses (score < 0.05) are real,
correctly pronounced wake utterances (STT hears 「喂 nora」 in 43/54) that are
**short**: median 0.81 s vs 1.09 s for hits; recall is 0.92 on clips ≥ 1 s and 0.74 on
the 35 % under 1 s. Not a streaming-startup artifact (leading silence/noise changes
nothing; openWakeWord seeds its buffer with noise embeddings). Training positives
are only 10 % under 1 s and the cloned clips are ~2 s. V7 (running) adds
time-stretch 1.1–1.5× on positives.

**Deploy recommendation (V6):** seed 0 is the first artefact that clears the publish
gate (0.97 frame-FP/hr) and the trap bar, but it loses 3 of 7 real wakes; seeds 1/2
keep all real wakes and the trap bar at 1.5–1.7 debounced FP/hr. Recall @0.5 on
fast utterances is the open item. Not deployed; nothing changed in production.

**Weighting probes on V6 (same data, seed 2):** `max_negative_weight` 1000 → bare recall
0.84, frame-FP 5.7/hr, real wakes 4/7; positives 150/batch → recall 0.83, frame-FP
2.8/hr, trap 0.7 %, AUC 0.974 / 0.984, real wakes 7/7 (min 0.62). Neither moves recall
past ~0.84: the ceiling is the short-utterance gap in the data, not the loss weighting.

**V7 (time-stretch 1.1–1.5× on positives, p=0.6) — negative result, do not repeat.**
Two seeds: bare recall 0.72 / 0.77, real wakes 4/7 with minima ≈ 0.001, frame-FP
2.2–4.2/hr. The phase-vocoder stretch changes the spectral detail the frozen embedding
keys on, so the head learns the artefact rather than "fast 喂 Nora". Fast utterances
have to come from the TTS itself (CosyVoice `speed` 1.3–1.5, ~0.5 T4-hour, not run:
the approved Modal budget is spent) or from real speakers.

**Status at hand-off (2026-09-17 morning).** Best artefacts, all in `voice-lab/models/` (repo):
`v6_s2` (AUC 0.972, trap 1.3 %, 7/7 real wakes, 1.7 debounced FP/hr), `v6_s1` (trap 0 %,
7/7, 1.5/hr), `v6_s0` (clears the FP gate at 0.97 frame-FP/hr but 4/7 real wakes),
`v6_p150` (AUC 0.984 bare, trap 0.7 %, 7/7, 1.6/hr). Open acceptance items: recall @0.5
> 95 % (at 0.80–0.84, limited by sub-second utterances) and FP/hr < 1.0 (at 1.5–1.7
debounced for the seeds that keep all real wakes). Nothing deployed.

**Deployed as trial — 2026-09-17 00:03 HKT (Miles: "yes let deploy it").** `deploy/voice-wake/models/喂Nora.onnx`
= V6 seed 2 (sha256 efb5c585…), threshold unchanged at 0.5. Image built from this worktree
(`docker build deploy/voice-wake`), container recreated under the live compose project with the
production `.env`; 18/18 voice-wake tests pass against the new model. In-container check on Miles's
two recordings: 5/5 and 4/4 wakes fire once each, no fires in any non-wake segment. Known trial
limits: ~1.5 debounced FP/hr on the English validation set, and fast (< 1 s) 「喂 Nora」 missed at
0.5 about a quarter of the time. **master still carries the Sep-16 model**: a rebuild from master
reverts this until feat/wake-v4-postmortem is merged.
Note: `deploy/voice-wake/models/` is gitignored, so the shipped file is NOT in git; the
canonical copy is `voice-lab/models/v6_s2-喂Nora.onnx` — copy it into
`deploy/voice-wake/models/喂Nora.onnx` before building on any other checkout.
