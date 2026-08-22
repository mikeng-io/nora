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

**A tier that declares metrics must ship them.** One tier declares ten and ships
none, so its own stated failure-detection is unreadable and a deferred piece of
work waits on evidence that structurally cannot be collected.

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


## 4. This bundle's own acceptance — the part it was missing

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

## 6. What this seam does not decide

Which numbers. Every threshold above, and every budget in
[05-transport.md](05-transport.md), waits on the measurement in
[16-open-items.md](16-open-items.md) §1 — because the observed generation
exceeded every cap Nora configures, so no number currently in the file was in
force. Sizing before that measurement would be choosing values for knobs not
known to be connected.
