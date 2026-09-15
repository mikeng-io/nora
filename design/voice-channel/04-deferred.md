---
type: decision
title: "Voice Channel — Deferred, With Evidence Bars"
description: "What was parked and the concrete evidence that would justify building it — tripwires, not a wishlist. Includes one path explicitly rejected by Miles, recorded so it is not re-proposed."
tags: [nora, voice, deferred, backlog, decision]
timestamp: '2026-09-14'
edges:
  - {dst: 00-overview, rel: motivated_by, provenance: asserted}
---

# Voice Channel — Deferred, With Evidence Bars

Following `design/discord-music/04-deferred.md`'s precedent: each item names
the evidence that would justify building it. Nothing here is a plan.

## 1. Wake word — deferred pending a real-audio probe

**Evidence bar:** a probe on **live Discord audio with music playing
underneath** shows acceptable false positives. The current synthetic probe
passes (AUC 0.94–1.00, 0 FP/150) but measures TTS→TTS, not the deployment
channel. See `03-wake-word-training.md`.

## 2. Live STT of arbitrary voice-channel speech — REJECTED, do not re-propose

**Miles rejected this path explicitly on 2026-09-14.** Recorded here so a later
session does not helpfully reinvent it.

The rejected shape: Nora joins the voice channel and transcribes *everything*
said, as a passive ambient listener. It was considered in the same discussion
as the Siri-style ask and **not chosen** — the accepted design is
wake-gated, not always-listening.

**Why it is worth writing down rather than dropping:** it is the *obvious*
next idea once STT exists and Nora is already in the channel, so it will
recur. It also differs from the accepted design in a way that matters:
always-on transcription turns every casual side-conversation in a shared
channel into stored text. That is a privacy posture, not an implementation
detail, and it is **not** what was agreed.

**Evidence bar to revisit:** Miles asks for it directly. An argument for it
is not evidence.

## 3. Barge-in — deferred

**What:** interrupting Nora mid-sentence — she stops speaking when a human
starts. The Siri behaviour in its complete form.

**Why deferred:** it requires reliable human-speech detection *while Nora is
speaking into the same channel*, i.e. the receive path plus echo
cancellation of Nora's own TTS. Both are stage-2+ concerns and the echo
problem has no measured solution yet.

**Evidence bar:** stage 2 receive lands, and a probe shows Nora's own TTS is
distinguishable from a human voice in the same stream.

## 3b. TTS provider — MEASURED and DECIDED 2026-09-14

Evidence gathered by direct native API calls against `api.minimaxi.com` (not
spec sheets). Key: `MINIMAX_CN_API_KEY` — already present, because Hermes
already runs MiniMax Cantonese TTS (`~/.hermes/scripts/minimax-cn-tts.py`,
`speech-2.8-hd`).

**The finding that matters: the voice is the lever, not the model tier.**
MiniMax ships **native Cantonese voices** (7 found via `POST /v1/get_voice`):
`Cantonese_GentleLady`, `Cantonese_ProfessionalHost（F/M)`, `Cantonese_KindWoman`,
`Cantonese_CuteGirl`, `Cantonese_PlayfulMan`, plus `港普` (HK-accented
Mandarin). The *current* Hermes config uses `female-shaonv` — a **Mandarin**
voice — with `language_boost: Chinese,Yue` bolted on. Swapping to a native
Cantonese voice is a **config-value change** (`[speech.tts].voice`), not code.

**`language_boost` matters more than turbo-vs-hd.** Every sample round-tripped
through local SenseVoice (`language="yue"`):

| variant | boost | Cantonese markers kept |
|---|---|---|
| speech-2.8-turbo | `Chinese,Yue` | 3/3 — 我系…唔该 |
| speech-2.8-turbo | `auto` | 2/3 — 我**是**…唔该 (係 lost) |
| speech-2.8-hd | `Chinese,Yue` | 3/3 |
| speech-2.8-hd | `auto` | 1/3 — 我**是**…**嗯**该 (two failures) |
| edge-tts zh-HK (all 3 voices) | n/a | 3/3 |

`auto` mis-renders Cantonese function words (係→是, 唔該→嗯該). The boost must
be **hard-set**, not heuristic.

**Latency (streaming TTFA — what a voice bot feels):** MiniMax both tiers
≈ **560–670 ms**; edge-tts zh-HK ≈ **150–330 ms**. Both acceptable.

**Cost (MiniMax paygo):** turbo **$60/M chars**, hd **$100/M**; measured CJK
billing runs ≈1.8× input chars. Typical 26-char reply bills 47 chars ≈
**$0.0028**; 1,000 replies ≈ **$2.82**. edge-tts = **$0**.

**DECIDED by Miles 2026-09-14:** `speech-2.8-turbo` + native Cantonese voice
**`Cantonese_CuteGirl`**, `language_boost: "Chinese,Yue"` hard-set. hd's
premium (¥3.50 vs ¥2.00 per 10k chars — 1.75x) buys nothing measurable at
reply lengths (measured: spectral centroid 3,942 Hz vs 3,478 Hz; only a 4.5 pp
8–16 kHz energy difference; wave-align correlation indistinguishable from the
same-model control). Video generation is **parked** by Miles (2026-09-14) — the
plan's video bucket exists but H3 is excluded from Token Plan, and usage was
unverifiable. edge-tts remains the documented offline/fallback route — free and
faster — but is NOT the v1 provider.

**DNS issue RESOLVED 2026-09-14:** `api.minimaxi.com` was returning `0.0.0.0`
via the host's Control D resolver (`ctrld` v1.5.2). `systemctl restart ctrld`
cleared the block immediately (96 s stable test, 12/12 ok) — the earlier
"restart does nothing" claim was wrong: it works, it just needs sudo/polkit.
Residual risk: ctrld's upstream was flapping (`all [upstream.0] endpoints
failed`), which may recur; `api.minimax.chat` is the documented fallback host
(verified same backend, same 303 voices, CN key works). `api-bj.minimaxi.com`
fallback inside `~/.hermes/scripts/minimax-cn-tts.py:96` is DEAD (HTTP 404)
and should be replaced with `api.minimax.chat`.

## 4. Local (non-cloud) TTS provider — deferred

**What:** replace the MiniMax cloud call so reply text never leaves the box,
matching STT's local-only posture.

**Why deferred (accepted trade, not an oversight):** MiniMax TTS is an
outbound HTTPS POST to a cloud vendor (CN plan `sk-cp-` key). Miles confirmed
this for v1 (2026-09-14), accepting the outbound text call for the fastest
path to a working Cantonese voice. See `00-overview.md` for why the asymmetry
with STT is defensible — the content differs in kind.

**Evidence bar:** a local provider reaches subjectively comparable Cantonese
quality **or** a private-inference requirement appears that the current
posture cannot meet. Quality is the real gate; privacy alone is not, because
the content is already fit to be spoken aloud in a shared channel.

## 5. Per-speaker voice / voice cloning — deferred

**What:** a voice per Discord user, or a cloned voice.

**Why deferred:** no requirement exists, and voice cloning carries a consent
question that a group channel makes worse.

**Evidence bar:** an actual request, with the consent question answered first.

## 6. Voice-channel session persistence — out of scope

Music persistence already exists (`design/discord-music/03-state-and-
persistence.md`, `06-session-persistence.md`). Any voice-*speech* session
state (e.g. "Nora is mid-answer, don't start a new one") is **not** designed
here and would attach to that existing store rather than a new one.

**Evidence bar:** a real failure caused by lost speech state across a
restart. Speculative state machines for a feature that does not exist yet are
exactly what this section exists to prevent.
