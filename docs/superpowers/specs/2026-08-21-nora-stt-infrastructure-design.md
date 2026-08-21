---
kind: spec
title: "Nora STT — local Cantonese speech-to-text infrastructure"
description: "A local SenseVoice (FunASR) OpenAI-compatible transcription service as a first-class docker-compose member, so Nora can understand Cantonese voice notes without sending audio off the box. This spec covers the INFRASTRUCTURE half only (image, compose service, endpoint, smoke path); wiring Nora's [media.describe.audio] onto the endpoint is the deliberately separate follow-on."
tags: [nora, spec, stt, asr, sensevoice, cantonese, infra]
timestamp: '2026-08-21'
status: proposed
derives_from:
  - ARCHITECTURE.md
  - deploy/drop/ (the "separate app in this repo" shape)
---

# Nora STT

## 0. What this document is

Nora's `[media.describe.audio]` config block exists (`config.example.toml`) but
is `enabled = false` with an empty `model = ""`. A group member sending a voice
note gets "I couldn't check that" — the wiring exists, the model does not.
This spec stands up the model side as its own compose service, following the
repo's established "a different app that lives in the same repo" shape
(`deploy/drop/`): own image, own dependency set, private-network-only.

It deliberately specs **infrastructure only**. Wiring `[media.describe.audio]`
to call the new endpoint, deciding whether the call goes through OmniRoute or
directly to the service, and the TTS half (outbound speech) are separate
follow-ons, named in §7 — not filled in here with invented answers.

## 1. Why SenseVoice (and why not the alternatives)

Measured in `~/workspace/voice-lab` (2026-08-21, this host, CPU-only):

| candidate | Cantonese verdict |
|---|---|
| SenseVoiceSmall (FunASR) | native `yue` LID; preserves 口語 particles 唔/嘅/喺/哋; RTF ≈ 0.04 (1s audio → ~40ms) |
| faster-whisper large-v3 | mangles Cantonese toward Mandarin orthography; loses 口語 |
| Grok / xAI STT | supported-languages list has no `yue` — "additional languages, varying accuracy" |
| S1-mini | not an ASR at all; English-only transcript normalizer |

SenseVoiceSmall is Apache-2.0, ~936MB, runs fine on this host's CPU
(Ryzen 7 PRO 6850U, no CUDA). Cantonese is a first-class language in its
training, unlike every cloud STT endpoint available to this setup.

## 2. Scope

In scope (this spec):

- `deploy/stt/` — a separate Python app (FastAPI + FunASR + OpenCC),
  OpenAI-compatible `/v1/audio/transcriptions`, `/v1/models`, `/health`.
- The `stt` compose service on the private `nora` network.
- Smoke test (`deploy/stt/smoke_test.py`) — health, model identity, and one
  real transcription round-trip.
- Model weights cache as a named volume (first boot downloads ~936MB once).

Out of scope:

- Nora calling the endpoint (`[media.describe.audio]` wiring — §7).
- TTS / outbound speech entirely.
- Streaming / real-time ASR.
- GPU acceleration (this host has no CUDA; the service is CPU-first).

## 3. Why a separate image (not a nora:latest dependency)

Same reasoning as `drop` (spec 2026-08-12 §3): SenseVoice pulls PyTorch +
FunASR + ModelScope into the image. Nora's own image is deliberately slim and
her `uv sync --frozen --no-dev` pins HER lockfile; cross-pollinating the two
would let a speech dependency drive Nora's own dependency set. The FunASR
stack stays inside `deploy/stt/`'s own pyproject/uv.lock. The `stt` container
has no need to know Nora's secrets or schema; Nora's container needs no
Python package for speech.

## 4. Deployment posture

- `stt` joins the **private `nora` network only**; it publishes **no host
  port** — `stt:8000` is reachable only by co-networked containers
  (Nora, presence, drop). Same posture as `signal-cli` and `drop`'s internal
  listener: a service the group's voice notes hit should not be addressable
  from the host or the internet at all.
- Image build uses `network: host` (operational constraint 1; the Dockerfile
  runs `apt-get install curl` for the healthcheck).
- Model weights (`~/.cache/modelscope` inside the container) are a named
  volume `stt-modelscope-cache` so `docker compose restart`/`up` does not
  re-download the 936MB.
- Healthcheck: `curl /health` with a long `start_period` (240s) to cover the
  first-boot model download.
- Run: `docker compose up -d stt` — the service is ready for Nora before any
  config wiring flips `[media.describe.audio]` on.

## Smoke test

    docker compose up -d --build stt
    python deploy/stt/smoke_test.py --audio /path/to/yue.mp3

The smoke test does the three independent checks the FunASR production
guidance names: (1) health, (2) model identity, (3) a real transcription
round-trip. Without `--audio` it runs checks 1–2 and skips 3.

## Output contract

`POST /v1/audio/transcriptions` returns OpenAI-compatible JSON:

    {"text": "喂，你今日得唔得閒呀？我想約你去食飯。"}

- Input: multipart form-data `file` + `model` (default `sensevoice`);
  `language=yue` optional hint (default `auto` — SenseVoice does LID).
- Output is **Traditional Chinese** (OpenCC `s2t` in the service), matching
  Nora's `yue-Hant-HK` doctrine — the caller never has to think about the
  written-variant gap.
- `response_format=verbose_json` returns `segments` with timestamps.
- `GET /v1/models`, `GET /health` for OpenAI-client compatibility and ops.

## 6. Files

- `deploy/stt/Dockerfile`
- `deploy/stt/pyproject.toml` / `deploy/stt/uv.lock`
- `deploy/stt/server.py` (FastAPI app, SenseVoice + OpenCC)
- `deploy/stt/smoke_test.py`
- `deploy/stt/.dockerignore`
- `docker-compose.yml` (new `stt` service + `stt-modelscope-cache` volume)

## 7. Out of scope / next steps

1. **Wiring**: `[media.describe.audio] enabled = true; model = "stt:8000"` —
   how Nora's media pipeline calls the endpoint (direct HTTP vs through
   OmniRoute), timeout/kb budgets, and whether `[media.describe.audio]`'s
   model string can name a plain URL. Needs an issue on `mikeng-io/nora`
   before implementation (CLAUDE.md design-backlog rule).
2. **TTS** — outbound speech (so Nora can *speak* Cantonese, not just
   understand it). Separate part per operator's split.
3. **Real-voice evaluation** — voice-lab used edge-tts synthesized Cantonese;
   a real 口語 voice note sample should be transcribed and checked before
   cutting over.
