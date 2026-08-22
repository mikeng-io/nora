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

## 3. Prior art, running, in a dependency this project already uses

Honcho — which Nora already depends on for memory — has a dreamer. It is
event-driven, not clock-driven, and its trigger is **three separate gates**:

| gate | value | what it answers |
|---|---|---|
| document threshold | 50 | *why* it fired — has enough accumulated to be worth a pass |
| minimum hours between dreams | **8** | a **floor**, not a period — refuse even when the threshold is met, if the last pass was recent |
| idle timeout | 60 min | *how* it fires — once triggered, wait for quiet rather than going immediately |

Its own comment on keeping two of those apart is the design in one line:

> *the two gates were intentionally split — collapsing them into a single
> trigger_reason would lose the scheduling semantics.*

So it records **why it fired** and **how it fired** as different fields. Nora
records neither, because it has neither.

Two more properties worth taking:

- **A new signal cancels the pending pass** rather than queueing a second one.
  Debounce is part of the trigger, not a separate concern.
- **In-flight is enforced by a unique index in the database**, with the queue as
  the source of truth. Nora's in-flight lock is a process-local set that dies
  on restart — which is also why its backoff does not survive one.

### The cadence comparison

| | Nora's Dreamer | Honcho's dreamer |
|---|---|---|
| shape | 300-second clock, every scope, every adaptor | event-driven; no clock at all |
| trigger | "did rendering produce a non-empty string" | accumulation crossed a threshold |
| rate limit | none — backoff only, and one message resets it | an **8-hour floor** between passes |
| when it fires | immediately, on the tick | after the room goes quiet |
| in-flight | process-local set, lost on restart | unique index, survives everything |
| telemetry | one outcome | why it fired and how it fired, separately |

**300 seconds against an 8-hour floor is roughly two orders of magnitude.** But
the interval is the least of it: Nora has **no floor at all**. Nothing anywhere
says *this adaptor may not run again for N hours*, so the only thing standing
between the current design and running every five minutes forever is whether
rendering happened to produce an empty string.

And the metaphor was right the whole time. A pass that runs every five minutes
is not dreaming, it is polling. Consolidation is what happens **after** enough
has accumulated and the room has gone quiet — which is precisely the shape the
three gates above encode, and precisely what a clock cannot express.

## 3.1 And Nora's own corpus already specifies one

The Honcho comparison is external. The note tier's curator — specced, unbuilt —
already declares exactly the trigger this seam argues for, **inside this
repo**, and goes one better on the property that matters most.

It declares a fire condition of three OR'd clauses (accumulation, change-plus-
quiet, and age), and singles out the third:

> *retirement is **time-driven, not evidence-driven** … a note whose anchor date
> has passed needs retiring even if nobody has touched the notebook in a month.*

That is a trigger case a pure accumulation threshold **cannot** express, and it
is why "an adaptor declares its condition" is stronger than "lengthen the
period": some passes must fire precisely when nothing has happened.

**And it solves §2's hardest problem — the trigger that costs what it gates.**
Its reads are in two tiers, and *the cheap one selects*:

| step | reads | cost |
|---|---|---|
| merge **detection** | ids, dates and entity sets for every note | **SQL only — zero tokens** |
| merge **execution** | the full bodies of one colliding group | bounded by collision size |
| retire | ids, dates, strike counts | **SQL only** |

Detection is a `GROUP BY … HAVING count(*) > 1`, not a judgement — and its own
criteria table says so in as many words: ***"SQL. The model never decides
eligibility."***

So the pattern is not "make the trigger cheap" but **split the read**: a
deterministic selector that costs nothing, and an expensive pass over only what
it selected. That is the same separation the sibling project's built pipeline
puts between its deterministic detector and its LLM author
([07-loop.md](07-loop.md) §5) — arrived at twice, independently, for the same
reason.

**None of it is expressible today**, and that is the finding: the best-specified
adaptor in the corpus declares a trigger the runtime has no verb for.


## 3.2 Two kinds of work, and they need different breakers

The note tier separates its two passes on a distinction the bundle has been
missing, and it decides more than scheduling:

| | **cursor-shaped** | **content-addressed** |
|---|---|---|
| unit of work | a backlog that drains | one object that may never change |
| trigger | lag / quiet / age over a scope | *this item is due*, a predicate over the tier's own rows |
| self-bounding? | yes — working consumes the backlog | **no — an item can be "due" forever** |
| breaker | the cursor is the breaker | needs a per-item counter standing in for one |
| read cost | splits into a cheap selector + a bounded pass | the unit is already one item; the whole pass is expensive |
| tools | may need none at all | definitionally a tool loop |

> *nothing is consumed by working on it — no cursor advances, no backlog drains
> — so a note can be "due" forever. **The strike is the substitute for the cursor
> the other passes have.**"*

**This is a derivation rule, not a taxonomy for its own sake:** a pass over a
consumable backlog needs no no-progress breaker; a pass over a persistent object
needs one, and its breaker is a per-item counter. Adding a breaker to the first
is ceremony; omitting it from the second is a loop that never ends.

**It also reclassifies one of this bundle's own findings.** The fact adjudicator
is content-addressed in this sense — a candidate that can never be settled stays
pending — and the negative cache is doing the strike's job. The ratchet in
[01-live-state.md](01-live-state.md) §3 is what happens when a content-addressed
pass has *neither* a cursor nor a counter.

**And it names a third scheduling shape.** The spec's end state is not a clock
and not an event stream:

> *over time most work should move to [self-scheduled revisits], which is the
> cheaper one: **the notebook tells the Dreamer when to think about what**,
> instead of every scope being swept whether or not anything moved.*

So the sweep exists to bootstrap and to catch what nothing scheduled; the steady
state is **per-item due times living in tier tables**. That is a stronger claim
than D2's "event-driven", and it is the direction D2 should be read as pointing.

## 3.3 The counter-case to an accumulation threshold

§3 held up an accumulation threshold as prior art. The same corpus supplies the
case it fails, and rejects it explicitly:

> *recurrence-gating was considered and **rejected**: in the operator's own
> scenario **the thread dies after one exchange and is never mentioned again,
> which is precisely when the note has to exist.** A "wait for a second mention"
> rule fails the case the tier is for.*

An accumulation threshold is a **confidence-raising gate**, and confidence-raising
gates suppress exactly the singleton events some passes exist to catch. Together
with time-driven retirement (§3.1), the full statement is:

**accumulation is one trigger axis of at least three, and it is the wrong one
for both time-driven work and singleton-event work.**

Which is the argument for *declared* conditions rather than a better default: no
single axis serves all three, so the axis has to be the adaptor's to choose.

## 3.4 The runtime iterates the wrong domain

A gap, recorded because it is small and silent:

> *the runtime's iteration returns **conversations**; the notebook is keyed on
> **workspace**, so both rooms would tick the same notebook … **or the pass does
> duplicate work behind a lock.***

**When the runtime's iteration domain is finer than the adaptor's key, every
pass is duplicated and the only thing preventing corruption is the in-flight
lock** — which converts a design error into a silent throughput cost. It is the
trigger-side twin of the key-derivation trap in
[08-state.md](08-state.md) §10.


## 4. The design

**An adaptor declares its own trigger condition, and the runtime evaluates it
before any input is rendered. The period is not fixed.**

That second sentence is the decision, not a consequence of the first. A fixed
clock is what the Dreamer has, and no amount of declaring conditions helps if
every condition is still asked every 300 seconds for every adaptor. The tick, if
one survives at all, becomes the thing that *asks*; what decides is the
adaptor's own condition plus its floor.

Four properties: three earned from §2, the fourth from §3.

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
   fold it into failure and a quiet room looks broken. Recording *why* it fired
   separately from *how* — §3's split — is what makes the distinction legible
   afterwards rather than merely correct at the time.
4. **A floor, separate from the trigger.** A minimum interval between passes for
   an adaptor, independent of whether its conditions are met. The trigger says
   *there is something to do*; the floor says *not yet, regardless*. Nora has
   the first (weakly) and not the second at all, which is why a busy room can
   drive a failing adaptor at full tick rate indefinitely.

**Cadence belongs to the adaptor, and the shape it should take is
event-driven.** Extraction wants new messages; adjudication wants queue depth;
synthesis wants accumulation *plus* quiet *plus* a floor. None of those is a
period, and none is expressible today — while the third is precisely what the
dependency next door already runs.

The honest end state is that the clock stops being the scheduler and becomes at
most a cheap poll over declared conditions — or disappears entirely in favour of
the signal that already exists, since a message arriving is exactly the event
that should be asking the question.

**What this does not change:** per-adaptor failure isolation, the in-flight
lock, and watermark seeding are all correct and stay as they are. The backoff
mechanism itself is correct — it is fed a wrong signal, not wrongly built.

## 5. Two things the trigger does not fix, recorded so they are not assumed away

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

## 6. What must be true before the migration lands

A global concurrency bound, **chosen rather than inherited**. Ten units today,
eighteen after two more tiers migrate, all sharing one client, with the only
existing bounds being per-unit. Per-unit isolation is correct and is not a
substitute for a population bound.

The trigger is what makes that bound expressible. Until an adaptor can say
whether it wants to run, the runtime is choosing a concurrency limit for a set
whose size it learns only by doing the work.
