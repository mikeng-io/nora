---
type: design
title: "Voice Channel — Receive Path & Identity (Sidecar, Decided)"
description: "Stage 2/3's receive route is settled: a Node sidecar (@discordjs/voice + libdave, Discord's own stack) pipes per-user PCM into the existing wake → STT → reactive path. Audio is transient, identity is protocol-native, memory sees a user input, not Nora's state."
tags: [nora, discord, voice, dave, sidecar, node, receive, identity, privacy]
timestamp: '2026-09-14'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 03-wake-word-training, rel: enables, provenance: asserted}
---

# Voice Channel — Receive Path & Identity (Sidecar, Decided)

## Why a Node sidecar, and why it wins the stability/future-support question

Compared live (2026-09-14, GitHub API + PyPI + discord.js source):

| Route | DAVE receive status | Future support |
|---|---|---|
| A. `discord-ext-voice-recv` | PyPI 0.5.2a179 is pre-DAVE (2025-06-18); DAVE decrypt sits in open PR #54, community-tested 2026-03 but unmerged | Single maintainer, alpha-on-alpha |
| B. pycord | `start_recording` exists but its OWN code warns "broken due to DAVE"; fix PR #3159 open, unmerged; AI-written #3185 was rejected by maintainers | Waiting on maintainers, no timeline |
| **C. Node sidecar (`@discordjs/voice` + Discord's libdave)** | Discord's own client library; `VoiceReceiver` decrypts per-packet with the negotiated mode + secret key + userId | Discord maintains it; protocol changes land upstream — `npm update` tracks them |

**C is the decision.** The cost is one more service in the stack, which the
host absorbs trivially (measured 2026-09-14: Node v22.22.3 present, nora
containers all <0.1% CPU, 13GiB RAM available; a receive-only sidecar costs
tens of MB RAM and ~0% CPU idle, 2–5% of one core while someone speaks).

**Repository home (operator expectation, 2026-09-14): the sidecar ships IN
the nora repo**, mirroring how STT landed — prototype/spike lives in
`~/workspace/voice-lab/` first (the voice-labs pattern Miles established:
prototype outside the repo, design + implementation inside), then the
production sidecar moves into the repo's `deploy/` tree as a sibling of
`deploy/stt/` (own `Dockerfile` + healthcheck + smoke test), with its
package.json pinned in the repo. It is a deployment artifact of Nora, not an
external tool — exactly as `deploy/stt/` is. Prototype location is
`~/workspace/voice-lab/sidecar/` (scaffolding + spike harness, 2026-09-14);
the repo placement happens as part of stage 2 implementation after the DAVE
spike gate passes.

## The sidecar is a dumb pipe — wake lives in Python

Corrected architecture (Miles's catch, 2026-09-14 — a naive diagram drew the
sidecar feeding STT directly; that would be always-on transcription, which is
REJECTED path C of `04-deferred.md` §2):

```
Discord voice channel
  │  Speaking opcode: {user_id, ssrc}   ← protocol-native identity
  ▼
[Node sidecar — dumb pipe]
  1. join via @discordjs/voice
  2. subscribe(userId) per speaking member → per-user Opus stream
  3. DAVE-decrypt (libdave / connection secret key) → OPUS → PCM
  4. forward {user_id, PCM} to the wake service over local transport
  ▼
[Wake gate — Python, openWakeWord 「喂 Nora」]
  VAD + mel-spec embeddings FIRST: nobody is transcribed unless the
  wake phrase fires. Front-end ≈ 2.02% of one core, resident.
  ▼  (only the woken utterance passes)
[STT — SenseVoice, existing deploy/stt]
  local, audio never leaves the box; ~0.23s wall for a 2s utterance
  ▼
text + [voice @user] provenance → existing ingest → reactive path → Stage 1 TTS
```

The sidecar does NO AI, NO wake, NO STT — it is receive/decrypt/decode and
forward. All intelligence stays in the Nora Python stack. openWakeWord is a
Python library, so the wake gate is a Python process; the sidecar forwards
PCM to it over a local socket/HTTP (implementation detail, decided at spike).

## Text-in, audio-discard — NOT a media object

The voice-wake utterance enters Nora as **text**, never as an audio artifact:

- STT runs on the woken utterance; the transcript becomes a synthetic message
  row with `[voice @user]` provenance and the same `applies_to`
  parameterization — the third speech occasion, not a new pipeline.
- **Audio is discarded after STT.** No `media_objects` row, no sha256 dedup,
  no description pass, no replay. This is a privacy posture: wake-gated
  audio that was never transcribed stays untranscribed, and even the woken
  clip is not retained. A future "what did Nora just hear" replay is a
  deliberate non-feature.
- Distinct from the voice-note path (`ingest/stages/media.py`), where the
  audio IS the message artifact and retention is the point. The two must not
  be confused; different shapes, different retention, same STT service.

## Transcript visibility & memory — it IS a user input

Three questions Miles asked directly, answered at `file:line` level in the
current repo:

1. **Will it appear in the transcript?** Yes — it is a user input. A voice
   wake row enters the same audit + render path as a typed message, with its
   provenance tag. Nothing about it is Nora speaking.
2. **How is it anchored to a user?** Protocol-native. Discord maps SSRC →
   `user_id` per speaking packet (`ssrcMap.update({userId, audioSSRC})`);
   `subscribe(userId)` yields a per-user stream by construction. Nora's
   existing identity stage (`pipeline.py:293` — "Identity first, so the
   resolved person_id lands in the audit INSERT") then maps Discord ID →
   person via `persons.yaml`. No speaker recognition, no guessing — same
   identity flow a typed message uses.
3. **Does it inject into memory?** It feeds Honcho via the existing
   `memory_feed` stage (`pipeline.py:331-332`) as a **user input** (D6
   mapped sender) — the same as any message the user typed. It is about the
   user, in the user's peer profile. It never becomes Nora's agent state,
   memory, or self-representation.

## Disclosure stays mandatory

DAVE trust model unchanged: the bot joining a room decrypts the channel.
Because the sidecar receives per-user audio, everyone in the room must know
Nora (and the music bot) can hear them. This is existing posture
(`00-overview.md`), unchanged by the sidecar choice.

## Gating the stages — what still must be proven before implementation

- **Stage 2 (receive) is gate: a live DAVE spike.** Package availability is
  NOT evidence of a working DAVE receive in Nora's actual guild (MLS
  negotiation, per-user SSRC mapping, decrypted PCM actually flowing). The
  spike: join a real voice channel with the sidecar, subscribe, and verify
  decrypted PCM frames arrive (e.g. write a WAV and check non-silence + F0).
  Operator-involved (a bot token + Miles in the channel) — not runnable by an
  executor alone.
- **Stage 3 (wake word) keeps its existing gate:** a real-audio probe
  through the actual Discord voice path with music playing underneath —
  synthetic 1.000 AUC is not evidence (03-wake-word-training.md §gap).

Neither gate has passed. Until they do, stage 2/3 implementation would be
building on unproven soil; the design says prove first, then build.
