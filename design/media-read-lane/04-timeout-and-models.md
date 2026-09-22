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
| **3** | **90** | **48 s** | 144 s | ✅ **recommended** |
| 2 | 90 | 72 s | 144 s | ✅ more headroom, fewer reads/turn |

**Recommended: `[llm.budgets] media_fetch = 3`, `[media].vision_timeout_seconds = 90`
→ derived 48 s.** Rationale: it covers a 60 s Story *with audio* (measured
43.8 s, the worst single-chunk case this bundle creates) while keeping the
aggregate at 144 s < 240 s, and it costs one media read per turn (4 → 3) against
a gain of 12 s of per-call headroom.

**Alternative, if 4 reads/turn matters more:** raise `[llm].timeout_seconds`
240 → 360. Then `360 × 0.6 / 4 = 54 s` with the budget unchanged. Rejected as the
default because it is the *tag path's* loop timeout — a genuinely hung tool would
hold a turn for six minutes — whereas the budget lever only affects media.

Whichever is chosen, **both numbers must move in the same commit** and the
invariant table above should be re-derived, not assumed.

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
