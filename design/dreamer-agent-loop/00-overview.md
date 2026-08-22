---
type: design
title: "The Dreamer Agent Loop — Revision Overview"
description: "The Dreamer was designed as a research agent and built as a pipeline. Five seams — transport, trigger, loop, state, measurement — each collapsed into something else. Entry point for the revision layer."
tags: [nora, dreamer, agent, tools, revision, architecture]
timestamp: '2026-08-22'
supersedes: >
  Framing in docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md §1
  (stale measurements), 2026-08-20-fact-tier-design.md §4.2 (no fact-read tool)
  and §5's Phase-A-is-tool-less clause ONLY — that section's deterministic
  filter and recurrence argument are relied on, not superseded — and
  2026-08-20-dreamer-prompt-substrate-design.md §2.1 (one doctrine, shared by
  every adaptor). Defers the rest.
status: proposed
edges:
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
  - {dst: 02-instrument-blindness, rel: motivated_by, provenance: asserted}
  - {dst: 03-no-agent-loop, rel: motivated_by, provenance: asserted}
  - {dst: 04-designed-vs-shipped, rel: motivated_by, provenance: asserted}
---

# The Dreamer Agent Loop — Revision Overview

> Entry point for the 2026-08-22 revision layer. It does **not** rewrite the
> three 2026-08-20 specs in place; it supersedes their framing where it
> conflicts with what shipped or what is now known, and defers the rest.

## 1. The diagnosis

**The Dreamer was designed as a research agent and built as a pipeline.**

Four of the five participants hold no tools at all — three make a single
completion call, and the fourth makes none by design. The fifth has a real loop
and can only look **outward**, at the open web; it cannot consult the room, the
transcript, Nora's memory, the notes, or its own prior conclusions.

Every tool the operator originally named — *the previous calls, the image, the
previous transcript, the user profile* — is absent from every shipped adaptor,
and **no decision removed them** ([04](04-designed-vs-shipped.md)).

## 2. The evidence

**963 messages processed. Two rows written. Both wrong.** That is the entire
lifetime output of every tier. The two rows are contradictory glosses of one
phrase, both live, rendered side by side into Nora's prompt with no marker that
they disagree. Full accounting in [01-live-state.md](01-live-state.md).

## 3. The architecture — five seams, each collapsed into something else

This is what makes it a design rather than a bug list. Every finding from six
investigations and four adversarial reviews lands in exactly one row.

| seam | should be | is today | node |
|---|---|---|---|
| **Transport** | the Dreamer's own, sized and measured for research | the tag tier's, inherited wholesale | [05](05-transport.md) |
| **Trigger** | each adaptor declares when it wants to run, with a floor between passes | a side-effect of rendering a prompt, on a 300-second clock | [06](06-trigger.md) |
| **Loop** | what one turn is — tools, termination, partial progress | absent for four of five participants | [07](07-loop.md) |
| **State** | knowable-before is an input; discovered-during is a tool | everything pre-rendered by Python | [08](08-state.md) |
| **Measurement** | every seam ships its instrument | six blind layers | [09](09-measurement.md) |

Two properties of that table are the argument:

**They are not independent.** A trigger that cannot be evaluated cheaply forces
the runtime to render before deciding, which is why concurrency cannot be
bounded. A transport sized for chat makes every loop budget meaningless. Fixing
one without the others produces a subsystem that fails differently.

**And the cadence is off by about two orders of magnitude.** Honcho's dreamer —
in a dependency this project already runs — uses an **8-hour floor** between
passes, triggered by accumulation and delayed until the room goes quiet. Nora's
runs a 300-second clock with **no floor at all** ([06](06-trigger.md) §3). A
pass that runs every five minutes is not dreaming, it is polling.

**Three of the five were never decided — they were inherited.** Transport from
the tag path, trigger from a watermark-driven consolidator that predates the
runtime, measurement from nowhere at all. Only State was reasoned about, and it
was reasoned about narrowly ([08](08-state.md) §2).

## 4. Why now

The runtime landed roughly one day before this was written. There is no legacy
to protect, and two more tiers are queued to migrate onto it — each of which
would inherit the same transport, the same trigger, and the same silence.

The cost of separating the seams now is a config section, a port method, and a
node that says what a turn is. The cost of not doing it is that the error
propagates to every adaptor built afterwards and becomes indistinguishable from
intent — which is precisely what already happened to the tool subsets.

## 5. Decisions

Proposed, not ratified. Each is argued in the node named. Findings are marked as
such rather than dressed as decisions.

| # | | Statement | Node |
|---|---|---|---|
| F1 | *finding* | An adaptor with no tools is a completion, not an agent; the stopping condition is satisfied on pass one | [03](03-no-agent-loop.md) |
| F2 | *finding* | The narrowing from eleven tools to four was decided by nobody — it is three documents and one mechanism that never met | [04](04-designed-vs-shipped.md) |
| F3 | *refutation* | Reading is **not** made safe by the egress screen; a room read changes the content class the tier persists | [11](11-disclosure.md) |
| **D1** | decision | The Dreamer gets its **own transport**, complete rather than a partial override, and verified against the wire rather than assumed | [05](05-transport.md) |
| **D2** | decision | An adaptor **declares its trigger condition and a floor between passes**; the period is **not fixed**, and the shape is event-driven. "Did not fire" is a third outcome | [06](06-trigger.md) |
| **D3** | decision | **Knowable-before-the-call is an input; discovered-during-reasoning must be a tool** | [08](08-state.md) §1 |
| **D4** | decision | A tier contributes read access through the existing registry seam — not a bespoke mechanism | [08](08-state.md) §10 |
| **D5** | decision | Phase A **gains tools**, and must **not** deduplicate against existing candidates — that would destroy the recurrence signal the filter ranks on | [08](08-state.md) §9 |
| **D6** | decision | Room access splits by key kind: scope-keyed proceeds; workspace-keyed is **blocked**, and the cheaper answer is a bounded input | [10](10-room-access.md) |
| **D7** | decision | **Every seam ships its instrument**, this bundle's own change first — with a stated acceptance reading and a rollback condition | [09](09-measurement.md) |
| **D8** | decision | Doctrine becomes shape-aware; the audience field stops being decorative | [13](13-doctrine.md) |
| **D9** | deferral | The language tier's absence of supersession is a **designed cost**; reversing it needs its own argument | [12](12-self-correction.md) |
| **D10** | sequencing | Phase 3 is how the objective-text freeze legitimately lifts | [14](14-migration-and-governance.md) |

**D5 is the one most easily got wrong.** It reads as "give Phase A tools", and
the second half is the load-bearing part: an extractor that suppresses claims it
has seen before destroys the signal that decides what is worth researching.

## 6. Reading order

**Evidence** — [01](01-live-state.md) what is true now · [02](02-instrument-blindness.md) why it stayed invisible · [03](03-no-agent-loop.md) the mechanism · [04](04-designed-vs-shipped.md) how it got this way

**The seams** — [05](05-transport.md) · [06](06-trigger.md) · [07](07-loop.md) · [08](08-state.md) · [09](09-measurement.md)

**Constraints on them** — [10](10-room-access.md) what may be reached · [11](11-disclosure.md) what must be true first · [12](12-self-correction.md) correcting yourself · [13](13-doctrine.md) what the model is told

**Execution** — [14](14-migration-and-governance.md) sequencing, numbers, tests · [15](15-roadmap.md) every tier's state and owner · [16](16-open-items.md) what is unresolved

**Cross-cutting** — [17](17-failure-classes.md) named failure classes, true of any adaptor · [18](18-flows.md) the tiers run end to end · [19](19-between-tiers.md) how they relate

> **If you only read two nodes:** [18](18-flows.md) to see how a tier actually
> runs, then [01](01-live-state.md) for what it has actually produced. The gap
> between those two is the whole bundle.

## 7. The critical path

1. **Measure the transport** ([16](16-open-items.md) §0 and §1, one run). Does it
   sustain tool calls and carry reasoning between turns; which ceiling actually
   binds. **Everything else is downstream of this** — and if the answer is no,
   the loop's shape changes rather than its numbers.
2. **Give the Dreamer its own transport** ([05](05-transport.md)) sized against
   that reading.
3. **Declare triggers** ([06](06-trigger.md)), which is what makes a concurrency
   bound expressible at all.
4. **Wire the tier read tools** ([08](08-state.md)) and the scope-keyed room
   access ([10](10-room-access.md)).
5. **Turn it on for one scope**, with the acceptance reading from
   [09](09-measurement.md) §4 and the rollback condition from §5 in place before
   the flag flips, not after.

## 8. What this bundle does not decide

- **The fact tier's store** — owned elsewhere, unchanged here.
- **Standing / the debrief** — owned by another session; its roadmap is
  consolidated in [15](15-roadmap.md) §6 with that session's agreement. This
  bundle records it, it does not own it.
- **Entity resolution** — a shared dependency of three tiers, owned by nobody.
  Named in [12](12-self-correction.md) §4; its mechanism stays open.
- **Which model the Dreamer runs** — closed by operator decision, carrying a
  falsifiability commitment never discharged because no loop has ever completed
  ([16](16-open-items.md) §9).

## Status / Checkpoint

2026-08-22 — restructured around the five seams after four adversarial reviews
(fact-check, coherence, design-quality, completeness) found ~8 refuted claims,
~15 overstated, 5 internal contradictions and 9 propagated concepts. The
forensic nodes 01–04 were independently reproduced and survive; the design half
was rewritten rather than patched.

Merged with **no** `design/09-decision-index.md` entry: DEC-135 and R-57 remain
reserved and unclaimed, and R-57's text is still undrafted — see
[14](14-migration-and-governance.md) §2. `[dreamer] enabled = false`; the loop
reports `0 adaptor(s)`.
