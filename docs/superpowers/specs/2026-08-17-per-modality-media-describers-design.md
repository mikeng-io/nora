---
kind: spec
title: "Per-modality media describers — image, audio, video"
description: "Split media.vision_model into per-modality describers, shipping audio and video INERT behind flags, so enabling either later is a flag plus a model name rather than a code change."
tags: [nora, media, vision, config, design]
timestamp: '2026-08-17'
status: proposed
---

# Per-modality media describers

## Why

Nora can look at an image. She cannot hear a voice note or watch a video, and
this is true **by construction, not by configuration**:

- `nora/infra/vision/client.py` sends its blob as a base64 `data:` URI inside an
  `image_url` content block. That is the only payload shape the module knows.
- its prompt is one authored constant: `"Describe this image. If it contains
  text in it, transcribe that text."`
- `nora/agent/tools.py` turns every non-image away *before* the model call with
  `not_an_image` — «That's not a picture — it's a voice note, a video or a
  file.»

Measured live (Langfuse, 2026-08-15T16:15:57): Miles tagged her on a video asking
「佢係邊，做緊mud」 and she answered 「個 video 我睇唔到」. That is the guard
working as designed, and it is also the ceiling.

So renaming `vision_model` into three fields would produce three knobs, two of
which route into a code path that refuses anything but an image.

## What this change is, and is not

**Is:** the seam. One describer port, three modalities, per-modality model +
enable flag, per-modality prompt, honest per-modality failure reporting.

**Is not:** a new provider, new credentials, or any claim that audio and video
work. Both ship **INERT** — the pattern `[presence]`, `[search]`, `[compute]`
and `[language]` already use in this repo.

**Why inert rather than measured first.** The OmniRoute catalog cannot answer
the capability question. Across all 406 models it advertises:

```
vision      215     attachment  162
audio         0     video         0
```

Zero models carry an `audio` or `video` key **at all**, so the flag vocabulary
does not model those modalities. That is `unavailable`, not `nothing_found`: it
is not evidence that no route can hear — `aug/gemini-3.1-pro-preview` handles
audio and video upstream and is flagged only `vision` here. Rather than design
against a probe we have not run, we build the seam and leave the capability
question to a later measurement, at which point enabling is a flag and a model
name.

## Decisions taken

| decision | choice | why |
|---|---|---|
| scope | split + feature flag, audio/video inert | operator, 2026-08-17 |
| module path | keep `nora/infra/vision/` | least churn; DEC-56's cited paths stay valid |
| config shape | nested `[media.describe.<modality>]` | leaves room for per-modality overrides without a second reshape |
| STT | **excluded**, slot reserved | a different endpoint and a different port — see below |

### Why STT is excluded

Speech-to-text is not a model-name swap away from audio understanding:

| | audio understanding | STT / ASR |
|---|---|---|
| API | `chat.completions`, audio content block | `/v1/audio/transcriptions` |
| prompt | authored, per modality | none, or a language hint |
| output | a description | a verbatim transcript |

Folding STT into `describe.audio` would break the very seam this change exists
to create. It also carries an unanswered safety question: R-18/R-28 identifier
screening currently runs over short captions, and a multi-minute transcript —
in which people say addresses and phone numbers aloud — is a materially larger
egress surface that deserves its own design pass. It lands later as a sibling
`[media.transcribe]`, not as a fourth entry under `describe`.

## Config

`MediaConfig` today carries a flat `vision_*` family (`vision_model`,
`vision_max_tokens`, `vision_reasoning_effort`, `vision_max_bytes`,
`vision_timeout_seconds`) **and** a parallel `sticker_*` family. Stickers are
already, in effect, a fourth describer with its own knobs; this change does not
disturb them.

```toml
[media.describe.image]
enabled = true
model   = "ollamacloud/minimax-m3"

[media.describe.audio]
enabled = false          # INERT — flip and name a model to enable
model   = ""

[media.describe.video]
enabled = false
model   = ""
```

The shared knobs — `vision_max_tokens`, `vision_reasoning_effort`,
`vision_max_bytes`, `vision_timeout_seconds` — **keep their current names and
stay flat on `[media]`**. They are not renamed to `describe_*`: the same
argument that keeps the module at `nora/infra/vision/` applies here, and each
one is cited by name in DEC-56's reasoning and in `derive_vision_timeout`'s
docstring. Only `vision_model` moves, because it is the only one that has to
differ per modality. Per-modality overrides are **deliberately not added yet**:
video will plausibly want a larger byte ceiling and audio a longer timeout, but
neither is measurable while both are off, and a guessed override is a number
nobody can defend. The nested shape is what lets them be added later without
reshaping the section again.

**Every new field is defaulted and every new table optional.** `MediaConfig` is
`extra="forbid"` and its own docstring already warns that a required section
would be "a startup failure on upgrade rather than a feature". This is not
theoretical: on 2026-08-17 a `[language]` block against an image whose `Config`
lacked the field put Nora into a startup crash-loop. Backward compatibility
here is a liveness property, not a courtesy.

`vision_model` is retained as a deprecated alias feeding
`describe.image.model`, so a config written before this change keeps loading.

## Behaviour

### One entry point, dispatched on modality

`describe(modality, …)` replaces the image-only call. The prompt becomes an
authored table keyed by modality — image keeps its current string **verbatim**;
audio and video get their own.

The prompt stays **authored and never model-supplied**, which is the existing
rule and the reason it is a constant today: the description is untrusted egress
(design/14 §2.1), screened on every serve. Selecting a prompt by modality is
derivation from a system-controlled fact and preserves that property.
Conditioning the prompt on what the tagger *asked* — 「佢係邊，做緊mud」 steering
the describer — is explicitly **out of scope**: it is group-authored text
reaching a model whose output egresses back into the room, i.e. an injection
channel, and it needs its own design.

### Three outcomes, not one (R-27)

Today every non-image collapses to `not_an_image`. After the split:

| situation | status | she can honestly say |
|---|---|---|
| flag off, or no model configured | `unavailable` | 「我聽唔到語音」 — I could not check |
| MIME is a file or unknown | `not_supported` | that is not something I can open |
| model returned nothing usable | `nothing_found` | I heard nothing in it |

The first row is the one that matters: a disabled modality is **`unavailable`,
never `nothing_found`**. A wiring gap reported as "there's nothing there" is
exactly the R-27 failure this repo names as an ERROR being served as a PASS.

### The timeout budget stays SHARED — the load-bearing constraint

`derive_vision_timeout` guarantees `budget × per_call < loop_timeout_seconds`,
by dividing `VISION_LOOP_SHARE` by the tool's budget. That invariant is what
stops a hung describer from becoming the agent loop's silent
`invocation_timeout` (R-5) — the failure DEC-56 exists to prevent.

**If each modality took its own budget, three of them could aggregate past the
loop and reopen that hole.** One shared describe budget across all modalities
keeps the existing proof intact and unchanged. This is the single most
important constraint in this document; a split that gets everything else right
and this wrong is a regression, not a feature.

## Testing

- A **shared contract suite** across the three modalities. The repo rule is that
  a port never gains a second implementation — including a fake — without one
  (`tests/contract/`), and this change takes one describer to three.
- A test proving the **disabled path returns `unavailable` and not
  `nothing_found`**, since that is the R-5/R-27 trap.
- A test proving the **aggregate timeout invariant** still holds with three
  modalities configured — i.e. that budgets did not become per-modality.
- A **backward-compatibility test**: a `config.toml` carrying the old
  `vision_model` and no `[media.describe.*]` tables still loads and still
  describes images. This is the crash-loop guard.
- Existing image behaviour must be **unchanged**: same model, same prompt string,
  same cache keys. A described image before and after this change should produce
  an identical result.

## Out of scope

- STT / `[media.transcribe]` (reserved, above).
- Any probe of which routes actually accept audio or video.
- Prompt conditioning on the tagger's question (injection surface).
- Per-modality byte/timeout overrides (unmeasurable while inert).
- Sticker describer changes.
