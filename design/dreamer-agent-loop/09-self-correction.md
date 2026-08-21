---
type: design
title: "Self-Correction — What It Means Per Tier, and Why Language Has None"
description: "The fact tier can supersede itself. The language tier structurally cannot, by a design decision that fixed an earlier bug. Two contradictory glosses coexisting is the schema working, and reversing it needs its own argument."
tags: [nora, dreamer, supersession, lexeme, facts, entity-resolution]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 06-tool-surface, rel: depends_on, provenance: asserted}
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
---

# Self-Correction — What It Means Per Tier

## 1. The correction this node exists to make

The two contradictory `big walk` glosses look like a bug. **They are not.**

The lexeme key includes the sense, and that is a deliberate fix: without it, a
word acquiring a second meaning **silently overwrote the first**, or merged
counts from unrelated senses. A second sense getting a second row is the
correct behaviour for the failure it was designed against.

What it costs is that a *better* gloss is also a new row. There is no
comparison, no ranking between senses of one form, no contradiction detection,
and no delete path. The only removal mechanism is time decay — and decay fires
on **absence**, so it cannot distinguish a wrong-but-recent gloss from a right
one. That tier's own open-risks list accepts this: *"A generated sense can be
wrong. The quote beneath makes it visible, not impossible."*

**Decision (D6): this is a designed cost, and reversing it is its own decision
with its own argument — not a patch applied while doing something else.** Any
proposal to add supersession to the language tier must say what happens to the
case the compound key was introduced to fix.

## 2. The asymmetry, stated correctly

The keystone argues *"proving a fact and understanding a term are the same kind
of work: research."* The two tiers were then built with opposite epistemics:

| | fact tier | language tier |
|---|---|---|
| three-state verdict | ✅ | — |
| explicit supersession | ✅ | — |
| retirement reason recorded | ✅ | — |
| negative cache | ✅ | — |
| dependency relation | ✅ (unwalked) | — |
| removal mechanism | contradiction | **time only** |

The tier whose founding failure *is* the wrong `big walk` gloss got the weaker
epistemics. If the keystone's claim is right, that asymmetry is the thing to
close — and the fact tier is the worked example of what closing it looks like.

## 3. Reading your own output is the precondition for correcting it

The live evidence is one step sharper than "there is no supersession
mechanism". The second lexeme run got **closer to the truth** — it knew the
group *plays* the thing, it knew about gourds, real game content. It could not
correct the first gloss because it **could not see it**.

Even with a supersession mechanism, an adaptor that cannot read its own prior
output has nothing to supersede *against*. That is why
[06-tool-surface.md](06-tool-surface.md) puts the own-tier read first for every
adaptor with a store: it is the precondition, and the schema question is
downstream of it.

The order matters for sequencing: grant the read, observe whether adaptors
actually reach for it and what they find, and let that evidence inform the
supersession design rather than guessing at it now.

## 4. Entity resolution is the shared dependency

The fact-tier spec states the stake plainly:

> *"`big walk` / `Big Walk` / 大步行 must resolve to one subject or the tier
> fragments into near-duplicates that can never contradict each other — **a
> fact tier that cannot contradict itself is decoration**."*

The alias mechanism exists in the schema; nothing populates it. Which means:

- **The fact tier is on course to reproduce the language tier's failure
  exactly.** Both would hold near-duplicate subjects that cannot meet.
- **A search tool over either store is weak until resolution lands.** A search
  that mostly misses teaches the model that searching is useless, which is
  worse than no tool. This is a real constraint on
  [06-tool-surface.md](06-tool-surface.md) and should temper expectations of
  the first version.
- **The live language rows already demonstrate it** — two forms differing only
  in capitalisation, with the surface and canonical fields swapped between the
  two observations in opposite directions, and nothing downstream normalising.

The mechanism stays open here. What this bundle records is that it is a
**shared** dependency of both tiers, not a fact-tier detail, and that the
tool-surface work should not be scoped as though resolution were already
solved.

## 5. Provenance points at the machine, not the belief

A fact records which Dreamer run established it. It does not record which human
asserted the claim.

For a tier whose job is belief, that is the wrong direction. Corroboration and
disagreement are properties of *who said it*:

- **Occurrence count conflates "one person said it three times" with "three
  people said it once."** The first is emphasis; the second is consensus. The
  ranking that decides what is worth adjudicating treats them identically.
- **There is no representation for the room disagreeing with itself** — a
  different thing from the web contradicting the room, and arguably the more
  interesting signal.

The extraction input compounds this: it renders message id and body only,
discarding the sender, the timestamp and the reply threading that were already
fetched. So the model is asked *who is claiming what* from anonymous, timeless,
unthreaded fragments — and the exclusion rule in its own objective ("nothing
about a person in this room") is structurally uncheckable from the input it
receives.

Notably the **language** tier's prompt already includes the speaker. The fact
tier regressed from an existing in-repo pattern.

**This bundle does not redesign the fact shape.** It records that the
assertion — who, when, in reply to what — is currently discarded at the one
point where it is free, and that any future work on corroboration or on
room-level disagreement starts by not throwing it away.

## 6. What is designed and unwalked

Recorded so the revision does not present them as new:

- **Transitive supersession.** The dependency relation makes "which facts rest
  on this one" a single indexed query. Nothing walks it, so a fact can stand on
  a retracted foundation indefinitely with no signal.
- **Premise cardinality.** A fact warranted by other facts is not required by
  anything to actually have any.
- **Two warrant kinds have no writer at all.**
