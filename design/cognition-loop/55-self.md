---
type: design
title: "Self — what her cognition builds, and where her growth lives"
description: "Cognition is not a tier: it is the loop; what it builds is Register, Character, Lessons and Standing. Her identity is the part of all state that is about her: given (doctrine), done (her Record), digested (her Character), learned (her Lessons) and held (her Standing). Growth is those changing and every turn reading them. Doctrine is the law: she never changes it, in whole or in part, and it changes only when the humans all agree. Her growth lives only in her own blocks, which render below the doctrine; where they disagree the doctrine is followed and a tension row records it for the operator."
tags: [nora, self, identity, cognition, growth, doctrine, character]
timestamp: '2026-09-28'
edges:
  - {dst: 15-map, rel: depends_on, provenance: asserted}
  - {dst: 50-character-and-standing, rel: extends, provenance: asserted}
---

# 55 · Self

## 1. What cognition is made of

**Cognition is not a tier.** It is the loop that reads the Record and writes
Register, Character, Lessons and Standing ([30](30-cognition.md)). What it has built
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
| **learned** | what she has learned about how to act in this unit | **Lessons** | cognition, from outcomes; the operator may roll any lesson back |
| **held** | what she currently thinks, follows and owes | **Standing** (room, unit, person) | cognition and the tag debrief |
| (heard about her) | a third party's digest of her own words | memory (self card) | Honcho |

**Character · Nora is where the digested self lives.** The Character tier
holds her identity as one of its entities, beside persons, rooms and the
unit, and cognition is what writes it. Her full identity is that account
read together with the doctrine above it and the Standing beside it.

## 3. Growth, and the law she lives within

Doctrine is the law: every file of it, `core/identity.md`,
`rooms/voice.md` and `rooms/belonging.md` included. **Nora never changes
doctrine**, in whole or in part, and never proposes a change (operator
2026-09-28). It changes only when the humans all agree.

**Her growth lives only in her own blocks**: Character · Nora (the `self`
block), Lessons (the `lessons` block) and Standing. They render after the doctrine and always below it, so
the doctrine is read first and wins. When cognition revises them her
behaviour changes on the next turn with no code or doctrine change. That is
the only way she changes herself.

**What is read.** Tag and fire turns read the `self` block, the `lessons`
block and room Standing (the `standing` block); unit and person Standing are read with
`standing_read`, which covers room, unit and person ([15](15-map.md) §3–4).
The `self` block (step 8) renders her whole Character · Nora, capped in
lines. `self_card` stays pushed until then and leaves the prompt at step 8
([80](80-prior-art.md) §6).

**Where her blocks and the doctrine disagree, the doctrine is followed** and
a tension row records the disagreement for the operator (§4): information
for the humans to decide on, never a proposal from her.

## 4. Tension, made visible

When cognition revises Character · Nora and the evidence pulls against a
doctrine line (people keep rewarding what the doctrine forbids, or keep
correcting what it requires), `character_revise` also writes a **tension**
row; so does `lesson_write` when it refuses a lesson that would loosen or
contradict doctrine ([30](30-cognition.md) §3). A tension row holds the
doctrine file and line quoted, the evidence cited, and what her Character or
the lesson would have said without the law. The doctrine is still followed.
Tension is outside the tiers ([10](10-tiers.md) §1; [15](15-map.md) §1 and
§6), and written by the two exceptions to "one write path per tier".

- **Visible to the operator only**: a portal page listing tensions with their
  evidence, beside the history of her Character, and
  `nora_self_tension_total{doctrine_file}`.
- **Never read by her, by construction**: no read tool returns a tension row
  (none is in `read_only_tools()`), no block renders one, and a test pins
  both. A tension rendered into her turns would become an argument with the
  law.
- **A report, not a request.** It is information for the humans, who alone
  decide whether the doctrine changes; she never proposes the change. The
  operator called it the experiment's most interesting possible result,
  growth beyond what was expected.

## 5. What keeps growth honest

- **The echo rule** ([50](50-character-and-standing.md) §3): her own words
  alone never ground a change to her Character or a lesson. A change needs people's
  responses: an answer, a correction, a laugh, silence where she expected
  a reply.
- **History**: every revision is kept, so "how has she changed since
  August?" is a read of her own chain.
- **Per unit**: she can be different in two units, and neither account
  reads the other.
- **Doctrine wins**, and a disagreement becomes a tension row, never a change.
