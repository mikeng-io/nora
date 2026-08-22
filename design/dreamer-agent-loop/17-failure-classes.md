---
type: reference
title: "Failure Classes — Named Ways a Background Agent Goes Wrong"
description: "Principles stated for one tier and true of every adaptor: a capability nobody reaches for, a required field as a pressure to fabricate, an optional argument that downgrades a guarded operation, and a reset that resets itself."
tags: [nora, dreamer, principles, failure-classes, background-agents]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 02-instrument-blindness, rel: relates_to, provenance: asserted}
---

# Failure Classes — Named Ways a Background Agent Goes Wrong

Each of these was derived for one tier and is true of any adaptor. They are
gathered because they fit no single seam and because a named class is what
survives after the person who found it stops reviewing.

Most come from the note tier's spec, which is the most careful adaptor design in
the corpus and reasons about problems the runtime has not met yet.

## 1. The inert capability

> **A capability whose activation depends on the model spontaneously deciding to
> reach for it mostly does not get reached for.**

A prior capability shipped inert and stayed that way. The note tier names it as
the failure its whole trigger design exists to prevent, and gives the reason:

> *"'no note covers this' and 'I did not think to look' are **indistinguishable
> from inside a turn**."*

That is R-27 turned inward. An agent cannot reliably detect an **absence** —
absence and inattention produce the same felt experience, so any trigger phrased
as *notice that X is missing* is inert by construction.

**The fix is structural, and it is the reason an always-injected index earns its
prompt cost:** convert the judgement into a **comparison between two things both
present** — the injected entry, and what was just said. Where no comparison is
available, move the judgement off the turn entirely (§2).

This is the *cause* of the symptom [09-measurement.md](09-measurement.md) tracks
as "a granted tool nobody calls". A grant is not a capability; a grant plus
something present to compare against is.

## 2. Judge the act somewhere other than inside it

Assessment of a turn belongs **after** the turn, off the reply path, holding the
finished trajectory. Two properties, and both are unavailable mid-turn: the
assessor sees the **completed** path, and it competes for neither the turn's
budget nor its single reply.

The general form: every meta-decision about a live turn — *was this worth
recording, did it go wrong, should it be revisited* — wants a second loop with
its own termination condition, not a branch inside the first.

## 3. Never mint a value to satisfy a schema

Derived twice, independently, from opposite ends — once about promoting a note
to an event, once about a port's nullability:

> *every promoted field must trace to an actual message… 'Sometime in November'
> yields no event — **that is `unavailable`, not a guessed date. Nothing is
> inferred to satisfy a schema.**"*

> *a non-nullable field would force this tier to **mint a placeholder to satisfy
> a signature** — the 'invent a value to satisfy a schema' move already
> forbidden.*

Stated generally, and this is the most transferable line in the corpus:

> **A required field in an output schema is a pressure to fabricate, and a
> background writer with nobody watching will discharge that pressure.**

Two consequences the bundle depends on. It is the structural argument for
**permissive at the shared port, strict at each sink** (§4) — a required field
at a shared layer conscripts every tier that cannot fill it honestly. And it is
why fail-closed promotion is not merely cautious: the alternative is not a
missing row, it is a **plausible invented one**, which is worse in exactly the
way a wrong gloss is worse than no gloss.

## 4. Permissive at the shared layer, strict at each sink

Three separate decisions resolved the same way — a nullable field, an untyped
payload, an open vocabulary — with the tempting move being the opposite each
time:

> *it reads as **more rigorous** while quietly making the shared layer
> satisfiable by exactly one tier. That is **the failure mode of a port declared
> from one implementation, and it does not announce itself: the first tier
> passes everything.**"*

The price is paid where the type is known again — the sink — which must
`isinstance`-refuse rather than duck-type. *"An opaque field is exactly where a
wrong value travels a long way quietly."*

## 5. A reset that can reset itself is not a reset

Two instances, one shipped:

- A no-progress counter cleared by *any* write is cleared by **its own** writes,
  so a pass that keeps rewriting the same object resets its own brake forever.
  The fix is attribution: reset only on a write from **another actor class**.
- A time-based fire condition whose clock is only advanced by *doing work* is a
  **latch with no reset** — the condition never falls back below its threshold
  and a once-a-day pass runs every tick forever. This one shipped, and its fix
  is labelled a plan defect found during implementation.

The bundle's own backoff defect ([06-trigger.md](06-trigger.md) §2) is a third
instance: the ladder is cleared by the adaptor's own decision not to fire. **The
loop resets its own brake.**

> *stated plainly because the mechanism **reads correct without the qualifier and
> loops forever with it missing**.*

That sentence is why these belong in a document rather than in a review.

## 6. An optional argument downgrades a guarded operation

> *a call that meant to revise but omitted the id **silently becomes a create**.
> The omission does not error — it **downgrades a guarded operation into an
> unguarded one**.*

**When two operations have different preconditions, one verb cannot hold both,
and the collapse always fails toward the weaker guard.** Two verbs make the
mistake unrepresentable rather than discouraged.

It compounds: the collapse also erases the metric that would have revealed it,
because a single verb has no per-kind series to diverge.

## 7. Nothing on the background clock speaks

> *The Dreamer thinks at midnight; people read at 08:00.*

Background output reaches a person only by becoming an object that fires on the
**live delivery path**, under the normal egress guards, at a time somebody is
there. Routing it that way inherits the time-shift, the guards, a rate cap and —
the part that is governance rather than plumbing — **attribution**: the nudge
spends the quota of the person whose message established it, never the agent's.

**This collides with the bundle's own act-verb line.**
[11-disclosure.md](11-disclosure.md) §4.1 blocks the scheduling verb as an act
verb, which removes the only mechanism this design has. The resolution
consistent with both is that the schedule leaves as a **proposal through a
sink** — which is the unresolved cross-scope write problem
([08-state.md](08-state.md) §10): an adaptor keyed on one thing writing into a
store keyed on another has no expressible path today.

## 8. An unordered read reads as a state change

Any block assembled from a set must have a total order, or the same state
renders differently between turns and the model observes churn that did not
happen. Cheap to get right, invisible when wrong.

## 9. A cap belongs in the constructor, not the renderer

A field with no cap in its type **cannot enter a prompt at all** — the renderer
is then forced to choose between unbounded cost and silent truncation, and
silent truncation is the loss this bundle spends
[02-instrument-blindness.md](02-instrument-blindness.md) on.

> *truncation at render is silent loss; **a constructor that refuses is not**.*

## 10. Refuse loudly; never accept and forget

A store that cannot record something **refuses**; it does not accept and drop.
Three tiers derived this independently, which is an argument and not yet
authority — it has no decision-index entry and no named test, and until it does
it is a convention rather than a rule.

That gap is itself the point: **cross-session agreement is evidence; a rule is
real when it has an entry and a test.**
