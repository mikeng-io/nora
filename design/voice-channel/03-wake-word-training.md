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
