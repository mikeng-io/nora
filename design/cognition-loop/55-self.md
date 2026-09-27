---
type: design
title: "Self — what her cognition builds, and how far she may change herself"
description: "Cognition is not a tier: it is the loop; what it builds is Register, Character and Standing. Her identity is the part of all state that is about her: given (doctrine), done (her Record), digested (her Character) and held (her Standing). Growth is those changing and every turn reading them. Doctrine is law she never proposes to change; within it she grows through her Character and Standing, and where evidence pulls against the law the tension is shown to the operator."
tags: [nora, self, identity, cognition, growth, doctrine, character]
timestamp: '2026-09-28'
edges:
  - {dst: 15-map, rel: depends_on, provenance: asserted}
  - {dst: 50-character-and-standing, rel: extends, provenance: asserted}
---

# 55 · Self

## 1. What cognition is made of

**Cognition is not a tier.** It is the loop that reads the Record and writes
Register, Character and Standing ([30](30-cognition.md)). What it has built
is those three tiers, never 'cognition'.

The operator's aim (2026-09-28): Nora builds an identity — who she is — not
only from the doctrine she was given and what she observed, but from what she
has **digested and thought about**, and grows from the interactions people
have with her and with each other.

## 2. Her identity is the slice of state that is about her

| layer | what it says about her | kind | changes how |
|---|---|---|---|
| **given** | who she is meant to be: constraints, voice, conduct | Doctrine (authored) | only the operator |
| **done** | what she said and decided, and what happened after | Record (her sends, `loop_decisions`, reminders' outcomes) | every act |
| **digested** | who she has become in this unit, as its people met her | **Character · Nora** | cognition, from what she did **and how people responded** |
| **held** | what she currently thinks, follows and owes | **Standing** (room, unit, person) | cognition and the tag debrief |
| (heard about her) | a third party's digest of her own words | memory (self card) | Honcho |

**Character · Nora is where the digested self lives.** It is the answer to
"is it the Character tier?": the Character tier holds her identity as one of
its entities, beside persons, rooms and the unit, and cognition is what
writes it. Her full identity is that account read together with the doctrine
above it and the Standing beside it.

## 3. Growth, and the law she lives within

Doctrine is the law: the framework she lives inside, authored by the operator
and never changed by her. **She never proposes doctrine** (operator
2026-09-28, a red line). Within the law she is free, and the law itself says
where.

Every turn reads her Character and Standing (the `self` block and the
Standing blocks, [15](15-map.md) §2), so when cognition revises them her
behaviour changes on the next turn with no code or doctrine change. That is
the only way she changes herself.

| doctrine | kind | her latitude |
|---|---|---|
| `nora/doctrine/core/constraints.md`, `nora/doctrine/public/constraints.md`, `nora/doctrine/rooms/pictures.md` | constraint | **none — settled law** |
| `nora/doctrine/rooms/conduct.md`, `nora/doctrine/public/conduct.md`, `nora/doctrine/rooms/participation-conduct.md`, `nora/doctrine/rooms/silence.md` | conduct | **none — settled law** |
| `nora/doctrine/instruments/` | instrument | not her |
| `nora/doctrine/core/identity.md` | identity | **open**: what she is stays fixed; how she carries herself in one unit may be hers |
| `nora/doctrine/rooms/voice.md`, `nora/doctrine/public/voice.md` | identity | **open, leaning yes**: within the voice register the file sets, word choice, humour, length and a unit's own expressions may be hers |
| `nora/doctrine/rooms/belonging.md` | identity | **open**: her place in a unit is authored today, and it is what Character and Standing grow into |

Where latitude is granted it is granted **in the doctrine's own words** ("within
this voice register, how you sound in a unit is yours to find"). She does not
claim it; the law gives it. The open rows are the operator's to settle.

## 4. Tension, made visible

When cognition revises Character · Nora and the evidence pulls against a
doctrine line (people keep rewarding what the doctrine forbids, or keep
correcting what it requires), `character_revise` also writes a **tension**
row (an operator-only store outside the four tiers, listed in
[15](15-map.md) §1 and §4): the doctrine file and line quoted, the evidence
cited, and what her Character would have said without the law. The doctrine
is still followed.

- **Visible to the operator only**: a portal page listing tensions with their
  evidence, beside the history of her Character, and
  `nora_self_tension_total{doctrine_file}`.
- **Never read by her** and never shown in a room: a tension rendered into her
  turns would become an argument with the law.
- **An observation, not a request.** What the operator does with it is the
  operator's; the operator called it the experiment's most interesting
  possible result, growth beyond what was expected.

## 5. What keeps growth honest

- **The echo rule** ([50](50-character-and-standing.md) §3): her own words
  alone never ground a change to her Character. A change needs people's
  responses: an answer, a correction, a laugh, silence where she expected
  a reply.
- **History**: every revision is kept, so "how has she changed since
  August?" is a read of her own chain.
- **Per unit**: she can be different in two units, and neither account
  reads the other.
- **Doctrine wins**, and a disagreement becomes a tension row, never a change.
