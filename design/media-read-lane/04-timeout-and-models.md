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
which is why `composition.py:1771` **resolves** the budget through
(**anchor corrected 2026-09-23: the first version cited :1751, which is the surrounding
comment; the resolving call `budget_overrides=cfg.llm.budgets).budgets()["media_fetch"]`
is at :1770-1771**)
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

## 3. ❌ WITHDRAWN — "The safe settings" (kept for the record; do NOT use)

> **WITHDRAWN.** §3a and §3b both falsified the table below, and the settled
> value is in §3c. It is retained only so the error stays visible. **If you are
> reading for current advice, skip to §3c.** (Flagged by 06-review-round-2 §m4:
> this was the one retained correction with no withdrawal marker.)

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

All rows are 90 s windows at the **production filter chain** (`preprocess.py:185-199`:
`scale='min(720,iw)':-2`, `libx264 -preset veryfast -crf 26`, audio kept) **except on
one parameter, corrected below**. MiMo v2.6 Flash, OpenCode Go direct,
`max_tokens=4096`. Every result below returned HTTP 200 and non-empty content.

> **⚠️ CORRECTED 2026-09-23 (independent audit): the bench chain used `fps=2`; production uses
> `VIDEO_FPS_CAP = 24`.** `preprocess.py:52` sets 24 and applies it at `:191`
> (`f"fps={VIDEO_FPS_CAP}"`); the *bench* scripts hardcoded `fps=2`
> (`bench_90s_multi.py:62`, `bench_90s_prod.py:45`). So the table below was measured at
> **1/12th of production frame rate**, and the earlier phrase "exact production shape"
> was false on the parameter that most changes payload size and token count.
>
> **Re-measured at the real fps** (`bench_90s_fps.py`, same 100 s YouTube source, same
> chain, fps the only variable, n=3 each):
>
> | fps | MB | latency (s) | ctok max |
> |---|---|---|---|
> | 2 (what the table below used) | 4.30 | 37.1 · 49.2 · 55.5 | 1,835 |
> | **24 (production)** | 4.94 | **36.3 · 41.1 · 42.7** | 2,245 |
>
> **The correction does not move the conclusion.** fps=24 produced *slightly more*
> output tokens (2,245 vs 1,835 max) and the same latency band, so the pooled
> distribution — and the 120 s timeout derived from it — stands. What changes is the
> claim's strength: the numbers below are a **lower-bound-ish proxy** measured at the
> wrong fps, not "exact production shape". Any future re-measurement must use `fps=24`.


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

> **⚠️ AMENDED 2026-09-23 (independent audit) — two selection problems, both now stated.**
>
> **(a) "Four sources" overstates the diversity.** `bbb_720p` (multi), the prod arm and
> the finish arm all read the **same file** (`corpus_yt/prod90_audio.mp4`), so **10 of the
> 16 pooled observations are one clip**. Distinct footage is **three**: Big Buck Bunny,
> the montage, and the looped 57 s clip. The caveat below already said "plus a re-cut";
> the headline "n=16 across four sources" was still more independent-sounding than the
> pool is.
>
> **(b) Three valid runs were silently excluded.** `out_90s_final.json` holds three more
> HTTP-200, `ok:true`, audible-90 s runs of the looped source (**62.0 / 62.6 / 68.7 s**)
> that do not appear in the pool and were not mentioned as excluded. Including them:
> **n=19, min 28.5 s, median 42.2 s, max 120.8 s — 9/19 exceed 48 s (47%), 13/19 exceed
> 36 s (68%)**. That is a **stronger** tail than published. The selection was not wrong
> (the excluded runs are the same source as an included arm) but it was silent, and
> silence in a sample selection is the thing to fix. **The n=19 figures are the more
> conservative input for any future decision that consumes this distribution.**
>
> **Neither problem changes the settled settings**, which were chosen to clear the tail
> rather than a median — but a reader must not treat n=16 as "16 independent samples".

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

### What must be decided (revised) → ✅ DECIDED in §3c

The options below were the pre-decision framing, kept so the path to the decision is
visible. **The operator chose none of them verbatim** — A was too weak (150 s still
sits below the 120.8 s tail plus compound), and B and C were rejected as evasions.
The settled settings are in **§3c above**: timeout 120, loop 360, budget held at 4.

- ~~**A.** `vision_timeout_seconds` → 150, `[llm].timeout_seconds` → 360+.~~
- ~~**B.** `budget` 4 → 2 and `vision_timeout_seconds` → 150.~~ **REJECTED** — kills the
  four-video post.
- ~~**C.** Cap what one tool invocation may read.~~ **REJECTED** — same reason.

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



## 3c. ⛔ SETTLED IN INTENT, **BLOCKED IN EFFECT** — review round 2, 2026-09-23

> **⛔ The settings below are the operator's decision and remain the intent. They are
> NOT yet effective, for two reasons found by independent review and re-verified
> against the code (see `06-review-round-2.md` §B1, §B2).**
>
> **B1 — the configured 120 s is INERT. The effective cap is 54 s.**
> `derive_vision_timeout` is still in the path and still wins:
> `min(configured, loop × 0.6 / budget)` = `min(120, 360 × 0.6 / 4)` = **54 s**
> (`nora/infra/vision/client.py:210-211`). Nothing in this bundle removes or bypasses
> it, and §5 below even mandates tests that *pin* it. Against this bundle's own n=19
> pool, **7/19 ≈ 37% of runs exceed 54 s** — so the tail this decision exists to
> clear still fails, at 54 s instead of 36 s. **A decision is needed on the
> derivation itself** (remove the `/budget` division, remove the `min`, or replace the
> invariant with a wave-based one), not on the number.
>
> **B2 — the numbers do not compose, and the failure is SILENT.**
> `4 invocations × ceil(10/4)=3 waves × 120 s = 1,440 s` against a 360 s loop; even
> one wave per call is `4 × 120.8 = 483 s > 360`. The bundle's own median product
> already exceeds the loop it chose (`3 × 41.6 × 3 = 374 s`, §4.2). And when the loop
> fires, `nora/engagement/stages/invoke.py:171-179` abandons the turn wholesale — logs
> `invocation_timeout`, increments `nora_silent_failures_total`, returns a blank
> trace, **user gets nothing**. That converts the honest per-window `_UNREAD_MARKER`
> degradation into **silent turn death** — the "unreadable post" outcome the operator
> rejected budget-lowering to avoid, one layer up with a worse failure mode.
> **The same unit error §3a found in the code, repeated in this bundle's own loop
> arithmetic: it computed `waves × tail` for ONE call and never multiplied by the
> held budget.**
>
> **Also open (cross-bundle):** DEC-196's ceiling **queues**, so it bounds
> *concurrency*, not *wall-clock* — four queued 3-wave invocations still consume
> their sum inside the same `wait_for`. §3c leaned on it for the latency question; it
> does not answer that question, and `01 §3`'s hand-off ("cross-clip queueing must be
> priced into §04's invocation budget") has **no queueing term in §04 at all**.

### 3c-i. The operator's decision, as given (intent — unchanged)



The three coupled numbers, decided together after B and C were both rejected as
avoiding the problem rather than solving it («你 B 同 C，你係斬腳趾避沙蟲啫»):

| layer | was | **decided** | why |
|---|---|---|---|
| agent loop `[llm].timeout_seconds` | 240 | **360** | must clear `waves × latency × calls`, not one call |
| `media_fetch` budget | 4 | **4 (held)** | counts MEDIA, not model calls; a four-video post needs four reads |
| `vision_timeout_seconds` | 40 (derived 36) | **120** | must clear the measured tail (120.8 s), not the median (41.6 s) |

**Why the budget is held, and what was wrong with lowering it.** `media_fetch`'s
budget is `NativeTool(4, …)` at `nora/agent/tools.py:727` — a ceiling on how many
media items one turn may read, **not** on how many model calls it may make. The
operator's own case is a post carrying four videos; `budget` 4 → 2 makes that post
*unreadable*, which is the defect the bundle set out to remove, re-introduced as a
"mitigation". Option C (cap what one invocation reads) fails the same way.

**Why the correction belongs in the arithmetic, not the config.** `derive_vision_timeout`
computes `loop × 0.6 / budget` and calls the result "per-call" — it divides a loop
allowance by a count of MEDIA ITEMS. The two quantities are not the same unit: one
`media_fetch` can occupy `ceil(N/C)` waves of model calls. So the derived number was
never a per-call figure, and the fix is to stop deriving the timeout from the item
count at all: **set it to cover the measured tail (120 s) and raise the loop to clear
the concurrency requirement (360 s).** `VISION_LOOP_SHARE = 0.6` remains a design
assumption, not a measurement; §3b's distribution is what supersedes it as the input.

**The forward path is a ceiling, not a budget cut.** The risk the operator named —
«budget raise 到 10 → 10 個 concurrent» — is answered by
`design/tool-concurrency/` (DEC-196), which bounds how many tool calls run at once
independently of how many are permitted. That is the right place for it: the budget
may then rise without the fan-out rising with it.

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
