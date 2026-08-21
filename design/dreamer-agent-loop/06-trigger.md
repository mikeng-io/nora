---
type: design
title: "Trigger — An Adaptor Must Say When It Wants To Run"
description: "The fire condition is a side-effect of rendering a prompt, so the runtime must do the expensive thing to learn whether to do the expensive thing. That single absence causes the cadence problem, the concurrency problem, the backoff defect and one live bug."
tags: [nora, dreamer, scheduling, cadence, backoff, concurrency, trigger]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
---

# Trigger — An Adaptor Must Say When It Wants To Run

## 1. The absence

`DreamAdaptor` has **no `should_fire` verb**, deliberately. The fire condition
is: *did `inputs()` render a non-empty string.*

Everything below follows from that one decision. It is the clearest case in the
bundle of a small design choice with large, distributed consequences — and none
of the consequences look like each other, which is why they were found
separately and filed as four different problems.

## 2. What it causes

**You must do the expensive thing to learn whether to do the expensive thing.**
Rendering is not free — for the adjudicator it means querying the candidate
table and the fact store. There is no cheap trigger *available*, because
deciding requires the work that deciding was supposed to gate.

**The runtime cannot know how many adaptors will fire.** So it cannot bound
concurrency. Today that is up to ten concurrent units per tick with no global
cap, no semaphore and no rate limiter; after two more tiers migrate it is
eighteen, each potentially issuing several network calls on one shared client.
This is not a separate problem from cadence — it is the same absence.

**Cadence is one global clock for three different rhythms.** Extraction is
watermark-driven and cheap; a clock suits it. Adjudication is a *work queue*
wearing a clock's clothing — it should run when there is something in the queue,
and today it fires every tick on every workspace until the queue drains.
Synthesis, when it exists, wants to run rarely. One tick serves all three.

**"Did not fire" is indistinguishable from "ran and found nothing" at the
dispatcher** — and that *is* the backoff defect. The dispatcher returns nothing
when the conditions do not fire; the failure counter treats any non-failure
result, including that nothing, as success and clears the ladder. So **a single
incoming chat message resets the backoff to zero** — observed three times in
three hours, and it means the one-hour ceiling is unreachable in practice.

**It has already produced a live bug.** Both adaptors initially rendered a
polite `"(none)"`, which is not blank, so the skip never fired and a model was
invoked every tick to be told there was nothing to do. The fix was to render an
empty string instead — which is a workaround for a missing verb, and it leaves
the next adaptor author one polite sentence away from the same bug.

## 3. The design

**An adaptor declares its own trigger, and the runtime evaluates it before any
input is rendered.**

Three properties, each earning its place from §2:

1. **Cheap.** The trigger must be answerable without doing the work it gates.
   Whatever it consults — a lag count, a queue depth, a clock, a watermark age —
   must be cheaper than rendering. This is the property the current design
   cannot have.
2. **Declared, not emergent.** The runtime can then ask *"who wants to run this
   tick"* before running anything, which is what makes a concurrency bound
   possible at all. A bound that cannot see the population it bounds is not a
   bound.
3. **A distinct outcome.** "Did not fire" is neither success nor failure and
   must not be folded into either. Fold it into success and the backoff dies;
   fold it into failure and a quiet room looks broken.

**Cadence then belongs to the adaptor, not the clock.** The tick becomes the
thing that *asks*, not the thing that decides. Extraction can want new messages;
adjudication can want queue depth; synthesis can want a slow clock and a
quorum. None of those is expressible today.

**What this does not change:** per-adaptor failure isolation, the in-flight
lock, and watermark seeding are all correct and stay as they are. The backoff
mechanism itself is correct — it is fed a wrong signal, not wrongly built.

## 4. Two things the trigger does not fix, recorded so they are not assumed away

**The wall clock covers the model call only.** Input rendering, item acceptance,
commit, and the watermark queries around them sit outside any timeout, so a hung
store read leaves a unit marked in-flight **forever** — skipped silently on
every subsequent tick. The config's own comment claims the timeout "wraps the
WHOLE run, not one call"; that is false. A declared trigger makes this *more*
visible, not less, because a unit that never fires again is now anomalous rather
than ordinary.

**There is no partial-progress path.** On timeout or budget exhaustion,
everything the loop learned is discarded and the watermark is held — so the
batch is re-read next tick, larger. That is the ratchet
([01-live-state.md](01-live-state.md) §3), and it is a property of the **loop**,
not the trigger. See [07-loop.md](07-loop.md).

## 5. What must be true before the migration lands

A global concurrency bound, **chosen rather than inherited**. Ten units today,
eighteen after two more tiers migrate, all sharing one client, with the only
existing bounds being per-unit. Per-unit isolation is correct and is not a
substitute for a population bound.

The trigger is what makes that bound expressible. Until an adaptor can say
whether it wants to run, the runtime is choosing a concurrency limit for a set
whose size it learns only by doing the work.
