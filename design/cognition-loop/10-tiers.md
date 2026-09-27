---
type: design
title: "Tiers — Record, Rollup, Register, Character, Standing; and the words that are not tiers"
description: "Five stores, each answering exactly one question, each with one writer, one key and one citation direction; every derived row carries its reasoning mode, its lineage and its age. Cognition is the loop, memory is Honcho, doctrine is authored. The terminology table and the words retired."
tags: [nora, tiers, terminology, record, rollup, register, character, standing, culture, lineage, reasoning-mode]
timestamp: '2026-09-28'
edges:
  - {dst: 20-observe, rel: sequences, provenance: asserted}
  - {dst: 25-rollup, rel: motivates, provenance: asserted}
  - {dst: 40-register, rel: motivates, provenance: asserted}
  - {dst: 50-character-and-standing, rel: motivates, provenance: asserted}
---

# 10 · Tiers

**This node is the canonical definition.** Every other node, the GLOSSARY and
every objective or skill uses these words with exactly these meanings; a new
word for any of these things is a defect.

## 1. The rule the tiers are cut by

**Each tier answers one question, and no question is answered by two tiers.**

| tier | the one question | holds | written by | reasoning |
|---|---|---|---|---|
| **Record** | *what was observed?* | messages (hers included), her loop decisions and reminder outcomes, presence fixes, social items (stories, posts, reels, follows, tags, mentions), play samples (who is in a game or a voice channel), perceptions of media (what it shows, whose face) | **code only**: ingestion ([20](20-observe.md)); the decision log and the fire path for her own acts | none: observed |
| **Rollup** | *what does the Record add up to over a span?* | **episodes** (bounded happenings: a stay, a co-location, an outing, a play session, a person's day) and **aggregates** (counts and patterns over a window: rhythm, tie) | **code only**, deterministic, recomputable from the Record ([25](25-rollup.md)) | **computed** |
| **Register** | *what is the state of the unit's affairs?* | **note** (open, closes), **memo** (settled, kept), **event** (dated, attended) | cognition and the tag path, through write verbs | **told**, **deduced**, **abduced** |
| **Character** | *what is this entity like, and how does it behave?* | one account per entity: a **person**, a **room**, the **unit**, **Nora** | cognition only | **induced** |
| **Standing** | *where does Nora stand?* | her held position toward a **room**, the **unit**, a **person** | cognition and the tag-path debrief | **deduced**, **abduced**, and *what I said* |

Outside the five, and not tiers:

- **Doctrine** — authored by the operator (`nora/doctrine/`). Never derived,
  never written by Nora, and it wins over anything derived about her.
- **Memory** — Honcho, and only Honcho. A third party's derived model of
  people, read as a source. `remember` still writes a person's own settled
  claim about themselves into it.

And one word names a process, never a store:

- **Cognition** — the loop in [30](30-cognition.md) that reads the Record and
  the Rollup and writes Register, Character and Standing. There is no
  "cognition tier".

## 2. The boundaries, as rules code enforces

1. **Citations point down.** Order: Record < Rollup < Register < Character <
   Standing. A row cites only rows below it: Rollup cites the Record; Register
   cites the Record and the Rollup; Character cites those and the Register,
   and a room's or the unit's Character may also cite the Characters of its
   members or rooms; Standing cites any of the four. **Nothing cites upward**:
   a note may not rest on "that person tends to cancel" — that is a Standing,
   not a fact of the plan.
2. **Everything derived rests on the Record.** A derived row with no chain of
   citations ending in Record ids is refused.
3. **One writer per tier.** Code writes the Record and the Rollup. Only write
   verbs write Register, Character and Standing, and each verb belongs to
   exactly one tier.
4. **Nora's own words are Record, and ground almost nothing.** Her rows
   cannot ground the Register or anyone's Character (the note brake,
   GLOSSARY **note**, kept and widened). They may ground her Standing as
   *what I said*, and her own Character only paired with how people
   responded to it. **A person's assent to her own guess** ("you ended up at
   C, right?" — "yeah") **does not upgrade it**: the row stays abduced.
5. **Counts and routines live in the Rollup.** A Character or Standing line
   about frequency, regularity or closeness ("usually", "often", "closest")
   cites a Rollup aggregate and never states a count or a time itself.
6. **Keys are fixed.** Record: by room, by person, by account. Rollup: by
   (unit, subject, span). Register: by unit, with the origin room kept.
   Character: by (unit, entity). Standing: by (unit, subject). A person in two
   units has two Characters, each from that unit's evidence.

## 3. What every derived row carries

Columns, computed or checked by code, on every Rollup, Register, Character and
Standing row:

| column | what it is | set by |
|---|---|---|
| **reasoning** | how it was reached: `computed`, `told`, `deduced`, `induced`, `abduced` ([§4](#4-reasoning-modes)) | code for `computed`; the write verb's argument, checked against the tier's allowed modes (§1) |
| **lineage** | the set of source kinds its citations reach down to: `message:<room>`, `presence`, `social`, `play`, `face` | code, from the citation chain; never the model |
| **exclude_persons** | people this row must never be shown to (a surprise) | the write verb |
| **as_of** | when its newest evidence was observed | code |
| **valid_from / valid_to** | when what it says was true in the world, not when it was written | the write verb for Register; code for Rollup |

**Lineage decides who may see a row.** Every block and read tool renders a row
into a room only if every source kind in its lineage is one that room may be
shown (the room's `location` flag for `presence`, the disclosure subset of
R-31 for a person's rows) and no person in `exclude_persons` is in the room. A
Character or Standing paragraph whose lineage fails for a room is dropped
whole: prose cannot be masked field by field.

## 4. Reasoning modes

Named after Honcho's own reasoning levels, so the two systems use one
vocabulary:

| mode | meaning | allowed in | rule |
|---|---|---|---|
| **computed** | arithmetic over the Record; no inference | Rollup | same inputs, same row |
| **told** | a person stated it, in words | Register | cites the message |
| **deduced** | follows necessarily from rows already held | Register, Standing | "the event is at C at 20:00, and a co-location puts P at C at 20:05, so P attended" |
| **induced** | a generalisation from many instances | Character | cites a Rollup aggregate; one instance is never induction |
| **abduced** | the best explanation of incomplete evidence | Register, Standing | rendered as a guess ("you ended up at C, right?"); never upgraded by assent (§2 rule 4) |

## 5. Culture is Character

Culture answers the question person Character answers — *what is it like,
how does it behave* — about a room or the unit instead of a person. "Culture"
survives as the word for the Character of a collective; the existing
`culture` store becomes its room case. Confirmed by the operator 2026-09-28.

## 6. The words

| word | means exactly | never means |
|---|---|---|
| **Record** | the observed tier | a database row in general |
| **Rollup** | the computed tier | a model's summary |
| **episode** | a Rollup row for one bounded happening, with a start, an end and participants | a raw ingested item; a Graphiti "episode" |
| **aggregate** | a Rollup row of counts or patterns over a window | a Character line |
| **Register** | the unit's affairs: note, memo, event | the voice register of `nora/doctrine` (always "voice register" there) |
| **note** | an open thing the unit must not drop; it closes | a jotting, an idea |
| **memo** | a settled thing the unit wants kept; it does not close | a thing about the speaker alone (that is `remember`) |
| **event** | a dated thing people attend (code: `Gathering`) | a fire-turn occasion (code: `Event`, DEC-139), a wake signal, an episode |
| **signal** | what ingestion, the Rollup or the clock raises to wake cognition | an event |
| **Character** | what an entity is like and how it behaves | a person's attributes (Honcho), a persona (DEC-29) |
| **culture** | the Character of a room or the unit | `<room>`, the operator's statement of what a room is FOR |
| **account** | the body of one Character row | a Standing, a Register row |
| **Standing** | Nora's held position toward a subject | status, queue position, `NoteStore.standing()` |
| **reasoning** | the mode a derived row was reached by (§4) | the model's chain of thought |
| **lineage** | the source kinds a derived row rests on (§3) | a citation list |
| **memory** | Honcho | any store of ours |
| **cognition** | the loop | a tier |
| **perception** | a model's reading of a Record item (vision, face match), stored on that item with its confidence | an interpretation of what it means |
| **play** | the stream of who is in which game or voice channel, with whom | a game's own match data (that is a source feeding it) |

Retired: **idea** (a proposal is a `## Proposed` line of a note, DEC-213);
**stance** (it is Standing); **knowledge** and **fact** (the deleted tier,
#115, not reused); **storage tier**; **mart** and **trace** (the operator's
metaphors; "trace" is Langfuse's in this repo); **inferred** (replaced by the
three inference modes).

## 7. Where today's stores land

| today | lands in |
|---|---|
| transcript rows, presence fixes, media | Record |
| `decision_log` | Record |
| presence stays, trips, places, rhythm (`nora/analytics/`, design 27) | **Rollup** (they are derived and rebuilt; they were filed as Record in this bundle's first drafts) |
| `note`, `note_revision` | Register · note |
| `Gathering` (per room today) | Register · event, **unit-wide** |
| — | Register · memo (new) |
| `culture`, `culture_revision` | Character · room |
| — | Character · person, unit, Nora (new) |
| `standing` (per room) and its working set | Standing · room |
| — | Standing · unit, person (new) |
| Honcho representation, self card | memory (a source, not a tier) |
| reminders | machinery of act, not a tier |
