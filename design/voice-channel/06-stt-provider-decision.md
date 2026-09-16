---
type: decision
title: "STT Provider — noise-detection primary, SenseVoiceSmall backup (Decided)"
description: "A/B tested five engines on real Discord voice audio (clean + degraded). JackyHoCL whisper-large-v3-turbo-cantonese-noise-detection wins on quality (RV THERE YET / Discord preserved, Hant + punctuation) at RTF 0.49; existing SenseVoiceSmall stays as the always-on, fast fallback. Qwen3-ASR too slow, MiniMax ASR violates the local-audio posture, Whisper large-v3 too slow."
tags: [nora, voice, stt, noise-detection, sensevoice, decision, ab-test]
timestamp: '2026-09-15'
edges:
  - {dst: 05-sidecar-receive, rel: decides, provenance: asserted}
---

# STT Provider — noise-detection primary, SenseVoiceSmall backup

**Decision (Miles, 2026-09-15):** voice-channel STT runs **JackyHoCL
`whisper-large-v3-turbo-cantonese-noise-detection`** as primary, with the
existing `deploy/stt` **SenseVoiceSmall** as the fallback (fast path /
failover). The rest of this file is the evidence and the boundary of the
claim.

## Why this is a decision file

`05-sidecar-receive.md:61` drew the pipeline box as
「STT — SenseVoice, existing deploy/stt」. That was true when written
(2026-09-14) and is still true for the *backup*. The *primary* is new: the
wake-gated voice channel transcribes with the noise-detection model, not
SenseVoice. Same pipeline shape, different engine, different failure
characteristics — recorded here so the drift is visible.

## Test method (what the comparison actually measured)

- **Audio:** two real recordings from the DAVE spike path (`voice-lab/sidecar/recordings/`):
  - `live-miles-repaired.wav` — 17.1 s, *degraded* (mic moved away mid-phrase; the
    four early engines all failed the same phrase — a clue the loss was in the
    signal, not the model).
  - `100000000000000001-1789405579646.wav` — 21.1 s, *clean* (fixed mic), the
    corpus for the decision. Contains: 「喂 Nora」×3, 「RV THERE YET」(game title),
    「Delia」「check」「email」「Discord」「online」.
- **Engines:** SenseVoiceSmall (local), MiniMax asr-1.0 (cloud), Whisper
  large-v3 (local), Qwen3-ASR-1.7B (local), JackyHoCL turbo-cantonese-yue-english
  (local, CT2), JackyHoCL noise-detection (local, transformers).
- **Methodology note:** single-utterance waveform comparisons are random
  (TTS sampling); the comparison here is transcript-level on the same live
  utterance, plus RTF wall-clock. Sufficient for an engine choice, not for a
  peer-reviewed benchmark.

## Result (clean audio, same 21.1 s utterance)

|| RV THERE YET | Discord | check/email/online/Delia | Punctuation | RTF |
|---|---|---|---|---|---|
| SenseVoiceSmall (prod) | `r there yet` (R only, no V) | `discuss` ✗ | all ✓ | none, Simplified | **0.04** |
| MiniMax asr-1.0 (cloud) | `Are we there yet` (pun!) | `Discuss` ✗ | all ✓ | ✓ | 0.1 (net) |
| Whisper large-v3 | `RVDare yet` | ✓ | email→電郵 ✗ | ✓ | 1.0 |
| Qwen3-ASR-1.7B | `RV Dare Yet` ✓ | ✓ | all ✓ | ✓ | 2.8 |
| turbo-yue-english (CT2) | `RV dare yet` ✓ | `discount` ✗ | all ✓ | ✓ Hant | 0.61 |
| **noise-detection** | **`RV there yet` ✓✓** | **`discord` ✓** | all ✓ | ✓ Hant | **0.49** |

On the degraded 17.1 s clip every engine failed the same phrase — the mic-loss
explanation, not engine quality, governs there. The clean clip is the decision.

## Why noise-detection wins

- **Only engine to keep both the acronym (`RV`) and the platform name
  (`discord`) intact.** Whisper-large-v3 and Qwen both heard the English
  fragments but mangled one of the two; SenseVoice lost the `V` and wrote
  Simplified.
- **Hant + punctuation out of the box** — Nora's doctrine is yue-Hant-HK;
  SenseVoice emits Simplified and the STT server converts (an extra moving
  part).
- **RTF 0.49 on CPU (10.4 s on 21.1 s audio)** — ~4 s speech → ~2 s processing.
  Qwen's quality is comparable but RTF 2.8 is not a voice-channel latency.
- Model card: fine-tuned from turbo-yue-english with noise-labeled training to
  reduce streaming hallucination; `%nz` tag for noise (removed at post).
- License MIT; `transformers` runtime (no CT2 for this variant, the plain
  yue-english variant does have CT2).

## Why SenseVoiceSmall stays (and why it is the backup, not the primary)

- **Latency floor:** RTF 0.04 — for the wake-gated path it is effectively
  instant; a degraded/edge case that should not hold up the room.
- **Already deployed** (`deploy/stt/`, own image, healthcheck, model cache
  volume) — fails over with zero new infrastructure.
- **Privacy posture intact:** stays on the private `nora` network; audio
  never leaves the box.

## Rejected alternatives (and the one-line reason)

- **MiniMax asr-1.0:** best cloud quality, but audio would leave the box —
  violates the STT-local posture from `00-overview.md` (inbound audio stays
  local). Not a codec/quality question; a boundary question.
- **Whisper large-v3:** RTF 1.0, no real-time headroom.
- **Qwen3-ASR-1.7B:** best English preservation along with noise-detection,
  but RTF 2.8 and 4.7 GB model — 6× slower than the winner.
- **SenseVoiceLarge:** does not exist — ModelScope 404s; FunASR registered
  models list only SenseVoiceSmall. (2026-09-14; do not re-propose.)
- **Deepgram/AssemblyAI:** no Cantonese; **Oakmeeting.ai:** HK specialist but
  no API; **Typeless:** no API.

## What this does NOT decide

- **Which container serves the primary.** `deploy/stt` currently ships
  SenseVoice. Adding noise-detection as primary means a second model (or a
  model-switch in the STT server) + weight cache volume. Wire-up is Stage 3
  implementation, not this decision.
- **Wake gate behaviour.** Unchanged and still gated (`03-wake-word-training.md`).

## The MiniMax-wake-training connection (decided alongside)

Synthetic wake positives are generated with **MiniMax TTS, 6 Cantonese voices
× 10 variants × 5 reps = 300 clips** (`voice-lab/scripts/gen_wake_minimax.py`,
`data/wake-minimax/`) — an upgrade from the single-voice edge-tts set used in
the 2026-09-14 probe. Speaker diversity is the point: a classifier trained on
six real Cantonese voices should generalise to the room better than one trained
on one TTS voice. The Stage 3 gate (real-audio probe) still stands.
