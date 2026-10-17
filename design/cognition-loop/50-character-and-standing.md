---
type: design
title: "Character, Lessons and Standing — what an entity is like, what she has learned, where she stands; and the two slow adaptors"
description: "Character per entity (person, room, unit, Nora) as one shape, written only by the Character adaptor; why it is not memory (Honcho); Nora's own Character beside the doctrine; Standing widened from room to unit and person, its head no longer a lossy fold; the echo rule; correction by the subject; disclosure per section; and the two slow adaptors, Character and Lessons, with the lesson guards."
tags: [nora, character, culture, standing, self, persons, social-cognition]
timestamp: '2026-10-17'
edges:
  - {dst: 10-tiers, rel: depends_on, provenance: asserted}
---

# 50 · Character, Lessons and Standing

Both tiers are later steps ([70](70-migration.md)); this node fixes their
shape so the loop's verbs and keys are right from the start.

## 1. Character: one shape, four kinds of entity

An **account** per `(unit, entity)`: what the entity is like and how it
behaves, in prose under the fixed sections of [12](12-schemas.md) §4, with
cited evidence, revised only by the Character adaptor (§6) through
`character_revise`, as an append-only chain (the
revision chain the culture store already has, design/culture-tier/).

| entity | the account holds | evidence it reads |
|---|---|---|
| **person** | how they talk, what they care about, how they decide, whom they are close to, what they reliably do (organise, cancel, turn up late) | the Record (their messages, social items), the Rollup (presence patterns, tie, play profile), the Register (events attended, commitments kept); memory as a hint only |
| **room** (its culture) | how the room talks and decides, what it calls itself | the room's Record, the Register, its members' Characters |
| **unit** (its culture) | what the unit is, across its rooms | the rooms' cultures, the Register |
| **Nora** | what she is like in this unit, as its people have met her | her Record rows **paired with the responses to them**; never her rows alone |

`character_revise` for a person refuses anyone who is not a listed person of
the unit's rooms.

**In the data model** ([05](05-data-model.md) §2) every account is an entry
of the Notebook's Character tier: culture is the Character of a room or the
unit, on the same machinery as a person's or Nora's. **Amended by DEC-249**
([05](05-data-model.md) §5): evidence is only the Record and the Rollup. The
Register rows and members' Characters named in "evidence it reads" above are
read as context and linked, never cited; an account cites Record ids and
Rollup keys. A person's social items are Record rows (`social_item`) and are
cited by their own id, `social:<network>:<item id>`; built 2026-10-17 with the
`<social_items>` block and their place in the person's wake
([22](22-social-stream.md) §6). The same holds for Standing below, which links to Character and
Lessons and cites the Record and Rollup.

Character is descriptive. What Nora has learned to *do* in a unit ("two
options, not five") is a lesson, in the Lessons tier ([10](10-tiers.md) §1,
[12](12-schemas.md) §4a), not a line of her Character.

**Why not memory.** Honcho's model of a person was measured on 2026-08-06 as
about 88% happenings and 12% traits (GLOSSARY, **representation**): it
records what someone did, not who they are, and its derivation is not ours
to steer. Character reads memory as a hint, never cites it, and writes an
account of its own.

**Nora's Character is not her doctrine.** Doctrine is authored and wins (its
law subset is the part no account may loosen, DEC-291). Her
Character is derived — how she has actually come across here — and may be
read beside the doctrine, never over it.

## 2. Standing: from room to unit and person

Standing keeps its meaning (her held position toward a subject) and gains
two subjects. Its writers are the Cognition adaptor and, for room Standing,
the tag path's debrief, both through `standing_revise`:

| subject | exists | holds |
|---|---|---|
| room | yes (design/standing-compact/) | where the room's conversation stands for her, plus the working set |
| **unit** | new | what she is following, what she is watching for, what she owes |
| **person** | new | her position toward one person: what is between them, what she owes them, how they receive her |

Its modes are `deduced`, `abduced` and `said` ([10](10-tiers.md) §4). It
cites the Record and the Rollup, and may **link** to a Character line or a
lesson ("they tend to change plans late, so I hold this one loosely"), never
cite one (DEC-249, [10](10-tiers.md) §2 rule 1).

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

Standing holds undated positions only, and has no `Unsure`
([12](12-schemas.md) §5, §8).

## 3. The echo rule

A position built from her own words drifts: each pass agrees a little more
with the last. The rule that stops it is [10](10-tiers.md) §2 rule 4. In
practice: a revision of her Character or a lesson whose only evidence is
Nora is refused; a Standing line resting only on her rows says only what she
said (`I owe`, `I hold open`), never what is so.

## 4. Correction

An account nobody sees cannot be corrected, so:

- **The portal shows each person the account about them**, plus the culture of the
  rooms they are listed in, with the evidence behind every line, lineage
  evaluated against the viewer's rooms.
- **Only the subject may dispute a line about them.** The tag turn records a
  `character_dispute` as a Record item authored by that person
  ([10](10-tiers.md) §1), which wakes the Character adaptor for that entity;
  its next session must re-evidence or remove the line, and a disputed line never renders until it has.
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
member, and on a turn only when that person is in it (the tagger, the mentioned,
the replied-to, a speaker in the window; self-growth/10-character.md). Within those rooms lineage decides per section ([10](10-tiers.md) §3):
a section resting on presence renders only where the room's `location` flag
is on and the DisclosureGuard passes for the person; a section that fails is
not shown, and the rest of the account is.

A person in two units has two accounts, and neither account reads the other;
their shared input is the person-scoped Rollup (stays, places, rhythm), read
under lineage. The peer card is not scoped per unit (it is unscoped inside
its Honcho workspace, DEC-214), so it can mix the units of one workspace:
nothing from it is Character evidence.

A lesson carries its lineage per row and renders only where it passes.

An account is an input to her judgement, never text she recites.

## 6. The two slow adaptors

Reflection is its own job on its own timescale ([00](00-intent.md) §3). Two
Dreamer adaptors do it, each the sole writer of its tier; neither acts, and
neither is woken by a single message except a `character_dispute`. Their
wake rules are in [20](20-observe.md) §3.

**The Character adaptor** — per (unit, entity), for every entity: a room's
or the unit's culture, a person, Nora. It is today's culture adaptor
(`nora/language/culture.py`) generalised to one shape: it reads the
entity's evidence since its last revision (the Record, the Rollup's
aggregates, the Register, and for a room or the unit its members'
Characters), and writes one revision through `character_revise`, or none.
Every revision of Character · Nora passes the same doctrine check as a lesson
(below); where it finds the evidence pulling against doctrine, a tension row
is written ([55](55-self.md) §4). It replaces the culture adaptor's daily clock
(`[culture] fold_interval_seconds`; done for rooms by DEC-288, 2026-10-07).

**The Lessons adaptor** — per unit. It reads the outcomes of her acts since
its last session, which are the `response` episodes of the Rollup
([25](25-rollup.md) §3: replies, reactions and edits to her sends, and
`silent` where one was expected), with the lessons already held, and writes
through `lesson_write` and `lesson_retire`. The operator's rollback on the
portal goes through `lesson_retire` too.

**Enforced by code on every lesson**, on top of every write verb's checks
([30](30-cognition.md) §3):

- **A hard size cap per unit** (Hermes-style: sized from the first month,
  around 800 tokens, never from one sample). A write that would pass it is
  refused until the adaptor consolidates or retires lessons.
- **Doctrine wins.** A lesson that would loosen or contradict doctrine is
  refused and written as a tension row instead ([55](55-self.md) §4). The
  judgement is a separate instrument call (a model other than the session,
  reading the lesson and the doctrine's law subset, DEC-291); code applies its verdict. It is a
  model check, not a proof, so the assess pass measures it on a labelled set.
  **An instrument error is not a pass**: a lesson is refused; for a
  Character · Nora revision no tension row is written (the revision itself
  is descriptive and still commits). Either way the error is counted.
- **The echo rule** ([10](10-tiers.md) §2 rule 4): a lesson cites the
  `response` episodes that taught it.
- **No approval step.** A lesson takes effect on the next turn. Every write
  and retirement is visible to the operator on the portal, with its evidence
  and history, and reversible by id through `lesson_retire`.
- **The operator's rollback sticks.** A lesson the operator retired is never
  re-written from outcomes older than the retirement. Rolling back a revision
  writes the prior one as a new revision; an operator retirement is undone
  the same way, never by deleting it.
