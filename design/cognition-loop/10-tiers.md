---
type: design
title: "Tiers — Record, Register, Character, Standing; and the words that are not tiers"
description: "Four stores, each answering exactly one question, each with one writer, one key and one citation direction. Cognition is the process, memory is Honcho, doctrine is authored. The terminology table, the words retired, and the one place this departs from the operator's sentence."
tags: [nora, tiers, terminology, record, register, character, standing, culture, memo, note, event]
timestamp: '2026-09-28'
edges:
  - {dst: 20-observe, rel: sequences, provenance: asserted}
  - {dst: 40-register, rel: motivates, provenance: asserted}
  - {dst: 50-character-and-standing, rel: motivates, provenance: asserted}
---

# 10 · Tiers

**This node is the canonical definition.** Every other node, the GLOSSARY and
every objective or skill uses these words with exactly these meanings; a new
word for any of these things is a defect.

## 1. The rule the tiers are cut by

**Each tier answers one question, and no question is answered by two tiers.**

| tier | the one question | holds | about | written by |
|---|---|---|---|---|
| **Record** | *what was observed?* | messages, presence fixes and stays, social items (stories, posts, reels, follows, tags, mentions), perceptions of media (what it shows, whose face) | a room, a person, an account | **code only** (ingestion, [20](20-observe.md)) |
| **Register** | *what is the state of the group's affairs?* | **note** (open, closes), **memo** (settled, kept), **event** (dated, attended) | the unit | cognition and the tag path, through write verbs |
| **Character** | *what is this entity like, and how does it behave?* | one account per entity: a **person**, a **room**, the **unit**, **Nora** | one entity | cognition only |
| **Standing** | *where does Nora stand?* | her held position toward a **room**, the **unit**, a **person** | Nora, toward one subject | cognition and the tag-path debrief |

Two things sit outside the four and are not tiers:

- **Doctrine** — authored by the operator (`nora/doctrine/`). Never derived,
  never written by Nora, and it wins over anything derived about her.
- **Memory** — Honcho, and only Honcho. A third party's derived model of
  people, read as a source. `remember` still writes a person's own settled
  claim about themselves into it.

And one word names a process, never a store:

- **Cognition** — the loop in [30](30-cognition.md) that reads the Record and
  writes the three derived tiers. There is no "cognition tier".

## 2. The boundaries, as rules code enforces

1. **Citations point down.** Register cites the Record. Character cites the
   Record and the Register. Standing cites any of the three. **Nothing cites
   upward**: a note may not rest on "that person tends to cancel" — that is a
   Standing, not a fact of the plan.
2. **The Record is the only thing observed.** Everything above it is derived
   and carries the Record ids it rests on. A derived row with no Record
   citation is refused.
3. **One writer per tier.** Code writes the Record. Only write verbs write the
   derived tiers, and each verb belongs to exactly one tier.
4. **Nora's own words are Record, and ground almost nothing.** Her rows
   cannot ground the Register or anyone's Character (the note tier's brake,
   GLOSSARY **note**, kept and widened). They may ground her Standing as
   "what I said", and her own Character only paired with how people
   responded to it.
5. **Keys are fixed.** Record: by room, by person, by account. Register: by
   unit, with the origin room kept. Character and Standing: by (unit,
   entity). A person in two units has two Characters, each from that unit's
   evidence: same person, different evidence, and units never share.

## 3. Culture is Character

The operator's sentence put **culture** with Standing, notes and memos, and
Character apart as "pure character representation". This draft puts culture
**in** Character, for one reason: culture answers the same question person
character answers — *what is it like, how does it behave* — about a room or a
unit instead of a person. Standing, notes and memos answer different questions
(where she stands; what is open; what is settled). "Culture" survives as the
word for the character of a collective: the room's culture, the unit's
culture. The existing `culture` store becomes the room case of Character.

**Confirmed by the operator 2026-09-28.** The full map of every store and
context block is [15](15-map.md); what her identity is built from is
[55](55-self.md).

## 4. The words

| word | means exactly | never means |
|---|---|---|
| **Record** | the observed tier | a database row in general |
| **Register** | the group's affairs: note, memo, event | the voice register of `nora/doctrine` (context disambiguates; the tier is always capitalised) |
| **note** | an open thing the group must not drop; it closes | a jotting, an idea |
| **memo** | a settled thing the group wants kept; it does not close | a thing about the speaker alone (that is `remember`) |
| **event** | a dated thing people attend (code: `Gathering`) | a fire-turn occasion (code: `Event`, DEC-139), a wake signal |
| **signal** | what ingestion raises to wake cognition | an event |
| **Character** | what an entity is like and how it behaves | a person's attributes (Honcho), a persona (DEC-29) |
| **culture** | the Character of a room or the unit | `<room>`, the operator's statement of what a room is FOR |
| **Standing** | Nora's held position toward a subject | status, queue position, `NoteStore.standing()` |
| **memory** | Honcho | any store of ours |
| **cognition** | the loop | a tier |
| **perception** | a model's reading of a Record item (vision, face match), stored on that item with its confidence | an interpretation of what it means |

Retired: **idea** (a proposal is a `## Proposed` line of a note, DEC-213);
**stance** (it is Standing); **knowledge** and **fact** (the deleted tier,
#115, not reused); **storage tier** (implementation, not a concept).

## 5. Where today's stores land

| today | lands in |
|---|---|
| transcript rows, presence fixes, media | Record |
| `note`, `note_revision` | Register · note |
| `Gathering` (per room today) | Register · event, **unit-wide** (operator 2026-09-28) |
| — | Register · memo (new) |
| `culture`, `culture_revision` | Character · room |
| — | Character · person, unit, Nora (new) |
| `standing` (per room) and its working set | Standing · room |
| — | Standing · unit, person (new) |
| Honcho representation, self card | memory (a source, not a tier) |
| reminders | machinery of act, not a tier |
