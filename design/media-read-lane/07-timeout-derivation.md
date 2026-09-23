---
type: design
title: "The timeout derivation — fix the formula, not the number"
description: "The operator rejected raising the loop to 800 s and chose to fix the formula instead: keep [llm].timeout_seconds at 360 and derive the per-call vision timeout from the SERIAL factor (waves), not the CONCURRENT factor (call count). Dividing a wall-clock ceiling by calls that overlap is the unit error behind the inert 120 s. With the divisor corrected, the configured 120 is honoured for the Instagram case the budget exists for."
tags: [nora, media, timeout, derivation, decided]
timestamp: '2026-09-23'
edges:
  - {dst: 04-timeout-and-models, rel: supersedes, provenance: asserted}
  - {dst: 06-review-round-2, rel: resolves, provenance: asserted}
---

# The timeout derivation — decided 2026-09-23

## 1. The operator's two decisions

| question | decision |
|---|---|
| `[llm].timeout_seconds` (the agent loop) | **stays 360** — do NOT raise it to 800 |
| fix the derivation formula | **yes** — derive from `invocations × waves`, not `budget` |

The reason this works without raising the loop: once the divisor stops counting
*concurrent* calls as if they were *serial*, 360 × 0.6 = 216 s is enough to honour
a 120 s per-call ceiling.

## 2. The unit error, stated precisely

`derive_vision_timeout` divides a **wall-clock** ceiling by the number of **calls
that overlap in time**:

```python
per_call = loop_timeout_seconds * VISION_LOOP_SHARE / max(1, budget)   # client.py:210
```

Four calls that run **concurrently** occupy `max(their durations)` of wall clock,
not their **sum**. So `4 × 54 = 216 s` is not the time those four calls consume —
it is four times more than they consume. The old formula's own invariant
(`budget × derived < loop`) was therefore proving something about a quantity that
does not exist.

Two further errors compound it:

- **One `media_fetch` is not one model call.** A long video is cut into windows
  (`VIDEO_CHUNK_SECONDS = 90`, `VIDEO_MAX_CHUNKS = 10`), so one invocation can be
  up to 10 model calls.
- **The divisor is the wrong factor entirely.** The calls that *do* add up in wall
  clock are the **serial** ones — the waves a single invocation must run. The
  calls that do not are the **concurrent** ones.

## 3. The corrected derivation

```python
# The divisor is the SERIAL factor: how many model calls one invocation must
# await in sequence. Concurrent reads overlap, so they are NOT summed.
waves = ceil(windows / concurrency)
per_call = loop_timeout_seconds * VISION_LOOP_SHARE / max(1, waves)
return min(configured, per_call) if configured > 0 else per_call
```

**New invariant:** `waves × derived < loop × VISION_LOOP_SHARE` — the time actually
consumed by one invocation, which is `waves × per_call`. It is **independent of the
budget**, because concurrent invocations overlap. `budget` bounds *refusals* (how
many calls a turn may make), not wall clock — which is exactly the count-vs-
concurrency distinction `design/tool-concurrency/` (DEC-196) already draws.

**Worked, against the settled values (`loop = 360`, `configured = 120`, share 0.6):**

| case | windows | concurrency | waves | per-call |
|---|---|---|---|---|
| **Instagram Reel / Story / Post (≤90 s)** | **1** | 1 | **1** | **120 s** ← honoured |
| 3-minute clip | 2 | 1 | 2 | 108.0 s |
| 15-minute clip (the maximum) | 10 | 1 | 10 | 21.6 s |
| 15-minute clip, windows read ×4 concurrent | 10 | 4 | 3 | 72.0 s |

**The case the budget was held at 4 for** — a post with four clips, all ≤90 s:
4 concurrent invocations × 1 wave × 120 s → **wall clock 120 s against a 360 s
loop, 240 s headroom.** The configured 120 is honoured, the loop is not raised, and
the four-video post completes. **This is the first configuration of the whole
exercise that satisfies all three at once.**

**Long clips still tighten automatically.** A 15-minute video gets 21.6 s per
window (10 serial waves), so it cannot blow the loop — the protection the old
formula was trying to provide is preserved, on the correct quantity.

> ⚠️ **Not yet verified at code level.** The numbers above are arithmetic on the
> code as read. Before merge: (a) confirm `cut_video` can report `windows` before
> `VisionClient` is constructed, or carry the wave count some other way — the
> derivation runs at construction time in `composition.py:1769`, while windows are
> only known per-request at read time; if construction cannot know them, the
> `min(configured, …)` must move to the call site. (b) Decide the concurrency
> value used for `waves`, since `01 §3` leaves the window fan-out limit open.

## 4. Why NOT raising the loop to 800

The operator's instinct — "if 360 is not enough, increase it" — is right, and it
*does* work: `800 × 0.6 / 4 = 120`, so 800 would honour 120 under the old formula.
It was rejected because:

- **It fixes the symptom, not the cause.** The formula would still divide by a
  concurrent count; it would merely be fed a bigger number until the wrong answer
  happened to equal the right one.
- **It has to be re-tuned every time the budget moves.** At budget 8, honouring
  120 would need a loop of 1,600 s. With the divisor corrected, the budget can rise
  freely without touching the loop — which is precisely the operator's stated
  concern that raising the budget must not silently multiply concurrency.
- **It inflates the text-synthesis ceiling too.** The loop feeds the shared
  derivation, so an 800 s loop raises the text lane's per-call ceiling from 54 s to
  120 s — a change to a lane nobody decided on.
- **A 13-minute turn is unhealthy for a chat room** — one stuck post would hold a
  tag turn for thirteen minutes before it gave up.

## 5. Consequences to carry

1. **`budget` stops being an input to the timeout.** `05 §5`'s test
   `test_moving_the_media_fetch_budget_moves_the_derived_timeout_with_it` must be
   **deleted or inverted** — after this change, moving the budget must NOT move the
   timeout. This resolves the contradiction §3c/§5 carried.
2. **A new test is required:** `test_the_derived_timeout_honours_the_configured_
   value_at_the_instagram_window_count` — one window, one invocation → derived ==
   configured (120), not a fraction of it. **This is the regression that would have
   caught the inert-120 defect**, and the current suite has nothing like it.
3. **`01 §3`'s bounded fan-out must land with this.** The derivation depends on
   `waves`, which depends on `concurrency`. If the window fan-out is unbounded, the
   divisor is undefined and the invariant is unprovable.
4. **The DEC-196 ceiling does not substitute for this.** It bounds how many tool
   calls run at once and queues the rest; it says nothing about a single
   invocation's internal waves.
