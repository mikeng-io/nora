---
type: design
title: "Media Read Lane — Overview"
description: "Four interacting changes to how Nora reads video and audio: concurrent chunk reads, an audible video path, a third describer provider for MiMo v2.6 Flash, and a per-call vision budget that measured latency already exceeded."
tags: [nora, media, video, audio, vision, opencode-go, mimo, timeout, concurrency]
timestamp: '2026-09-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# Media Read Lane — Overview

## Why

Miles's directive (2026-09-22), after a benchmark session that reversed two of my
conclusions:

> 「冇問題，我覺得你四個同步做，唔使分開獨立去做。你 audio 亦都 enable 佢啦，直接啟動佢
> override 之前個 decision。」

So this is one change, not four, because the four parts interact:

| Change | Why it cannot standalone |
|---|---|
| **Concurrent chunks** | Changes the wall-clock envelope the timeout is derived against |
| **Audible video** | Raises per-chunk latency (~+50 %) — feeds into the same budget |
| **OpenCode Go lane** | Where the audio-capable model lives; the lane is the vehicle |
| **Raised timeout** | Correctness fix that the first three make *more* urgent, not less |

Doing the timeout first and the rest separately would mean measuring the budget
twice. Doing them together means one arithmetic, one review, one deploy.

## What this change is, and is not

**Is:** a change to Nora's *media describer* lane — the machinery that turns bytes
into a description. Five files of production code plus config, no schema change,
no new table, no migration.

**Is not:** a change to *retrieval*. Fetching an Instagram Reel or Story is
`design/instagram-adoption/` — a different bundle, different work. This bundle
begins the moment bytes exist in Nora's custody and end when a description exists.
The two meet at `media_fetch`, and this bundle does not alter its contract.

**Also is not:** a change to the `stt` service or voice-note ingest. That is a
separate lane with its own provider (`design/voice-channel/06-stt-provider-decision.md`).
Audio *inside video* is what this bundle makes audible; the standalone voice-note
path is untouched.

## The measurement this is built on

Everything here traces to a live benchmark against Nora's own stored Signal
attachments, preprocessed through Nora's own ffmpeg geometry. Harness and raw
JSON: `~/workspace/nora-bench/`. Two rounds:

**Round one (wrong).** I benchmarked video candidates *through OmniRoute* and
concluded MiMo v2.6 Flash had no video capability at all.

**Round two (corrected).** Miles asked «are you bypassing omniroute?» — and the
answer was no, which invalidated the method. Against **OpenCode Go's own
endpoint**, MiMo v2.6 Flash sees video fine, at every clip size:

| clip | bytes | seconds | media Δ tokens | finish |
|---|---|---|---|---|
| 7 s | 0.36 MB | 8.5 | +1,589 | stop |
| 20 s | 0.86 MB | 22.5 | +4,767 | stop |
| 35 s | 2.14 MB | 29.6 | +8,172 | stop |
| 57 s | 2.50 MB | 21–56 | +13,166 | stop |

The lesson is recorded as a rule in `05-decisions-and-index.md`: **never benchmark
a candidate through a router that is itself a known transformer of the payload.**
Using OmniRoute's own catalogue to judge a model OmniRoute was mishandling is
circular reasoning, and it produced a confident wrong answer.

## Status checkpoint

| Item | State |
|---|---|
| Benchmark | ✅ done, both rounds, raw data retained |
| Chunk concurrency | designed here, unbuilt |
| Audio in video path | designed here, **overrides a prior decision** — see 02 §4 |
| OpenCode Go lane | designed here, unbuilt; kimi lane is the exact precedent |
| Timeout raise | designed here, unbuilt; **fixes a defect that exists on master today** |
| Adversarial review | ❌ not dispatched |
| Release fragment | ❌ not written |
| DEC row | ❌ not yet added to `design/09-decision-index.md` (see 05) |

## Reading order

1. **01-chunk-parallelism.md** — the concurrency change and its ordering guarantee.
2. **02-audio-in-the-video-path.md** — the `-an` removal and the overridden decision.
3. **03-opencode-go-provider-lane.md** — the third provider, header, and credential.
4. **04-timeout-and-models.md** — the arithmetic, and why it is a bug today.
5. **05-decisions-and-index.md** — DEC rows, the superseded-decision register,
   rules, and the test obligations.

## Out of scope

- Retrieval of Instagram media (see `design/instagram-adoption/`).
- Concurrent *tool calls* within one agent turn (stated as an open question in 05).
- Per-modality chunk geometry.
- The standalone voice-note/STT lane.
- Image-lane model choice beyond confirming what already ships (see 04 §5).

## Open questions

1. **Does audio-bearing video need a different chunk size?** A 90 s chunk at
   43.8 s latency is close to a 48 s derived budget; shorter chunks with audio
   would cut per-call latency but raise the call count. Not measured — a spike,
   not a guess, per the repo's measurement convention.
2. **Will Strands issue concurrent `tool_use` blocks?** If it does, three
   Instagram stories in one turn parallelise for free. Untested.
3. **Does the egress screen cover speech inside a video description?** The
   transcript is model-generated text derived from untrusted media, so the
   existing `dream_screen`/identifier screen *should* apply — but "should" is
   not verified for this new content class. Flagged as a blocking check in 02 §5.
