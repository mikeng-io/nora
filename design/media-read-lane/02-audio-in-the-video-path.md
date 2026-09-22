---
type: design
title: "Audio in the Video Path — the `-an` that throws away half the evidence"
description: "Every video Nora sends is silent by construction: `_video_args` passes `-an` unconditionally. 9 of 11 stored clips carry an audio stream. The operator has directed that audio be enabled, overriding the earlier no-verbatim-transcript decision."
tags: [nora, media, video, audio, stt, egress, pii, superseded]
timestamp: '2026-09-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 02-audio-in-the-video-path, dst: 04-timeout-and-models, rel: depends_on, provenance: asserted}
---

# Audio in the Video Path

## 1. The defect

```
nora/infra/vision/preprocess.py:195        "-an",
```

`_video_args` passes `-an` with **no flag, no branch, no config key**. Every chunk
of every video Nora has ever described was silent. She has only ever been able to
*see* video, never *hear* it — and nothing in the code says that was a choice.

**The audio is really there.** Of the 11 stored clips in
`state/signal-cli/attachments/`, **9 carry an `aac` audio stream**; only 3 have
none. This is the common case, not an edge case.

## 2. What is being thrown away — measured

Same 35-second clip, same 720p/24fps/CRF26 geometry, one variable changed:

| | prompt tokens | seconds | what it produced |
|---|---|---|---|
| `-an` (today) | 8,172 Δ | 29.6 | visual + on-screen text only |
| audio intact | 8,503 | 43.8 | **+ verbatim Cantonese dialogue, timecoded** |

The audio version produced:

> **00:00–00:04** Male (Jason): 「…都好正啊」 / Female Host: 「多謝」 / Male: 「好彩係360…」
> **00:09–00:15** Female Host: 「今日我地後台唯一比Jason既Brief就係…」

It also **named the performer** (Jason Cortez / 陳詠謙) and recovered signage the
silent version missed (`GONDOLA`). Cost: **~289 tokens, +3.5 %.**

On a 7 s concert clip with audio, the model transcribed song lyrics and crowd
noise, with timestamps.

## 3. Standalone audio also works

A real Signal voice note (`.m4a`, 8.1 s) through the same provider via
`input_audio`:

```
prompt_tokens=89  8.8s  →  "male speaking Cantonese in an aggressive,
                            threatening, confrontational tone… issuing a
                            warning or threat"
```

Correct language, correct tone, correct register. So `[media.describe.audio]`
— `enabled = false` today (`config.toml`) — is **fully wired and inert**. The
`_AUDIO_FORMATS` table (`client.py:112`) already maps `m4a`/`mp3`/`wav`/`ogg`/
`opus`/`webm`/`flac`, and `_content_block` (`client.py:179`) already builds the
`input_audio` block. Enabling is a config change plus the prompt work below.

## 4. 🔴 This OVERRIDES a prior decision — recorded, not silently reversed

The operator's direction is explicit: 「audio 亦都 enable 佢啦，直接啟動佢 override
之前個 decision」. Two prior decisions are being overridden, and both must be
marked, not deleted:

**(a) The video prompt deliberately excludes speech.**
```
nora/infra/vision/client.py:84-88   "video": (
    "Describe what happens in this video, and transcribe any text on screen. "
    "If people speak, say briefly what it is about rather than writing out "
    "what was said.")
```
The comment above it says speech is *"deliberately summarised rather than
transcribed"* (the same block notes that verbatim belongs in a future
`[media.transcribe]` with "its own egress analysis" — see §5, which is exactly
that concern surfacing in the original design).

**(b) Issue #117 explicitly rejected STT-on-video as out of scope.**
Recorded at `preprocess.py:186-190` and formalised in **DEC-161** as *"`-an` stays
so the chunks are silent: audio extraction plus STT was proposed and rejected as
scope."* Note the *reasoning* there was scope discipline, not a safety or quality
objection — so overriding it on operator direction is legitimate. **The scope
argument is not being refuted, it is being superseded by a later decision**, and
DEC-161 must carry a `DEPRECATED IN PART 2026-09-22` prefix pointing at DEC-195
(see `05-decisions-and-index.md §2`).

**What replaces it:** a video prompt that asks for speech to be transcribed when
it is the substance of the clip, and summarised when it is not. The `audio`
prompt's prohibition («Do NOT write out a word-for-word transcript») is likewise
revised — but see §5, which is the part that needs care.

## 5. ⚠️ Blocking check — the egress/PII surface widens

**This is the one part of the bundle that can be wrong in a way that matters, and
it is not yet verified.**

Transcribed speech is a **new content class** in Nora's answers. Before it
ships, one question must be answered with evidence:

> Does the existing identifier/egress screen cover model-generated speech
> transcripts, or only literal text the humans typed?

The screens named in this repo (`scan_identifiers`, `dream_screen`, the
note-tier egress screen) were built against *authored* content. A transcript is
derived from untrusted media and arrives as a model's string — structurally the
same shape as a description, which the screens already handle, but that is an
inference. **Verify before enabling, do not assume.**

Concretely, the risks audio newly admits:

- A phone number, address, or account number **spoken** in a clip now reaches
  the description, where before it could not.
- Cantonese speech about a named third party becomes quotable text.
- The `[media.describe.audio]` prompt's current guard against repeating
  «phone number, address or account number» is prompt-level, not a screen —
  prompt instructions are not a security boundary.

**Recommended shape:** route the transcript through the same screen the
identifier path uses *before* it enters `body`, and add a test that a spoken
digit-string does not survive into a rendered description. If that proves
infeasible, the fallback is to keep transcription on for the *video* lane (where
speech is ambient and the ask is "what is this clip about") and leave
`[media.describe.audio]` off — a narrower override. **Operator gets this choice
with the finding attached; it is not for the implementer to decide.**

## 6. Prompt changes required

Both modality prompts are authored constants near `client.py:59`:

| prompt | today | change |
|---|---|---|
| `video` | summarise speech | transcribe speech when it carries the clip; keep on-screen text |
| `audio` | gist only, **no verbatim** | gist **plus** verbatim where it is the point |

The `video` prompt is the one that runs for the Instagram lane, so it is the
higher-consequence of the two.

## 7. Cost

Audio adds ~3.5 % tokens on a video read (measured) and a large share on a
standalone voice note. Both are negligible against the video token totals
(13,166 for a 57 s clip). The real cost is **latency** — 29.6 s → 43.8 s on the
measured clip — which is why this bundles with `04-timeout-and-models.md` rather
than shipping alone: **enabling audio on the current 36 s budget would make the
lane fail more often, not less.**

Also: `VIDEO_CRF = 26` (`preprocess.py:69`) is a *video* quality knob. Re-encoding
audio should specify its own codec and bitrate explicitly (the benchmark used
`-c:a aac -b:a 64k`) rather than inheriting ffmpeg's default, which differs by
container and would make token cost depend on the input format.
