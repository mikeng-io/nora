---
type: design
title: "Voice Channel — Overview & Why TTS Lands First"
description: "Nora's voice-channel presence, staged so the one stage with no unresolved question ships first: TTS with ducking needs neither a receive path nor a model."
tags: [nora, discord, voice, tts, ducking, wake-word, staging]
timestamp: '2026-09-14'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

> **PARTLY DEPRECATED 2026-09-16 — the «What already exists» section names machinery that phase 1 of the audio-owner controller (`design/voice-channel/07-audio-owner-controller.md`, DEC-187) removed or superseded.**
> `CrossfadeSource` and `DiscordVoiceSession` are deleted: the controller owns the one voice connection, speech hard-cuts music (no ducking mixer in phase 1), and her reply audio reaches it over `POST /stream`. The staging argument (TTS first, receive later, wake last) and the MiniMax TTS-provider decision still stand.

# Voice Channel — Overview & Why TTS Lands First

## The goal

Miles's ask (2026-09-14): Nora answers in a Discord voice channel the way Siri
does — **the music under her drops, she speaks the answer in Cantonese, the
music comes back up.** The wake word (「喂 Nora」) and the answering are two
halves of one experience, but they are not one piece of work and they do not
carry the same risk.

## Why TTS is the correct first stage — not merely the easiest

The full loop is: **wake word → STT → agent turn → TTS → duck → play.**

Read it as a dependency chain and the ordering is forced, not chosen:

| Stage | Needs a receive path? | Needs a model? | Unresolved question? |
|---|---|---|---|
| **TTS + ducking** | No — text is already in hand | No | None |
| Voice receive | Yes (`discord.py` sink) | No | None |
| Wake word | Yes (above) | Yes (custom oww) | Synthetic→real acoustic gap |

**TTS is the only stage that is blocked by nothing.** It sits at the *end* of
the chain, so building it first makes the tail of the pipeline live and
verifiable while the head is still absent. Every later stage then has a
working destination to feed.

The wake word is deliberately last. It is the only stage carrying an
*epistemic* risk rather than an engineering one, and that risk is not yet
measured — see `03-wake-word-training.md`.

**Grounding for the "receive path is absent" claim:** a repo-wide search for
`start_recording|AudioSink|VoiceRecvClient` under `nora/` returns **zero
results** (2026-09-14). The `stt` service (`deploy/stt/`, container `stt`, up
and healthy) is wired to **voice-note ingest only** — `nora/ingest/stages/
media.py` — not to live voice-channel audio. There is no path today by which
Nora hears a voice channel.

## What already exists (and must not be rebuilt)

The design leans entirely on shipped machinery:

- **`CrossfadeSource`** (`nora/platform/discord/crossfade.py`) — deleted
  2026-09-16 (DEC-187) — solved the hard mechanical constraint: Discord's
  player reads **one** `discord.AudioSource` at a time, and this class read
  two sources simultaneously, blending them per 20 ms frame. Ducking was the
  same mechanic with a different gain law. The controller now hard-cuts
  instead; the spike record is `design/discord-music/05-crossfade-spike.md`.
- **`VoiceSession` port + `DiscordVoiceSession`** — the local session is
  deleted (DEC-187): the audio-controller owns the voice connection, and the
  `VoiceSession` port survives as `HttpVoiceSession` over the controller's
  HTTP control plane. The persisted volume is a `/music/volume` call; the
  live `.source` swap is the controller's replace-play.
- **`SpeechTtsConfig`** (`nora/config.py`) already exists with
  `enabled/provider/voice`, and `config.toml` already carries
  `[speech.tts] enabled = false`. **No schema migration is needed** — the
  decision from 2026-09-14 (MiniMax CN / `speech-2.8-turbo` /
  `Cantonese_CuteGirl`) fills config values, not new fields.
- **`Transcriber` port + `SttResult`** (`domain/ports.py:917`, `:939`) are the
  exact shape the outbound port copies.

## The one real decision: TTS is an outbound cloud call

MiniMax TTS is an outbound HTTP call to `api.minimaxi.com` (CN plan,
`sk-cp-` key; `api.minimax.chat` as fallback host). This is a
**deliberate asymmetry** with STT:

- **STT is inbound audio → stays local.** A private `nora`-network service
  with no host port, so recordings never leave the box.
- **TTS is outbound text → leaves the box.**

The asymmetry is acceptable because the *content* differs in kind: STT handles
whatever a person happened to say (private, unknown in advance), while TTS
speaks text Nora has already decided is fit to say aloud in a shared channel.
Miles confirmed **MiniMax CN** as the provider on 2026-09-14 — `speech-2.8-turbo`
model, `Cantonese_CuteGirl` voice, `api.minimaxi.com` host with
`api.minimax.chat` fallback — accepting the cloud call for the fastest path to a
working Cantonese voice. A local provider stays open — `04-deferred.md`.

## Staging

1. **Stage 1 — TTS playback + ducking** (`01-…`, `02-…`) — landable now.
2. **Stage 2 — voice-channel receive** — the wake word's prerequisite; no
   unresolved question, but no value alone.
3. **Stage 3 — trained wake word** (`03-…`) — gated on the synthetic→real
   measurement, not on engineering.

Each stage is independently verifiable and independently revertable. Stage 1
is specified in enough detail to implement without further discovery.
