---
type: design
title: "Nora World: the world as a View of the Notebook"
description: "How each Notebook tier and Rollup aggregate takes form in the world, how lineage and withdrawal reach it, what presentation state the world keeps, and how Nora decides what to make real through a world_place act."
tags: [nora, world, view, notebook, rollup, lineage, withdrawal, cognition]
timestamp: '2026-10-16'
status: proposed
governance: pre-governance-input
edges:
  - {dst: 00-overview.md, rel: realizes, provenance: asserted}
  - {dst: ../cognition-loop/05-data-model.md, rel: depends_on, provenance: asserted}
  - {dst: ../cognition-loop/10-tiers.md, rel: depends_on, provenance: asserted}
---

# The world as a View of the Notebook

## 1. What reaches the world

The operator, 2026-10-16: the world shows the note, the memo and the rollup
or Notebook layer, never the raw layer. In the data model's words: the
Notebook and the Rollup reach the world; the Record does not.

| source | layer · tier · kind | in the world |
|---|---|---|
| a room | (scope) | a district, with its own building and ground |
| the room's Standing | Notebook · Standing | the district's mood: light, weather, how busy it is |
| culture (in-jokes, shared references) | Notebook (moves onto the entry later, DEC-249 open question) | landmarks only that group would recognise |
| a note | Notebook · Register · note | an object Nora placed: a framed picture, a ticket stub, a souvenir |
| a memo | Notebook · Register · memo | a pinned card on the district's board |
| an event | Notebook · Register · event | something that appears before the date (a banner, a table set for dinner) and leaves after it |
| a person's Character | Notebook · Character · person | Nora's layer on that person's avatar (`20-presentation-and-avatars.md` §2) |
| Nora's own Character | Notebook · Character · Nora | her home: what is in it, how it changes |
| places and rhythm | Rollup | a map of the real places the group goes, with where people usually are at this hour |
| ties | Rollup | who stands near whom when avatars idle |
| music played in the room | Rollup (when the play history is rolled up) | the venue's setlist and posters |

Memory (Honcho) sits beside the flow and is never shown directly; if a world
element needs per-person understanding, it goes through Character.

## 2. Lineage and withdrawal

The world is a portal View, so it is assembled by the one View builder the
data model requires (05 §6), never by a selection of its own:

- **Lineage for the viewer's rooms.** A viewer sees a district only if they
  are in that room, and sees an entry's object only if its lineage reaches one
  of their rooms. Two people standing in the same spot can see different
  objects. That is correct, and it is the price of a per-reader View
  (`50-open-questions.md` §3).
- **Withdrawal.** An entry that becomes `unsupported` or `hidden` is absent
  from the View, so its object disappears on the next assembly. A forget
  request therefore reaches the world with no world-specific code.
- **As-of.** Because the Notebook is append-only in system time, the world can
  be shown as of a past date. A "this day last year" view costs nothing extra.

## 3. Presentation state

The world keeps one kind of data of its own, and it is never knowledge:

| row | keyed by | holds |
|---|---|---|
| placement | entry id | asset id, district, grid cell, rotation |
| asset | asset id | the GLB in object storage, its spec, thumbnail, check result (`30-asset-pipeline.md`) |
| avatar | person_id | the person's chosen VRM and the items they hid from Nora's layer |

A placement whose entry is absent from the viewer's View is not drawn. A
placement is never read as evidence of anything, by any reader. Deleting all
presentation state loses looks, not knowledge, and the projection rebuilds
placements from the View.

## 4. Who decides what is made real

Two paths, built in this order:

1. **The projection (code).** A deterministic mapping from the View to the
   world, per the table in §1. Each new entry gets a placement in its
   district from a fixed rule. This makes the world true to the Notebook, but
   it is a mirror, not a choice.
2. **`world_place` (Nora).** A write verb for Cognition: place, change or
   retire an object for an entry, with a short spec of how it should look.
   This is where the world starts to feel alive, because things appear
   because she decided they mattered. Code validates every call: the entry
   exists and is supported, the district is one its lineage reaches, the spec
   is within length. Doctrine says when to use it; the verb's description does
   not enumerate cases (judgement, not a menu).

`world_place` writes presentation state, not the Notebook, so it does not
break "one writer per tier". It is still a new act with model cost, so it
needs a measurement before it ships on (`50-open-questions.md` §4).

## 5. What would reopen this

- A world element is wanted that needs the Record (a quote, a photo from
  chat). That would breach the operator's rule and needs a new ruling.
- Per-viewer differences are measured to confuse people in the same district
  more than they delight them. Then district objects move to unit-wide kinds
  only, and per-viewer variation stays on avatars.
