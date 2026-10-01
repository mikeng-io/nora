---
type: design
title: "Speech formatter — a late, lite call that paces a spoken reply for MiniMax"
description: >
  Nora's voice replies reach MiniMax speech-2.8-turbo as the written chat
  text she sent. This node adds a formatter at the speak seam, off by
  default: a deterministic normaliser (emoji, markdown, stray symbols) and,
  optionally, one cheap model call that may add MiniMax pause markers and
  interjection tags. A code check speaks the model's text only when every
  letter and digit of her reply survives unchanged and in order. Records the
  placement decision, the check, the measured latency and preservation, and
  the open questions about how it sounds.
tags: [nora, discord, voice, tts, minimax, formatter, measurement]
timestamp: '2026-10-01'
edges:
  - {src: 08-speech-formatter, dst: 01-tts-playback-and-ducking, rel: extends, provenance: asserted}
  - {src: 08-speech-formatter, dst: 07-audio-owner-controller, rel: extends, provenance: asserted}
  - {src: 08-speech-formatter, dst: 09-decision-index, rel: motivated_by, provenance: asserted}
---

# 08 — Speech formatter

## 1. The question, and the answer

Operator, 2026-10-01: a formatter that makes her reply fit MiniMax's TTS
requirements (format, tones) — «is it the job of Nora, or a late lite agent?»

**A late lite agent at the speak seam, not Nora's main loop.** Three reasons,
each a property of the code rather than a preference:

1. **Her reply is recorded.** The audit row, the Honcho feed and the notes
   carry her words exactly as sent (`AGENTS.md`, the serialize verb). Markup
   written by her would enter every one of those stores; `<#0.40#>` in a note
   is a defect. Markup added after the reply is final touches only the copy
   that is synthesized, and nothing records that copy but the audio.
2. **Her doctrine is written chat.** Asking her to write two registers at
   once is the optional garnish this repo measured a cheap model never
   choosing (`nora/agent/reaction.py`: 0/80 unprompted GIFs until the choice
   got its own call). The reaction picker is the precedent: a separate cheap
   call after `send_reply`, whose every failure sends her words as written.
3. **Markup is for the ear.** The typed fallback (a voice reply that cannot be
   spoken is posted as text, `DiscordAdapter.send`) must stay her chat text,
   and it does, because formatting happens inside `MiniMaxTtsSpeaker.speak`,
   after `speakable()` and the live-session check and before the vendor call.

## 2. What it may do (MiniMax, read 2026-10-01)

From platform.minimax.cn/docs/api-reference/speech-t2a-http:

- pause `<#x#>`, x in [0.01, 99.99] seconds, at most two decimals, only
  between pronounceable text, never consecutive;
- interjection tags on speech-2.8-hd/turbo only: (laughs) (chuckle) (coughs)
  (clear-throat) (groans) (breath) (pant) (inhale) (exhale) (gasps) (sniffs)
  (sighs) (snorts) (burps) (lip-smacking) (humming) (hissing) (emm) (sneezes);
- `pronunciation_dict.tone`: `original/replacement` entries per request;
- `voice_setting.text_normalization`: the vendor's digit normalization;
- `emotion`: speech-2.8 does not support `whisper`; unset, the model matches
  emotion to the text and the vendor says manual is generally unnecessary.

The formatter may only ADD pauses and allowed tags. Pronunciation entries are
static (`[speech.tts] pronunciation`), not model-written: a model-written
inline pronunciation adds letters, and the check refuses it.

## 3. The check (fail-closed toward her words)

`nora.domain.speech_markup`, deterministic and framework-free:

- `normalise(reply)` removes emoji, markdown markers and stray symbols
  (`* _ # ~ ～ | ^ \``) and never a letter, digit or combining mark; unit
  signs a voice says (° ℃ ℉) stay. Removing `#` also disarms any `<#…#>` her
  own text carried, so the only pauses spoken are ones the check saw.
- `check(base, candidate)` refuses: empty; a malformed or out-of-range pause
  (our bound is 0.01 to 2.00 s); a pause first, last or beside another; more
  than one pause per 3 spoken characters; more than 2 added interjections; an
  added emoji or stray symbol; **any difference in the sequence of letters,
  digits and combining marks** once markup is removed (a tag outside the list
  stays in and reads as an added word); and any difference in the numbers as
  written, sign and inner separators included («3.14», «-5», «19:54», no
  space inside a number). Other punctuation and whitespace may differ.
  Re-applied to the stored draws of §4, the number and mark rules changed no
  outcome.
- Any refusal, timeout, error, truncation or missing instruction speaks
  `normalise(reply)`. A model answer that adds no markup also speaks
  `normalise(reply)`, never the model's copy.

Proven by reverting: see the PR's mutation section.

## 4. Measurement (2026-10-01, norty, offline harness)

Corpus: 68 replies. 26 are every reply Nora sent within 60 s of a voice
utterance (2026-09-17 to 09-30, the whole voice history), 30 her latest typed
replies in the same Discord rooms (12 to 200 characters), 12 invented
Cantonese replies with emoji, markdown, digits and English words. Rows stayed
on norty. Each case drawn 3 times with the gateway cache bypassed
(`x-omniroute-no-cache`; the first run without it returned 0.04 s medians and
identical outcomes across two models, i.e. one reading counted many times).
Same path as production: `build_structured_model` + `ask_text`,
`reasoning_effort = "none"`, temperature 0, the branch's instruction.

| model alias | draws | check passed | refused (fallback) | formatted | latency p50 / p90 / p95 / max |
|---|---|---|---|---|---|
| `nora-gate` | 204 | 202 (99.0%) | 2 `words_changed` | 142 (69.6%) | 0.91 / 1.52 / 1.79 / 3.35 s |
| `nora` | 204 | 203 (99.5%) | 1 `words_changed` | 145 (71.1%) | 0.88 / 1.48 / 1.59 / 3.17 s |

- The two refusals on `nora-gate` were real deletions of one character each
  (a particle and an honorific): the check is not decorative.
- 13 of 68 cases had draws that disagreed (formatted vs unchanged), so these
  are rates, not per-case verdicts.
- By length (`nora-gate`): 0-11 characters, 5 of 39 draws formatted and 33
  unchanged, which is why `min_chars = 12` skips the model there; 121+
  characters, p95 3.02 s.
- The model never used an interjection tag (0 in 142 formatted answers); it
  adds 2.2 pauses per formatted reply on average, mostly at clause ends.
- With `timeout_seconds = 2.0`, 137 of the 142 formatted answers arrive in
  time; 1.5 s keeps 129.

**Chosen:** `nora-gate`, the relevance gate's own cheap combo (no new
provider path). The two aliases measured the same within noise; the gate's
combo is chosen so a formatter call never competes with, or changes along
with, the tag lane (ADR 0006's argument for keeping lanes apart), timeout 2.0 s,
`min_chars = 12`, `max_chars = 200`. Cost of the choice: on a formatted
reply the voice starts about 0.9 s later at the median and up to 2.0 s later
in the worst case, against a wake-to-first-speech median of about 6 s today.

## 5. A/B audio (UNVERIFIED by ear)

Six invented Cantonese replies, each synthesized four ways with the live
voice (`Cantonese_CuteGirl`, speech-2.8-turbo, speed 1.0, `language_boost`
`Chinese,Yue`): her text as written vs formatted by `nora-gate`, and
`emotion` unset vs `fluent`. One synthesis per cell, so durations are single
readings (a repeated probe of one text moved by several hundred ms).

| arm | total audio over the 6 (MiniMax `audio_length`) |
|---|---|
| raw, emotion unset | 34.3 s |
| raw, `fluent` | 31.9 s |
| formatted, unset | 40.8 s |
| formatted, `fluent` | 38.4 s |

What is measured: every formatted text passed the check; the formatter
added 1 to 3 pauses per sentence (0.3 to 0.4 s each, about 3.8 s in all) and
no interjection, often in place of a comma; the formatted arms are about
6.5 s longer in total, more than the 3.8 s of pauses added (single readings);
`fluent` is about 2.4 s shorter than unset. All 24 syntheses returned audio,
including `fluent` on speech-2.8-turbo. What is NOT measured: whether any of
it sounds better. The files are for the operator to listen to; they are not
in the repo.

## 6. What this does not decide

- Whether the paced audio sounds better. Nobody has listened under this
  branch; the A/B files are for the operator.
- Whether `emotion` should stay `fluent` (live) or go unset. The live default
  is unchanged.
- Any static `pronunciation` entries. None are configured.

The reading that would reopen the placement: a measured need for markup that
depends on the conversation rather than on the reply (the formatter reads the
reply alone), or evidence that the 0.9 s median costs more than pacing buys.
