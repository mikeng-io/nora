---
type: contract
title: "Measurement — Every Seam Ships Its Instrument, Including This One"
description: "Six layers carry an instrument that cannot observe what it exists to observe. The rule that follows, applied first to this bundle's own change: what a working loop looks like, what would prove it wrong, and what says turn it off again."
tags: [nora, dreamer, measurement, metrics, evals, acceptance, rollback]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 02-instrument-blindness, rel: motivated_by, provenance: asserted}
---

# Measurement — Every Seam Ships Its Instrument

## 1. The rule

> **A change whose effect cannot be read back is not done.**

CLAUDE.md already says this — *"never read an instrument's output as evidence
without first checking it can measure the thing"* — and
[02-instrument-blindness.md](02-instrument-blindness.md) shows six independent
layers where it was not held, one of them in a tier written by a different
session at a different time. Six instances is not six bugs. It is a discipline
that has no home, in a subsystem where nobody is waiting and a null reading and
a broken gauge are the same observation.

This node gives it a home, and applies it to this bundle's own change first —
because the bundle asserting the rule while exempting itself is the failure mode
it is describing.

## 2. What each seam owes

| seam | the reading that must be possible |
|---|---|
| **Transport** ([05](05-transport.md)) | which ceiling actually bound: the per-call one, the loop budget, or the provider's own. Today none of the three is distinguishable from the others or from a provider error |
| **Trigger** ([06](06-trigger.md)) | fired / did-not-fire / was-skipped, per adaptor per tick, **surviving a restart**. Today a skip and a backoff leave no first-party record at all, and the counters are in-process |
| **Loop** ([07](07-loop.md)) | how many turns it took, why it stopped, and whether anything was kept when it was cut off |
| **State** ([08](08-state.md)) | whether a granted tool was ever *called*, and whether calling it changed the answer. A tool nobody reaches for is a cost with no benefit and must not be invisible |
| **Self-correction** ([12](12-self-correction.md)) | whether a later run ever superseded an earlier one — the thing the language tier structurally cannot do and nobody would currently notice |

The three that bind hardest are the first two, because they are the ones whose
absence already cost weeks: the cap that actually binds, and the difference
between *did not fire* and *ran and found nothing*.

## 3. The specific blindness to close first

**A cap that binds must be distinguishable from a provider failure.** The
counter that exists to answer *"did a budget cap bind?"* is incremented only on
the success path, so the timeout value its own help text advertises **can never
appear**, and the token-limit case exits as a generic failure. All thirteen
production failures are indistinguishable from a rate limit.

**"Did not fire" must be a third outcome.** It is currently folded into success,
which is what clears the backoff and lets one chat message reset the ladder
([06-trigger.md](06-trigger.md) §2). Fixing the metric and fixing the defect are
the same edit.

**A skip must leave a record that survives a restart.** Backoff and in-flight
skips emit no log line, and their counters die with the process. The
reconstruction in [01-live-state.md](01-live-state.md) §4 had to be rebuilt by
joining log timestamps against message arrival times, because the instrument
that should have recorded it had already been reset.

**A tier that declares metrics must ship them.** One tier declared ten and
shipped none, so its own stated failure-detection was unreadable and a deferred
piece of work waited on evidence that structurally could not be collected.

**Resolved 2026-08-27, and the resolution sharpens the rule rather than
satisfying it.** The note tier ships **three**
([27](27-note-dreamer.md) §6.1): four of the ten were already carried by
`nora_tool_calls_total` and a second series would have double-counted, and four
measure adaptors that do not exist — where a declared name with no call site is
the NEXT failure in this same family, not a partial fix. So the rule is not
*"ship every metric you declare"*; it is:

> **Ship the metric that has a call site, and delete the declaration that does
> not.** A name without a site exports a flat zero, and a flat zero reads as a
> measurement.

## 3.1 One tier already wrote the rule down

The note curator's spec states the requirement this seam generalises, for its
own read cap:

> Bodies read per tick are **capped in config and counted when the cap bites**. A
> silent truncation here reads as *"nothing to do"*, which is the
> `unavailable`-as-`nothing_found` failure at the pass level.

That is exactly Layer 1's defect stated in advance, by a different author, for a
different tier — a cap that binds must be distinguishable from having found
nothing. It is also, notably, the one place in the corpus where a cap is
specified *with* its instrument rather than as a bare number.


## 3.2 Two ways an instrument passes while measuring nothing

**A fixture that reuses one value across N fields cannot see a permutation.** A
non-null constraint catches a dropped field; it does not catch a *wrong* one — a
sink that stamped a constant, or **swapped two columns**, satisfies every
constraint and passes every assertion. The suite must write a **distinct value
into each field** and read all of them back. With one repeated string a swap is
invisible, and *"the assertion becomes the thing it exists to prevent: a check
that looks like rigour and verifies nothing."*

This is CLAUDE.md's never-write-a-fake-that-answers-everything-the-same-way rule,
applied to **fixtures** rather than to fakes.

**A primitive chosen by name may answer a different question.** Measured on 3000
rows: a similarity operator returned **0 of 3000 rows that all contained the
string**, because it compares whole strings rather than doing the containment a
search asks for. A plausible, correctly-named, documented operator would have
reported `nothing_found` on a corpus where everything matched.

**So: a granted read tool must be measured against a corpus with a known answer
before any of its null results are believed.** That extends this seam's State row
— not only *was the tool called*, but *could it have found the thing*.

 — the part it was missing

The design review's sharpest finding was that this bundle asserts the rule and
does not apply it to itself. It owes three things.

### 4.1 What a working loop looks like

Stated against the batch that broke it — the room's own messages, the ones in
[01-live-state.md](01-live-state.md). A passing run:

- makes **more than one** model call, with at least one tool result between them
  (the present failure makes exactly one);
- returns a **parseable envelope** (the present failure returns none);
- **advances its watermark** (the present failure has never once done so);
- produces **few, grounded** items rather than one per message — the batch is
  mostly chatter and the correct answer is a small number;
- and for the language tier specifically, does not gloss a proper noun as a
  literal reading of its words when a link in the same batch says otherwise.

The last is the motivating failure, and it is the only one of the five that
tests *comprehension* rather than *mechanics*.

### 4.2 The instrument already exists, and is unwired

Two pieces of prior art, neither invented here:

- **A harness that drives the real pipeline** — real doctrine, real registry,
  real multi-turn loop — with no flag connecting it to the eval runner. This is
  the correct instrument for the transport question in
  [16-open-items.md](16-open-items.md) §1 *and* for the loop's own acceptance,
  and it is why this bundle should not commission a standalone probe.
- **A judge-free corpus for exactly this tier**, graded by equality on an
  outcome rather than by a model, with the subject renamed so passing cannot be
  memorisation, controls in both over-correction directions, and a stated
  red-on-revert. It is deliberately held out of the runner's suites.

Both were built for the measurement problem this bundle has. Neither is cited
anywhere else in it. Wiring them is a smaller job than writing anything new, and
it is the concrete shape of §4.1.

### 4.3 What would prove this bundle wrong

The claim is: *the wrong-gloss class is caused by the agent being unable to
reach what it needs mid-reasoning, and grounding tools fix it.*

It is wrong if a loop **with** the tools still produces a gloss of the same
kind — if it can search, read the link, consult its own prior conclusions, and
still say a proper noun means the literal reading of its words. That would mean
the cause is comprehension, not access, and the design is treating a symptom.

That is a real, checkable condition, and it is the same shape as the
falsifiability commitment the keystone made and never discharged
([16-open-items.md](16-open-items.md) §9). Stating it here is what stops this
bundle inheriting the same debt.

## 5. Rollback

The Dreamer is off because of thirteen consecutive production failures. There is
a path to turning it back on and **no stated condition for turning it off
again**. That asymmetry is how a subsystem gets re-enabled and then quietly
degrades, which is the history this bundle documents.

Turn it off again when, over a bounded observation window:

- a scope's watermark **does not advance across N consecutive fires** — the
  ratchet signature, and the one that ran for three hours unnoticed;
- **unavailable** exceeds a threshold share of runs for any adaptor;
- any adaptor's **loop cost** exceeds its budget on a sustained fraction of
  runs, rather than occasionally;
- a granted tool is **never called** across the window — the grant is a cost
  with no benefit and the decision that made it should be revisited, not the
  flag left on.

The thresholds are for whoever builds this; the point is that each is a reading
someone can take, and that the first one is the failure that already happened.

## 6. The thresholds, decided as defaults

The rollback conditions in §5 are useless without numbers, and "waits on a
measurement" is how a rollback rule never acquires one. **These are the
defaults. They ship, and the first window of real traffic adjusts them.**

| condition | default | reasoning |
|---|---|---|
| watermark does not advance across N consecutive fires | **N = 3** | the ratchet signature. One stall is a quiet room; three is a stuck scope — the failure that ran three hours unnoticed |
| `unavailable` share of runs, per adaptor | **> 20% over 24h** | below that is a flaky provider; above it the tier is not doing its job and a null result cannot be trusted |
| loop cost over budget | **> 10% of runs, sustained over 24h** | occasional is a long batch; one run in ten is a budget that does not fit the work |
| a granted tool is never called | **0 calls in 100 runs** | the grant is a cost with no benefit, and D4's decision to make it should be revisited rather than the flag left on |

Every one is a reading someone can take from a counter that §2 already requires.
They are **defaults, not measurements dressed as decisions** — the distinction
this bundle keeps insisting on, applied to itself: a number chosen with its
reasoning stated is a decision; a number chosen and presented as evidence is not.

What the transport measurement in [16-open-items.md](16-open-items.md) §1 still
governs is the **budgets** in [05-transport.md](05-transport.md) — because the
observed generation exceeded every cap Nora configures, so those numbers are not
known to be connected to anything. That is a wiring question, not a sizing one,
and [07-loop.md](07-loop.md) §7 decides both branches of it.

## Status / Checkpoint

2026-08-24 — D7. §6's rollback thresholds are now defaults with reasoning rather than pending readings.
