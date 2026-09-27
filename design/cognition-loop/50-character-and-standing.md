---
type: design
title: "Character and Standing — what an entity is like, and where Nora stands toward it"
description: "Character per entity (person, room, unit, Nora) as one shape; why it is not memory (Honcho); Nora's own Character beside the doctrine; Standing widened from room to unit and person; the echo rule that keeps her from agreeing with herself."
tags: [nora, character, culture, standing, self, persons, social-cognition]
timestamp: '2026-09-28'
edges:
  - {dst: 10-tiers, rel: depends_on, provenance: asserted}
---

# 50 · Character and Standing

Both tiers are later steps ([70](70-migration.md)); this node fixes their
shape so the loop's verbs and keys are right from the start.

## 1. Character: one shape, four kinds of entity

An **account** per `(unit, entity)`: what the entity is like and how it
behaves, in prose, with cited evidence, revised by `character_revise` as an
append-only chain — the shape the culture tier already has
(design/culture-tier/).

| entity | the account holds | evidence it reads |
|---|---|---|
| **person** | how they talk, what they care about, how they decide, whom they are close to, what they reliably do (organise, cancel, turn up late) | the Record (their messages, presence patterns, social items and edges), the Register (events attended, commitments kept), memory |
| **room** (its culture) | how the room talks and decides, what it calls itself | the room's Record, the Register, its members' Characters |
| **unit** (its culture) | what the circle is, across its rooms | the rooms' cultures, the Register |
| **Nora** | what she is like in this circle, as the circle has met her | her Record rows **paired with the responses to them**; never her rows alone |

**Why not memory.** Honcho's model of a person was measured on 2026-08-06 as
about 88% events and 12% traits (GLOSSARY, **representation**): it records
what someone did, not who they are, and its derivation is not ours to steer.
Character reads memory as one source among several and writes an account of
its own.

**Nora's Character is not her doctrine.** Doctrine is authored and wins. Her
Character is derived — how she has actually come across here — and may be
read beside the doctrine, never over it.

## 2. Standing: from room to unit and person

Standing keeps its meaning (GLOSSARY: her held position, a *stance*) and gains
two subjects:

| subject | exists | holds |
|---|---|---|
| room | yes (design/standing-compact/) | where the room's conversation stands for her, plus the working set |
| **unit** | new | where she stands in the circle: what she is following, what she owes, what she thinks is going on |
| **person** | new | her position toward one person: what is between them, what she owes them, how they receive her |

Standing may cite every tier below it, including Character ("they tend to
change plans late, so I hold this one loosely").

## 3. The echo rule

A stance built from her own words drifts: each pass agrees a little more with
the last. So her own rows ground her Standing only as *what I said*, and her
own Character only together with how people responded. A revision of either
whose only evidence is Nora is refused ([10](10-tiers.md) §2, rule 4).

## 4. Disclosure

Character and Standing are per unit and render only in that unit's rooms. A
person in two units has two accounts, and neither reads the other. What a
room is shown about a person's Character is decided by the same egress
screens a reply passes; an account is an input to her judgement, never text
she recites.
