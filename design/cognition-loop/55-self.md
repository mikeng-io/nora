---
type: design
title: "Self — what her adaptors build, where her growth lives, and the conscious and the subconscious"
description: "Cognition is not a tier: it is the loop; what it builds is Register, Character, Lessons and Standing. Her identity is the part of all state that is about her: given (the law in the doctrine), done (her Record), digested (her Character), learned (her Lessons) and held (her Standing). Growth is those changing and every turn reading them. Doctrine is the law: she never changes it, in whole or in part, and it changes only when the humans all agree. Her growth lives only in her own blocks, which render below the doctrine; where they disagree the doctrine is followed and a tension row records it for the operator."
tags: [nora, self, identity, cognition, growth, doctrine, character]
timestamp: '2026-09-28'
edges:
  - {dst: 15-map, rel: depends_on, provenance: asserted}
  - {dst: 50-character-and-standing, rel: extends, provenance: asserted}
---

# 55 · Self

## 1. What her adaptors build

> 2026-10-28 (operator, ruled; [30](30-cognition.md#0-the-loop-as-ruled-2026-10-28)): the Cognition loop now builds all of these: step ① the memory graph, step ② Character, culture and Lessons, step ③ the Register and Standing.

**Cognition is not a tier**, and neither is reflection. The Dreamer's
adaptors read the Record and the Rollup and build Register, Character,
Lessons and Standing ([00](00-intent.md) §3): Cognition writes Register and
Standing, the Character adaptor writes Character, the Lessons adaptor writes
Lessons. What they have built is those tiers, never "cognition".

The operator's aim (2026-09-28): Nora builds an identity — who she is — not
only from the doctrine she was given and what she observed, but from what she
has **digested and thought about**, and grows from the interactions people
have with her and with each other.

## 2. Her identity is the slice of state that is about her

| facet | what it says about her | where it lives | changes how |
|---|---|---|---|
| **given** | the law she lives within: what no unit may loosen. **AMENDED 2026-10-07 (DEC-291):** it named «constraints, voice, conduct»; it now means the law subset of the doctrine ([self-growth/30](../self-growth/30-doctrine-sort.md), [31](../self-growth/31-doctrine-audit.md)). Her voice is digested (Character) and her conduct is learned (Lessons), though their text is still served from the doctrine files until the cut | Doctrine, law subset (authored) | only when the humans all agree |
| **done** | what she said and decided, and what happened after | Record (her sends, `loop_decisions`, reminders' outcomes) | every act |
| **digested** | who she has become in this unit, as its people met her | **Character · Nora** | the Character adaptor, from what she did **and how people responded** |
| **learned** | what she has learned about how to act in this unit | **Lessons** | the Lessons adaptor, from outcomes; the operator may roll any lesson back |
| **held** | what she currently thinks, follows and owes | **Standing** (room, unit, person) | Cognition and the tag debrief |
| (heard about her) | a third party's digest of her own words | memory (self card) | Honcho |

**Character · Nora is where the digested self lives.** The Character tier
holds her identity as one of its entities, beside persons, rooms and the
unit, and the Character adaptor is what writes it. Her full identity is that account
read together with the doctrine above it and the Standing beside it.

## 3. Growth, and the law she lives within

Doctrine is the law: every file of it, `core/identity.md`,
`rooms/voice.md` and `rooms/belonging.md` included. **AMENDED 2026-10-07 (DEC-291):**
until the cut, the files still carry practice and character beside the law, and
the whole of it is still served, still wins and is still never changed by her;
but what *law* means is the law bucket (`nora/doctrine/classification.yaml`,
[self-growth/31](../self-growth/31-doctrine-audit.md)), and that subset is what the
doctrine check reads (§4). **Nora never changes
doctrine**, in whole or in part, and never proposes a change (operator
2026-09-28). It changes only when the humans all agree.

**Her growth lives only in her own blocks**: Character · Nora (the `self`
block), Lessons (the `lessons` block) and Standing. They render after the doctrine and always below it, so
the doctrine is read first and wins. When her adaptors revise them her
behaviour changes on the next turn with no code or doctrine change. That is
the only way she changes herself.

**What is read.** Tag and fire turns read the `self` block, the `lessons`
block and room Standing (the `standing` block); unit and person Standing are read with
`standing_read`, which covers room, unit and person ([15](15-map.md) §3–4).
The `self` block (step 8) renders her whole Character · Nora, capped in
lines. `self_card` stays pushed until then and leaves the prompt at step 8
([80](80-prior-art.md) §6).

> **As built 2026-10-07 (DEC-292, `design/self-growth/20-self-block.md`):** the
> `self` block is built and `self_card` is deleted, ahead of the cognition
> loop's own step 8. The self-growth bundle sequenced it first.

**Where her blocks and the doctrine disagree, the doctrine is followed** and
a tension row records the disagreement for the operator (§4): information
for the humans to decide on, never a proposal from her.

## 4. Tension, made visible

When the Character adaptor revises Character · Nora and the evidence pulls
against a doctrine line (people keep rewarding what the doctrine forbids, or
keep correcting what it requires), `character_revise` also writes a
**tension** row; so does the Lessons adaptor's `lesson_write` when it
refuses a lesson that would loosen or contradict doctrine. Both rest on the
same doctrine-check instrument, run on every Character · Nora revision and
every lesson; an instrument error writes no tension row and is counted
([50](50-character-and-standing.md) §6). A tension row holds the
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

- **The echo rule** ([10](10-tiers.md) §2 rule 4): her own words alone
  never ground a change to her Character or a lesson. A change needs people's
  responses: an answer, a correction, a laugh, silence where she expected
  a reply.
- **History**: every revision is kept, so "how has she changed since
  August?" is a read of her own chain.
- **Per unit**: she can be different in two units, and neither account
  reads the other.
- **Doctrine wins**, and a disagreement becomes a tension row, never a change.

## 6. The conscious and the subconscious, like a person

The operator's model (2026-09-28), not a code name:

- **The subconscious** (潛意識): the Dreamer's adaptors — Cognition, the slow
  Character and Lessons adaptors, and Assess. Always running underneath, woken by
  the world and by herself (`wake_at`).
- **The conscious** (明意識): the turns that speak — the tag turn, and the fire
  turn a `remind` starts.

`remind` is the one bridge from the subconscious to the conscious; `wake_at`
is the subconscious scheduling itself.

**The direction rule, in code: the subconscious drives the conscious; the
conscious barely drives the subconscious.**

- **Subconscious → conscious**: through the pushed blocks (the Register
  indices, Standing, `self`, `lessons`, `culture`) and through the `remind`
  rows the fire turn acts on.
- **Conscious → subconscious**: only indirectly. What she says and how people
  respond is Record a later session may read; the tag path's ordinary
  Register writes and the debrief's Standing are shared state, not a
  command. The conscious holds no pace verb (`wake_at`, `wake_move`,
  `wake_cancel`), cannot read or edit an agenda or a pending self-wake, and
  cannot start or steer a session. That her own messages raise no stimulus
  ([20](20-observe.md) §3) is a consequence of this rule.

**The conscious and the subconscious over the data model**
([05](05-data-model.md) §7, DEC-249). The subconscious writes the Notebook,
one writer per tier, split by timescale: Cognition, fast and per group,
writes the Register and Standing; the Character adaptor and the Lessons
adaptor, slow reflection, write Character and Lessons; Assess writes nothing
there, only reports. The conscious reads Views cut for its room at read time
([05](05-data-model.md) §6): pushed indices (`<notes>`, `<memos>`, `<events>`,
`culture`, `standing`, `self`, `lessons`) and pulled reads (`note_read`,
`memo_read`, `event_search`, `character_read`, `standing_read`). It never
writes the Notebook: it hands off. A «記低» about the unit is a strong cue,
and the end of a tag turn raises a hand-off stimulus in place of the debrief
(its Standing write moves behind Cognition at step 4), each waking Cognition
in about two minutes; a «記低» about the speaker stays `remember`, into
Memory. So the conscious reaches the subconscious only as Record and stimuli,
which is the direction rule above, now without the shared writes it allowed.
