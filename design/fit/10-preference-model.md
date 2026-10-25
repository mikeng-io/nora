---
type: design
title: "The preference model: facets with evidence, decay and uncertainty"
description: "What a preference is (a facet of a domain, with a posterior that carries its own uncertainty), the evidence kinds that feed it and their weights, how social engagement enters, time decay by evidence kind, the unit's aggregate, the origin model for logistics, and how it relates to Character."
tags: [nora, fit, preferences, evidence, decay, beta, social-engagement, presence]
timestamp: '2026-10-25'
edges:
  - {dst: ../cognition-loop/22-social-stream.md, rel: depends_on, provenance: asserted}
  - {dst: ../cognition-loop/25-rollup.md, rel: depends_on, provenance: asserted}
---

# 10 · The preference model

## 1. A preference is a facet with a posterior

A **facet** is one attribute of a domain's items, from a vocabulary the
recipe declares ([30](30-recipes.md)): `cuisine:japanese`, `price:$$`,
`vibe:loud`, `genre:co-op-survival`, `players:4+`, `platform:pc`,
`travel:max-40min`.

For each `(person, facet)` the model keeps a **Beta posterior**: `α` (weighted
evidence for), `β` (weighted evidence against), from a weak prior
(`α₀ = β₀ = 1`). From it come the two numbers the ranker needs:

- **affinity** = `α / (α + β)`, between 0 and 1;
- **confidence** = how much evidence there is (`α + β` against the prior).

Beta is chosen because the evidence is mostly binary in kind (liked or not,
went or did not, accepted or ignored), it updates with addition, it carries
uncertainty the bandit in [40](40-feedback.md) needs, and every number is
traceable to the evidence rows that moved it. A facet with no evidence has
affinity 0.5 and no confidence, which the ranker reads as **unknown**, never as
neutral agreement ([20](20-ranker.md) §5).

**Hard preferences are not posteriors.** "I don't eat beef", "I don't own a
PS5", "I can't do Fridays" are **constraints**, stored as dated facts
(`fact.valid_from`/`valid_to`) and applied by the filter step, never averaged.

## 2. Evidence kinds

Each row of evidence is `(person, facet, polarity, kind, weight, at, cites)`.
The model extracts it from unstructured input; code assigns the weight by
kind and never lets the model choose it.

| kind | example | starting weight | source |
|---|---|---|---|
| **stated** | "I love ramen", "not another escape room" | 3 | messages |
| **revealed: went** | an outing at a ramen place | 3 | `rollup_outing` |
| **revealed: played** | 6 hours of a co-op survival game this week | 3 (scaled by hours, capped) | play sessions |
| **social: posted** | a member's own post or story at a bar, a game clip | 2 | `social_item` |
| **social: shared** | a reel or link a member sends the group | 1.5 | `social_item`, links in rooms |
| **social: reacted** | a reaction to someone else's reel or suggestion | 0.5 | `message_reaction` |
| **response** | her proposal of a ramen place was picked, or ignored | 2 picked / 1 against when ignored | `rollup_response` ([40](40-feedback.md)) |
| **told by others** | "Ana hates spicy food" | 1 | messages; never a constraint on its own |

The weights are hand-set to start and are recipe data. They are not learned
until the replay measurement ([50](50-verification.md) §1) has enough history
to show a change beats noise.

**Social engagement is evidence, not only a feed.** A shared reel says "I find
this interesting" about its facets; a member's own post at a venue is revealed
preference, weaker than a co-located outing because it may be one visit. The
extraction runs once per `social_item` when the social stream records it, and
its evidence cites the item.

## 3. Decay

Tastes move, so evidence fades. Each kind has a **half-life** (stated and
revealed: 180 days; social: 60 days; reactions: 30 days, all recipe data, to
be sized from replay). The posterior is computed at read time from the
evidence rows with their decayed weights, so changing a half-life recomputes
everything without a migration and nothing is ever overwritten.

## 4. The unit's aggregate

A unit preference is **not** a person's posterior averaged. It is computed for
the people a proposal is for (the attendees of the event, the room's
members), at ranking time, by the group strategy in [20](20-ranker.md) §6. The
model stores only per-person evidence, plus unit-level evidence that belongs
to no one person ("we always end up at the same bar" is the unit's).

## 5. Where people will be: the origin model

Logistics needs each person's starting point at the proposal's time `T`, and
the Relevance Gate measurement (CMS, 2026-09-09/10) is the warning: a
judgement about time whose input carries no time is answered wrongly with
nothing reporting it. So the origin is a **distribution with provenance**,
from the strongest source available:

1. **a dated fact** for that time ("working late on the 31st, finishing about
   21:00" with its place), from conversation;
2. **the event's own plan** (someone said they are coming from work);
3. **place rhythm**: `presence_place_rhythm`'s mean arrival and departure per
   place by hour of week, giving a probability per place at `T`;
4. **home**, the derived place of kind home, as a fallback only.

Each person's origin carries which rung it came from. A proposal whose
logistics rests on rung 3 or 4 for someone **says so** ("assuming Kit leaves
work around 7"), and rung 4 alone never ranks a candidate first.

## 6. Relation to Character

Character is prose: what a person is like, cited ([cognition-loop 12](../cognition-loop/12-schemas.md)
§4). The preference model is numbers over a vocabulary, computed by code from
cited evidence. They are kept apart because ranking needs numbers with
uncertainty, and Character's prose cannot be replayed. Character's `What they
care about` may cite preference facets; the preference model never reads
Character, so it cannot be built from a summary of itself.

## 7. Custody and disclosure

Preference evidence is derived data about a person and follows the house rule:
store everything, filter at read. Each evidence row carries its source's
disclosure requirements (lineage, exclusions, location restrictions), the
contract the turn records in
[standing-ledger 30](../standing-ledger/30-turn-records.md) §5 use, and a
proposal's explanation renders only reasons whose evidence passes for the room
it is posted in. Logistics may use a location internally; "convenient for Kit
coming from work in Kwun Tong" is a disclosure, and passes only the room's
`location` gate.
