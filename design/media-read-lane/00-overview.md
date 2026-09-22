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
| Audio in video path | designed here, **overrides DEC-161 and DEC-114(3)** — see 02 §4–5 |
| OpenCode Go lane | designed here, unbuilt; kimi lane is the near-precedent (see 03 §3) |
| Timeout raise | designed here, unbuilt; **fixes a defect that exists on master today** |
| Adversarial review | ✅ **round 1 dispatched (Codex, read-only) — REQUEST CHANGES**; all findings addressed |
| Release fragment | ✅ written, both gates green |
| DEC row | ❌ DEC-195 not yet added to `design/09-decision-index.md` (see 05) |
| **Measurement obligations** | ❌ **open — see 05 §4. The recommended settings are NOT yet validated at production shape; do these before implementing.** |

### What the adversarial review changed

Round 1 returned **REQUEST CHANGES** with 2 BLOCKING, 5 MAJOR and 3 MINOR findings.
The substantive corrections, all applied in place with the original text retained so the
change is visible:

- **The timeout proof was over the wrong unit.** `budget × derived < loop` bounds
  *model calls*; a tool invocation with concurrent windows takes `ceil(N/C)` waves. One
  10-window clip is up to 3 waves; three such reads in a turn is 432 s against a 240 s
  loop. A pre-existing defect the bundle had not exposed. See `04 §3a`.
- **48 s was below this bundle's own measurements** (46.2 s K3 / up to 56 s MiMo on a
  57 s clip). **Now measured properly at production shape — and it is worse than that:**
  n=16 across four sources gives **min 28.5 s / median 41.6 s / max 120.8 s**, with
  **6/16 exceeding 48 s**. The same file returned 28.5 s and 120.8 s, so the tail is
  upstream variance rather than payload cost. See `04 §3b`.
- **The egress blocker was the wrong question.** The screen *does* run; what it
  *detects* is the issue — `egress.py` deliberately skips bare digit runs and its E.164
  pattern requires a `+`. The "keep it on the video lane" fallback I proposed was not a
  mitigation at all. And **DEC-114 clause (3) is a safety decision I had mislabelled as
  scope** — a materially worse error than a wrong number. See `02 §5`, `05 §2`.
- **Two prompt families, not one.** Editing `client.py` alone implements nothing for
  automatic comprehension (`COMPREHEND_QUESTIONS`), which is where Instagram media is
  read. See `02 §6`.
- **Already-comprehended media is never re-read** — the headline benefit would not
  appear on anything already in the store. See `02 §8`.
- **A pre-existing coverage defect:** a capped read with a *middle* gap claims it
  «watched the first N» — `covered_seconds` is a duration sum, not an interval set. See
  `01 §5a`.
- **A per-read semaphore bounds nothing globally** — ingest already runs 4 concurrent
  jobs, so the true ceiling is 4 × concurrency. See `01 §3`.
- **`create_client` has no header seam** — the "follow the kimi precedent" recipe as
  written would never send the mandatory header. See `03 §3`.

**What the review confirmed as correct:** DEC-161's two clauses are quoted accurately;
window order IS recoverable from `VideoWindow.start_seconds`; ordinary per-window
failure already degrades locally; the scalar timeout arithmetic is right; there is no
fourth provider map; storage hashes are unaffected by ffmpeg output; and `_checked`
still rejects `finish_reason="length"` after the token raise.

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

1. ✅ **SETTLED 2026-09-23 — timeout 120 s, loop 360 s, budget held at 4** (`04 §3c`).
   The measurement below is what forced it; the earlier "48 s holds" reading was
   over-fitted to one clip and is corrected, not deleted.

   ~~⚠️ RE-OPENED — the audible 90 s window is measured, and 48 s does NOT hold.~~
   Across four production-shaped sources and **n=16 runs**: **min 28.5 s, median
   41.6 s, max 120.8 s.** **6/16 exceed a 48 s derived budget; 10/16 exceed today's
   36 s.** The *same file* returned 28.5 s and 120.8 s, so **latency is not bounded by
   window duration** — the tail is upstream scheduling variance, not payload cost.
   The earlier "48 s holds with ~6 s margin" reading was **over-fitted to a single
   clip**; it has been corrected, not deleted. See `04 §3b`.

   **Consequence:** the chunk size question ("does audio-bearing video need a different
   chunk size?") is answered **no** — 90 s is fine. The *timeout* is the open item, and
   `budget` 4→3 alone is insufficient because it addresses the median, not the tail.

   ⚠️ An earlier attempt measured this with **concatenated** Signal clips and got
   `400` above 45 s. That was a **VFR construction artifact**, not a model limit —
   it was discarded, not reported. Real footage is mandatory for this class of
   measurement.

2. **Will Strands issue concurrent `tool_use` blocks?** ✅ **ANSWERED 2026-09-23 —
   yes, and they already do** (seven real batches carrying 2–3 `media_fetch` calls in
   one model turn, Langfuse). The ceiling on *how many* run at once is
   **`design/tool-concurrency/` (DEC-196)**, not this bundle. *(This entry said
   “Untested” after the Langfuse check had already answered it — corrected
   2026-09-23; see 06-review-round-2 §M3.)*
3. **Does the egress screen cover speech inside a video description?** ⏸️ **PARKED by
   operator 2026-09-23 — no longer gates this bundle** («唔好俾呢一個 PII 呢一嚿嘢成為
   blocker»); findings live in `~/workspace/nora-bench/spikes/pii-layer/`. The gap is
   real (`scan_identifiers` requires a `+`, so a bare local number passes) but it is
   **pre-existing, not created here**: `-an` never protected it, because a video's
   own text was always read and STT is a separate lane. See 02 §5.
