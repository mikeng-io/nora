---
type: design
title: "Timeout and Models — the budget that is already too small on master"
description: "The derived per-call vision budget is 36 s. Measured video latency exceeds it for both the incumbent and the candidate model, so real clips degrade to `unavailable` today. Raise it inside the budget x derived < loop invariant, raise max_tokens for MiMo, and record the image lane as it now ships."
tags: [nora, media, timeout, budget, config, vision]
timestamp: '2026-09-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {src: 04-timeout-and-models, dst: 01-chunk-parallelism, rel: depends_on, provenance: asserted}
  - {src: 04-timeout-and-models, dst: 03-opencode-go-provider-lane, rel: depends_on, provenance: asserted}
---

# Timeout and Models

## 1. The budget is derived, never configured

```
nora/infra/vision/client.py:193   def derive_vision_timeout(...)
nora/infra/vision/client.py:53    VISION_LOOP_SHARE = 0.6

    per_call = loop_timeout_seconds * VISION_LOOP_SHARE / max(1, budget)
    return min(configured, per_call) if configured > 0 else per_call
```

Today's inputs, all read live on the clean base:

| input | value | where |
|---|---|---|
| `[llm].timeout_seconds` | **240** | `config.toml:209` |
| `media_fetch` budget | **4** | `nora/agent/tools.py:727` |
| `vision_timeout_seconds` | **40.0** | `config.toml:1003` |
| → **derived per-call** | **`min(40, 240×0.6/4)` = 36.0 s** | |

The invariant is `budget × derived < loop_timeout_seconds`, and the docstring's
point is that it is *"one nobody can violate if it is never written down twice"* —
which is why `composition.py:1751` **resolves** the budget through
`ToolRegistry.budgets()` rather than reading the constant, so an operator moving
`[llm.budgets]` cannot silently break the bound.

## 2. 🔴 This is a live defect, not a tuning preference

Measured video latency, both models, on clips matching Nora's own preprocessing:

| clip | MiMo v2.6 Flash | k3-256k (incumbent) |
|---|---|---|
| 7 s | 8.5 – 14.2 s | 11.1 s |
| 20 s | 22.5 s | 21.4 s |
| 35 s | 29.6 – **43.8 s** (with audio) | 32.1 s |
| 57 s | 21 – **56 s** | **46.2 s** |

**36 s is below the measured latency of a 57 s clip on the model shipping today.**
Nora answers `unavailable` («I couldn't check») on a perfectly good video. This
is not introduced by anything in this bundle — it exists on master right now, and
enabling audio (§`02`) and reading Instagram Reels will make it bite harder.

## 3. The safe settings

Verified arithmetic against the 240 s loop:

| budget | configured | derived | aggregate | safe |
|---|---|---|---|---|
| 4 (today) | 40 | **36 s** | 144 s | ✅ but below real latency |
| **3** | **90** | **48 s** | 144 s | ⚠️ **arithmetically safe, but see §3a — 48 s is too small** |
| 2 | 90 | 72 s | 144 s | ✅ more headroom, fewer reads/turn |

## 3a. ⚠️ CORRECTED AFTER ADVERSARIAL REVIEW — the aggregate proof is over the wrong unit

The first version of this section claimed "three reads aggregate to 144 s, under the
240 s loop". **The adversarial review found this is a proof about MODEL CALLS applied
to a TOOL INVOCATION**, and it is wrong in two ways:

**(a) The derived timeout is per REQUEST, not per tool call.**
`derive_vision_timeout`'s value is applied at `client.py:314-318`:

```python
text = await asyncio.wait_for(
    self._describe_with_retry(...), timeout=self._timeout)
```

…i.e. once **inside every `describe()` call**. The original `budget × derived < loop`
argument was written when `media_fetch` issued at most `budget` calls *sequentially
inside one invocation*. Concurrency changes the arithmetic: N windows at concurrency C
take `ceil(N/C)` **waves**, and each wave can consume the full per-call timeout.

- 10 windows (the `chunk_max` ceiling) at concurrency 4 = **3 waves → up to 3 × 48 s =
  144 s for ONE tool invocation**
- Three such invocations in a turn = **432 s**, against a 240 s loop

**(b) This is a pre-existing defect that concurrency does not fix.** On today's
sequential loop, one 10-window clip is `10 × 36 s = 360 s` — already past the loop.
`gather` reduces it (3 waves instead of 10) but the *proof* was never valid for
multi-chunk clips, and nothing in the bundle made that visible.

**What this means for the recommendation:** the 48 s figure is arithmetically safe
under the invariant as written, but the invariant is not the binding constraint for a
multi-wave clip. **The budget must be established per tool invocation — covering queued
waves and preprocessing — not per model call.** That is a measurement obligation, not
a config value, and it is now item 1 of §7.

## 3b. ⚠️ CORRECTED AGAIN — 48 s does NOT hold. Latency is not bounded by the window.

> **This section has now been wrong twice, in opposite directions, and both errors are
> kept.** v1 projected 43.8 s was sufficient (no measurement). v2 measured a single
> 90 s audible window at 34.0 / 41.2 / 42.0 s and concluded 48 s holds "with ~6 s of
> margin". **v3 (this one) shows v2 was over-fitted to one clip:** the same window shape
> measured across four sources and n=16 runs spans **28.5 s → 120.8 s**.

### What is actually measured

All rows are 90 s windows at the **exact production shape** (`preprocess.py:186-200`:
`scale='min(720,iw)':-2`, `fps=2`, `libx264 -preset veryfast -crf 26`, `-t 90`) with the
**audio track kept** (the proposed DEC-195 shape). MiMo v2.6 Flash, OpenCode Go direct,
`max_tokens=4096`. Every result below returned HTTP 200 and non-empty content.

| source | resolution | latency (s) |
|---|---|---|
| Big Buck Bunny | 720p | 28.5 · 31.7 · **120.8** |
| Big Buck Bunny (prod re-cut) | 720p | 30.2 · 39.0 · 42.2 |
| Big Buck Bunny (finish-check arm) | 720p | 34.0 · 34.5 · 41.2 · 42.0 |
| montage (real) | 720p | 30.7 · 48.8 · 58.0 |
| looped 57 s clip | 360p | 49.1 · 55.7 · 61.6 |

**Pooled: n=16, min 28.5 s, median 41.6 s, mean 46.8 s, max 120.8 s.**
**6 of 16 runs exceed the 48 s derived budget (38%).**
**10 of 16 exceed today's 36 s derived budget (63%).**

### The finding that matters

**Latency is not a function of window duration.** The *same file* — identical bytes,
identical chain, identical prompt — returned **28.5 s and 120.8 s**. Same for the
720p Big Buck Bunny arm: 30.2 s and 42.2 s. Output tokens do not explain it either
(the 120.8 s run produced only 1,707 completion tokens).

So the tail is **upstream scheduling/generation variance, not payload cost**. That has
three consequences this bundle has to absorb:

1. **A 48 s derived budget fails ~38% of the time** at production shape. At the current
   36 s it fails ~63%. Nora would return `unavailable` on a large fraction of 90 s
   windows — worse than the pre-existing defect it set out to fix, not better.
2. **The §3a wave arithmetic must be recomputed on the tail, not the median.** With the
   measured 120.8 s worst case and `budget=4, C=4`: 10 windows → 3 waves → **362 s per
   tool call**, against a 240 s loop — before any second or third call in the turn.
3. **`finish_reason="length"` at `completion_tokens=4096` occurred** on the 360p looped
   arm (content 342 chars). §4's raise to 4096 is necessary but **not always
   sufficient**; the ceiling is still reachable.

### What must be decided (revised)

The earlier recommendation (`budget` 4 → 3) is **not enough on its own** — it addresses
the median and not the tail. The options are now:

- **A. Raise the timeout to cover the tail:** `vision_timeout_seconds` → 150, and
  `[llm].timeout_seconds` 240 → 360+. Cost: a slow turn blocks the loop.
- **B. Raise the timeout moderately and reduce concurrency exposure:** `budget` 4 → 2
  (derived 72 s) **and** `vision_timeout_seconds` → 150, accepting that a >72 s window
  still returns `unavailable` and is reported honestly by `cut.note()`.
- **C. Cap what one tool invocation may read** so the tail cannot compound across
  windows — bounds the per-invocation budget directly instead of bounding each call.

**B or C, not A alone.** The measured variance is upstream and outside our control, so
the design must fail honestly at the tail (which `cut.note()` already does) rather than
assume the tail away. **This is an operator decision with the distribution attached.**

### Measurement caveats (so this is not over-read)

- Sources: one 100 s YouTube clip, one montage, one looped 57 s clip, plus a re-cut.
  **All animated/montage content.** No speech-heavy 90 s source was obtained — the
  "talky vlog" fetch returned a 19 s clip, which is **not** a 90 s window and is
  excluded from the table. A speech-heavy source could plausibly sit at the high end
  (more output tokens), so the tail may be under-sampled, not over-sampled.
- n=16 across 4 sources is a **distribution sketch, not a latency SLO**. The 120.8 s
  point is a single observation and is not characterised (no repeat).
- Cached vs uncached is not the driver: the 120.8 s run had `cached_tokens=16640`.


### ⚠️ A construction artifact that nearly produced a wrong verdict

An earlier attempt built "90 s" clips by **concatenating heterogeneous real Signal
clips**. That join produced a VFR stream, and the upstream rejected the audible arms
with `400 Invalid request parameters` at 60 s and 90 s — **while 45 s passed and a real
57 s clip passed.** Read naively, that says "audible windows above ~45 s are rejected",
which would have forced a chunk-size change.

**It was the artifact, not the model.** Rebuilt from a single real encode, the same
90 s audible window returns 200/`finish="stop"` 4/4. **Lesson: size the timeout only
against footage that is production-shaped.** Never benchmark with a synthetic join.

### The concurrency requirement compounds the tail

`04 §3a`'s wave arithmetic stands: a tool invocation takes `ceil(N/C)` waves. On the
**measured median** (41.6 s) with `budget=4, C=4`: 10 windows → 3 waves → **125 s per
tool call**; three such calls → **374 s vs a 240 s loop.** On the **measured tail**
(120.8 s): **362 s for one call**, before any second call.

So the per-call timeout and the per-invocation budget are **two separate failures**, and
the second is worse. `vision_timeout_seconds` must clear the tail it intends to serve,
and the loop budget must clear `waves × tail × calls`.



## 4. `vision_max_tokens` must rise for MiMo

```
config.toml:994   vision_max_tokens = 1024
```

At 1024, on the two largest clips:
- 57 s clip → `finish_reason="length"` and **empty content** (reasoning consumed
  the entire budget)
- 35 s clip → a truncated 90-character stub

At **4096** both returned `finish="stop"` and complete descriptions.

**This trap lands directly on a guard this repo already has.** `_checked`
(`client.py:~130`) raises when `finish_reason == "length"`:

> *«a truncated description reads exactly like a complete one, so it is
> unavailable, not short»*

So the failure mode is a **safe** `unavailable`, not a confident half-read — the
design was right. But a lane that answers `unavailable` on every long clip is not
shipping. K3 is terse at 1024; MiMo is not. **`vision_max_tokens` 1024 → 4096**
travels with the provider change.

## 5. The image lane, as it actually ships

Not changed by this bundle, recorded because a benchmark session turned it up and
it affects the same config section.

`nora-image` was, at measurement time, serving **`kimi-k3`** rather than the
`glm-5.3-flash` its config declared — stable on 3/3 probes (identical media delta,
no failover signature). Operator has since moved it; confirm the live combo state
at implementation time rather than trusting this paragraph.

What the measurements support, if the lane is revisited:

| image model | dense label | clean render | media Δ |
|---|---|---|---|
| GLM 5.3 Flash | 6.0 s | 9.2 s | +2,435 |
| DeepSeek V4.1 Flash | 7.0 s | **4.2 s** | **+960** |
| k3-256k | 29.6 s | 14.5 s | +2,443 |

Accuracy was a **tie** across all of them; K3's image cost is ~20× the input
price of the alternatives for no measured accuracy gain. **K3 is the wrong model
for images and the forced model for video** — the split is by modality, not
preference.

## 6. New config keys

Two additions, both with template-gate obligations:

| key | section | default | why |
|---|---|---|---|
| `chunk_concurrency` | `[media.describe.video]` | 4 | bounds the `gather` fan-out from `01` |
| `vision_max_tokens` | `[media]` *(existing key)* | **1024 → 4096** | `§4` |

The concurrency default of 4 deliberately equals `media_fetch`'s budget so an
operator reading both sees one number meaning the same thing.
