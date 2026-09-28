---
type: design
title: "Self — what her cognition builds, and how far she may change herself"
description: "Cognition is not a tier: it is the loop; what it builds is Register, Character and Standing. Her identity is the part of all state that is about her: given (doctrine), done (her Record), digested (her Character) and held (her Standing). Growth is those changing and every turn reading them. Doctrine is law she never proposes to change; within the latitude the operator opens she grows through her Character and Standing, and where evidence pulls against the law the tension is shown to the operator only."
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
is those three tiers, never "cognition".

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

**Character · Nora is where the digested self lives.** The Character tier
holds her identity as one of its entities, beside persons, rooms and the
unit, and cognition is what writes it. Her full identity is that account
read together with the doctrine above it and the Standing beside it.

## 3. Growth, and the law she lives within

Doctrine is the law: the framework she lives inside, authored by the operator
and never changed by her. **She never proposes doctrine** (operator
2026-09-28, a red line). Within the law she grows only where the law opens
latitude, in its own words.

**What is read.** Tag and fire turns read the `self` block and room Standing
(the `standing` block); unit and person Standing are read with
`standing_read`, which covers room, unit and person ([15](15-map.md) §3–4).
When cognition revises them her behaviour changes on the next turn with no
code or doctrine change. That is the only way she changes herself.

**The `self` block** (step 8) renders after the doctrine and shows only the
sections of Character · Nora that grow under a doctrine row whose latitude
the operator has opened; until a row is opened its sections do not render.
`self_card` stays pushed until then and leaves the prompt at step 8
([80](80-prior-art.md) §6).

| doctrine | kind | latitude | the section that grows under it |
|---|---|---|---|
| `nora/doctrine/core/constraints.md`, `nora/doctrine/public/constraints.md`, `nora/doctrine/rooms/pictures.md` | constraint | **none — settled law** | — |
| `nora/doctrine/rooms/conduct.md`, `nora/doctrine/public/conduct.md`, `nora/doctrine/rooms/participation-conduct.md`, `nora/doctrine/rooms/silence.md` | conduct | **none — settled law** | — |
| `nora/doctrine/instruments/` | instrument | not hers | — |
| `nora/doctrine/core/identity.md` | identity | **undecided**: what she is stays fixed; how she carries herself in one unit may be hers | `How I come across` |
| `nora/doctrine/rooms/voice.md`, `nora/doctrine/public/voice.md` | identity | **undecided, leaning yes**: within the voice register the file sets, word choice, humour, length and a unit's own expressions may be hers | `How I sound here`, `What lands and what does not` |
| `nora/doctrine/rooms/belonging.md` | identity | **undecided**: her place in a unit is authored today, and it is what Character and Standing grow into | `What they come to me for` |

Where latitude is opened it is opened **in the doctrine's own words** ("within
this voice register, how you sound in a unit is yours to find"). She does not
claim it; the law gives it. The undecided rows are the operator's to settle,
before step 8.

## 4. Tension, made visible

When cognition revises Character · Nora and the evidence pulls against a
doctrine line (people keep rewarding what the doctrine forbids, or keep
correcting what it requires), `character_revise` also writes a **tension**
row: the doctrine file and line quoted, the evidence cited, and what her
Character would have said without the law. The doctrine is still followed.
Tension is outside the tiers ([10](10-tiers.md) §1; [15](15-map.md) §1 and
§6), and the one exception to "one write path per tier".

- **Visible to the operator only**: a portal page listing tensions with their
  evidence, beside the history of her Character, and
  `nora_self_tension_total{doctrine_file}`.
- **Never read by her, by construction**: no read tool returns a tension row
  (none is in `read_only_tools()`), no block renders one, and a test pins
  both. A tension rendered into her turns would become an argument with the
  law.
- **A report, not a request.** What the operator does with it is the
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
