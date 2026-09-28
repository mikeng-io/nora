---
type: design
title: "Character and Standing — what an entity is like, and where Nora stands toward it"
description: "Character per entity (person, room, unit, Nora) as one shape; why it is not memory (Honcho); Nora's own Character beside the doctrine; Standing widened from room to unit and person, kept as an append-only chain of undated positions; the echo rule; correction by the subject; disclosure per section."
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
| **person** | how they talk, what they care about, how they decide, whom they are close to, what they reliably do (organise, cancel, turn up late) | the Record (their messages, social items), the Rollup (presence patterns, tie, play profile), the Register (events attended, commitments kept); memory as a hint only |
| **room** (its culture) | how the room talks and decides, what it calls itself | the room's Record, the Register, its members' Characters |
| **unit** (its culture) | what the unit is, across its rooms | the rooms' cultures, the Register |
| **Nora** | what she is like in this unit, as its people have met her | her Record rows **paired with the responses to them**; never her rows alone |

`character_revise` for a person refuses anyone who is not a listed person of
the unit's rooms.

**Why not memory.** Honcho's model of a person was measured on 2026-08-06 as
about 88% happenings and 12% traits (GLOSSARY, **representation**): it
records what someone did, not who they are, and its derivation is not ours
to steer. Character reads memory as a hint, never cites it, and writes an
account of its own.

**Nora's Character is not her doctrine.** Doctrine is authored and wins. Her
Character is derived — how she has actually come across here — and may be
read beside the doctrine, never over it.

## 2. Standing: from room to unit and person

Standing keeps its meaning (her held position toward a subject) and gains
two subjects:

| subject | exists | holds |
|---|---|---|
| room | yes (design/standing-compact/) | where the room's conversation stands for her, plus the working set |
| **unit** | new | what she is following, what she is watching for, what she owes |
| **person** | new | her position toward one person: what is between them, what she owes them, how they receive her |

Its modes are `deduced`, `abduced` and `said` ([10](10-tiers.md) §4). It may
cite every tier below it, including Character ("they tend to change plans
late, so I hold this one loosely").

**The head stops being a lossy fold, which reverses standing-compact.**
Standing already keeps its history: `standing_revision` (migration 0059,
#352) is append-only and written in the same transaction as the head. What
changes is the head. Today every fold rewrites it whole, "lossy and rewritten
every turn" (design/standing-compact/00-intent.md), and folds were measured
dropping at least 10 open dated plans in 206 revisions
(design/group-situation/10-standing-drops-plans.md): the chain kept them, but
the head every turn reads did not. Now the head is sectioned
([12](12-schemas.md) §5), each section carries its own support and age, and
a revision changes only the sections its evidence touches.

**Undated positions only.** `I hold open` and `What I am following` hold what
has no date. A dated plan is the note's: the operator withdrew Standing's
`open_plans` (design/standing-compact/40-open-plans.md; its PR #565 was never
merged). Standing has no `Unsure`: a section past its horizon is dropped
([12](12-schemas.md) §8).

## 3. The echo rule

A position built from her own words drifts: each pass agrees a little more
with the last. So her own rows ground her Standing only as `said`, and her
own Character only together with how people responded. A revision of her
Character whose only evidence is Nora is refused; a Standing line resting
only on her rows says only what she said (`I owe`, `I hold open`), never what
is so.

## 4. Correction

An account nobody sees cannot be corrected, so:

- **The portal shows each person the account about them**, plus the culture of the
  rooms they are listed in, with the evidence behind every line, lineage
  evaluated against the viewer's rooms.
- **Only the subject may dispute a line about them.** The tag turn records a
  `character_dispute` as a Record item authored by that person
  ([10](10-tiers.md) §1); the next session must re-evidence or remove the
  line, and a disputed line never renders until it has.
- **Lines decay**: a Character line not re-evidenced within its horizon moves
  to `Unsure`; a Standing line is dropped ([12](12-schemas.md) §8).
- **Induction needs instances**: a line about what someone reliably does cites
  a Rollup aggregate ([10](10-tiers.md) §2 rule 5).
- **Contradiction is kept, not resolved on write**: two cited claims that
  conflict stay side by side, marked, until a later revision's evidence
  settles one ([80](80-prior-art.md) §4).

## 5. Disclosure

Character and Standing are per unit and render only in that unit's rooms. A
person's Character renders only in rooms where that person is a listed
member. Within those rooms lineage decides per section ([10](10-tiers.md) §3):
a section resting on presence renders only where the room's `location` flag
is on and the DisclosureGuard passes for the person; a section that fails is
not shown, and the rest of the account is.

A person in two units has two accounts, and neither account reads the other;
their shared input is the person-scoped Rollup (stays, places, rhythm), read
under lineage. The peer card is not scoped per unit (it is unscoped inside
its Honcho workspace, DEC-214), so it can mix the units of one workspace:
nothing from it is Character evidence.

An account is an input to her judgement, never text she recites.
