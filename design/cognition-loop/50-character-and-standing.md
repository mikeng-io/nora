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
behaves, in prose under the fixed sections of [12](12-schemas.md) §4, with
cited evidence, revised by `character_revise` as an append-only chain (the
revision chain the culture store already has, design/culture-tier/).

| entity | the account holds | evidence it reads |
|---|---|---|
| **person** | how they talk, what they care about, how they decide, whom they are close to, what they reliably do (organise, cancel, turn up late) | the Record (their messages, presence patterns, social items and edges), the Register (events attended, commitments kept), memory |
| **room** (its culture) | how the room talks and decides, what it calls itself | the room's Record, the Register, its members' Characters |
| **unit** (its culture) | what the unit is, across its rooms | the rooms' cultures, the Register |
| **Nora** | what she is like in this unit, as its people have met her | her Record rows **paired with the responses to them**; never her rows alone |

**Why not memory.** Honcho's model of a person was measured on 2026-08-06 as
about 88% events and 12% traits (GLOSSARY, **representation**): it records
what someone did, not who they are, and its derivation is not ours to steer.
Character reads memory as one source among several and writes an account of
its own.

**Nora's Character is not her doctrine.** Doctrine is authored and wins. Her
Character is derived — how she has actually come across here — and may be
read beside the doctrine, never over it.

## 2. Standing: from room to unit and person

Standing keeps its meaning (her held position toward a subject) and gains
two subjects:

| subject | exists | holds |
|---|---|---|
| room | yes (design/standing-compact/) | where the room's conversation stands for her, plus the working set |
| **unit** | new | where she stands in the unit: what she is following, what she owes, what she thinks is going on |
| **person** | new | her position toward one person: what is between them, what she owes them, how they receive her |

Standing may cite every tier below it, including Character ("they tend to
change plans late, so I hold this one loosely").

## 3. The echo rule

A position built from her own words drifts: each pass agrees a little more with
the last. So her own rows ground her Standing only as *what I said*, and her
own Character only together with how people responded. A revision of her
Character whose only evidence is Nora is refused; a Standing line resting
only on her rows may say only what she said (`I owe`, `I hold open`), never
what is so.

## 4. Correction

An account nobody sees cannot be corrected, so:

- **The portal shows each unit's accounts** with the evidence behind every
  line, to the people of that unit, under lineage.
- **A dispute from chat**: a person may say a line about them is wrong; the
  tag turn records a `character_dispute` against that line, the next session
  must re-evidence or remove it, and a disputed line never renders until it
  has.
- **Lines decay**: a line not re-evidenced within its kind's horizon moves to
  `Unsure` ([12](12-schemas.md) §8).
- **Induction needs instances**: a line about what someone reliably does cites
  a Rollup aggregate ([10](10-tiers.md) §2 rule 5).

## 5. Disclosure

Character and Standing are per unit and render only in that unit's rooms. A
person in two units has two accounts, and neither reads the other. Within a
unit, **lineage** decides per room ([10](10-tiers.md) §3): a paragraph resting
on presence renders only where members' locations may be shown, and is
otherwise dropped whole. An account is an input to her judgement, never text
she recites.
