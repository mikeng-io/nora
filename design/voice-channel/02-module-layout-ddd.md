---
type: design
title: "Voice Channel — DDD Module Layout"
description: "Where each piece lives across domain / infra / app / platform, mapped onto the STT path that already ships — the same five layers, the same conventions, no new pattern."
tags: [nora, ddd, architecture, ports, layers, module-layout]
timestamp: '2026-09-14'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 01-tts-playback-and-ducking, rel: extends, provenance: asserted}
---

# Voice Channel — DDD Module Layout

## The rule this follows

**Extend the existing pattern; invent only where reuse is impossible.** The
STT capability (spec 2026-08-21) is the template, because it is the same kind
of thing: a speech capability that is optional, defaulted, inert, and never
load-bearing. TTS mirrors it layer for layer.

## The five layers

### `domain/` — pure contracts, zero I/O

- **`ports.py`** — add `Speaker` Protocol beside `Transcriber` (`:917`) and
  `SpeakResult` beside `SttResult` (`:939`).
- Nothing else. `domain/` never learns what MiniMax is, and never learns what
  Discord is.

**Why here and not in the Discord adapter:** `domain/ports.py` is where this
repo declares "a capability that exists independent of who provides it". A
`Speaker` that lived in `platform/discord/` would make saying text out loud a
Discord-only idea, and the Signal voice-note path (`04-deferred.md`) would
have to re-declare it.

### `infra/` — one provider, one adapter

- **`nora/infra/tts.py`** (new) — `MiniMaxTtsSpeaker`. Sibling of
  `infra/speech.py`'s `SenseVoiceTranscriber`, same docstring posture: one
  endpoint, never load-bearing, never raises.

**Why a new file rather than appending to `speech.py`:** `speech.py`'s
docstring and `__all__` are scoped to transcription; MiniMax is a different
dependency (an outbound HTTPS POST to a cloud vendor vs a local HTTP POST to
a service we run) with a different failure surface. One module owning both an
inbound local service and an outbound cloud call is two reasons to change.

### `app/` — wiring only, no logic

- **`composition.py`** — construct `MiniMaxTtsSpeaker` beside
  `SenseVoiceTranscriber` (`:1495`), gated on `[speech.tts].enabled`.

**Why this is the only change here:** the composition root is the one place
allowed to know which adapter is real. Everything downstream receives a
`Speaker | None` and must behave correctly with `None`.

### `platform/` — Discord-specific mechanics

- **`platform/discord/duck_source.py`** (new) — `DuckSource`.
- **`platform/discord/voice_session.py`** — `DiscordVoiceSession.speak()`.
- **`platform/discord/music_player.py`** — `VoiceSession` Protocol gains
  `speak` (the port declaration lives here, next to its consumer).

**Why the mixer is in `platform/` and not `infra/`:** it is written against
`discord.AudioSource` and `discord.opus.Encoder`'s frame size. It is not a
provider adapter — it is the Discord voice mechanic, exactly like its sibling
`crossfade.py`. `infra/` means "an external thing we call"; the mixer calls
nothing.

**Why `speak` is on `VoiceSession`:** the session owns the voice connection
and the one source Discord reads, and only it knows whether music is currently
playing — the precondition for ducking. See `01-…` §4.

### Dependencies — one addition, declared

No new dependency. MiniMax TTS is a plain HTTPS POST via `httpx` (already a
repo dependency for the LLM path) — no SDK, no `uv add`. The CN plan key is
`MINIMAX_CN_API_KEY` (already present in `~/.hermes/.env`, where Hermes' own
MiniMax script reads it); the nora container must pass it through and allow
egress to `api.minimaxi.com` (with `api.minimax.chat` as documented
fallback host). "Works in voice-lab" and "works in the deployment image"
are different facts, and only the second is shippable.

### `deploy/` — no new service

STT needed `deploy/stt/` because it is a **local model server** (FunASR/
SenseVoice) that must stay on-box. MiniMax TTS is a plain outbound HTTPS
POST to a cloud vendor; there is no service to run, no container, no port.

**If a local TTS provider is ever adopted, `deploy/tts/` appears then**,
mirroring `deploy/stt/`'s shape (`Dockerfile`, `server.py`, `smoke_test.py`).
Not now — a service that does not exist should not be scaffolded
speculatively.

## Resulting tree

```
nora/
  domain/
    ports.py                    ← + Speaker, SpeakResult
  infra/
    speech.py                   (unchanged — SenseVoiceTranscriber)
    tts.py                      ← NEW  MiniMaxTtsSpeaker
  app/
    composition.py              ← construct the speaker, gated
  platform/discord/
    crossfade.py                (unchanged)
    duck_source.py              ← NEW  DuckSource
    music_player.py             ← VoiceSession + speak()
    voice_session.py            ← DiscordVoiceSession.speak()
```

Three files added, three touched. No new top-level package, no new layer, no
new convention.

## Clean Code notes — the names carry the design

- **`DuckSource`, not `MixerSource` or `AudioMixer`.** The class does one
  thing: lower one source under another and restore it. `Mixer` would invite
  EQ, panning, and layering to move in.
- **`speak`, not `play_tts` or `play_speech`.** It reads as the voice-channel
  verb, matching `play/pause/resume/stop` on the same protocol.
- **`SpeakResult`, not `TtsResult`.** The port is `Speaker`; the result names
  the act, not the technology. `TtsResult` would leak the acronym into the
  domain and age badly if the provider stops being TTS-shaped.
- **`duck_gain` / `duck_ramp_ms`, not `speech_volume` / `fade_time`.** Names
  the behaviour being controlled, not the mechanism.
- **One reason to change per module.** `tts.py` changes if the TTS provider
  changes. `duck_source.py` changes if the mixing law changes.
  `voice_session.py` changes if the Discord voice API changes. None of these
  forces the others.

## What the interface boundaries buy

- `domain/` can be tested with no Discord and no network — the mixer's
  arithmetic test (`01-…` §6.3) needs neither.
- A local TTS provider is a **new file in `infra/`** and one line in
  `composition.py`. Nothing in `platform/` notices.
- The Signal voice-note path can reuse `Speaker` without touching Discord —
  that is the whole point of the port living in `domain/`.
