---
type: design
title: "Nora World: the world as a View of the Notebook"
description: "One world per unit, the read boundary (DEC-231). How each Notebook tier and Rollup aggregate takes form in it; where the world's own rows sit in the data model (her acts and people's acts are Record; projected placements are computed at assembly and never stored; the scene is a View); who writes each; and how Nora decides what to make real through a world_place act."
tags: [nora, world, view, notebook, rollup, unit, withdrawal, cognition]
timestamp: '2026-10-16'
status: proposed
governance: pre-governance-input
edges:
  - {dst: 00-overview.md, rel: realizes, provenance: asserted}
  - {dst: ../cognition-loop/05-data-model.md, rel: depends_on, provenance: asserted}
  - {dst: ../cognition-loop/10-tiers.md, rel: depends_on, provenance: asserted}
---

# The world as a View of the Notebook

## 1. One world per unit

The unit is the read boundary (DEC-231), and a boundary is drawn by scope,
never by a filter inside it: lineage stays built and `[lineage] mode` stays
`off` (operator, 2026-10-03, `design/cognition-loop/10-tiers.md` «Lineage
decides who may see a row»). So the world follows the unit, not the room:

- **Each unit has its own world.** Its rooms are districts of that world, and
  every member of the unit sees the whole of it, the same way Cognition reads
  every room of its unit.
- **A person in two units visits two worlds.** Nothing of one unit's
  Notebook is drawn in the other's world, avatars included.
- **Everyone in a world sees the same objects.** There is no per-viewer
  filtering inside a unit, so two people in the same place can point at the
  same thing.

Rooms whose talk must not meet already belong in different units; the world
inherits that, and needs no privacy model of its own.

## 2. What reaches the world

The operator, 2026-10-16: show the note, the memo and the rollup or Notebook
layer, never the raw layer. In the data model's words: the world draws
knowledge from the Notebook and the Rollup, and never shows an observation of
a person (a transcript, a photo, a perception). The Record rows it does read
are acts made for the world itself (§4).

| source | layer · tier · kind | in the world |
|---|---|---|
| a room of the unit | (scope) | a district, with its own building and ground |
| the room's Standing | Notebook · Standing | the district's mood: light, weather, how busy it is |
| culture (in-jokes, shared references) | Notebook · Character · room | landmarks only that group would recognise |
| a note | Notebook · Register · note | an object Nora placed: a framed picture, a ticket stub, a souvenir |
| a memo | Notebook · Register · memo | a pinned card on the district's board |
| an event | Notebook · Register · event | something that appears before the date (a banner, a table set for dinner) and leaves after it |
| a person's Character | Notebook · Character · person | Nora's reading of that person, worn by their avatar (`20-presentation-and-avatars.md` §2) |
| Nora's own Character | Notebook · Character · Nora | her home: what is in it, how it changes |
| places and rhythm | Rollup | a map of the real places the unit goes, with where people usually are at this hour |
| ties | Rollup | who stands near whom when avatars idle |
| music played in the unit's rooms | Rollup (when the play history is rolled up) | the venue's setlist and posters |

Memory (Honcho) sits beside the flow and is never shown directly; if a world
element needs per-person understanding, it goes through Character.

## 3. Withdrawal and time

- **Withdrawal.** An entry that becomes `unsupported` or `hidden` is absent
  from the View, so its object disappears on the next assembly. A forget
  request reaches the world with no world-specific code.
- **As of a past date.** The Notebook and the Record are append-only in
  system time (`10-tiers.md` §8), and the projection is a pure function of
  them (§5), so the world can be assembled as of T with no extra store.

## 4. The world's own rows, placed in the data model

| row | layer | writer | holds |
|---|---|---|---|
| a `world_place` call | Record (her acts), like her generated images (`05-data-model.md` §1a) | Cognition, through the verb | entry id, the spec, the asset chosen, district and cell; append-only |
| an asset | Record (her acts: an artifact) | the asset pipeline, for her act | the GLB in object storage, its spec, thumbnail, check result (`30-asset-pipeline.md`) |
| a person's avatar choice, and each item they hide | Record (that person's acts) | the person, through the portal | their VRM, and hides by item; append-only |
| the scene a viewer gets | View | the one View builder (`05-data-model.md` §6) | assembled at read time; never stored |

**Projected placements are not stored.** The projection (§5) is a pure
function of the unit's supported entries as of T (each one's id, kind and
district) and the `world_place` acts that hold cells, evaluated by the View
builder at assembly, so an object can shift when a neighbour arrives or
leaves. Nothing is stored, so there is no cache to read back and nothing to
rebuild.

**Precedence.** For one entry, Nora's latest `world_place` act wins over the
projected placement. A person's hide wins over both, for that person's avatar.

**Who reads these rows.** They are Record like her other acts, and evidence
of what was placed or chosen:

- Cognition reads its unit's current `world_place` acts as part of its View,
  the way it sees its own earlier acts, so it can change or retire them. An
  entry may cite them like any Record row.
- People's avatar choices and hides are read by the View builder to draw the
  scene. Whether a Cognition session may read hides as a signal about the
  person is open (`50-open-questions.md` §6); until a ruling, its View
  declares no slice that includes them.

Deleting any of these rows is a forget, with its cascade, never a reset.

## 5. Who decides what is made real

Two paths, built in this order:

1. **The projection (code).** A pure function, per the table in §2: an
   entry's kind picks a stock asset and its id picks a free cell in its
   district by a fixed rule. It runs inside the View builder and stores
   nothing. This makes the world true to the Notebook, but
   it is a mirror, not a choice.
2. **`world_place` (Nora).** A verb for Cognition: place, change or retire an
   object for an entry, with a short spec of how it should look. Things then
   appear because she decided they mattered. Code validates every call: the
   entry exists, is supported and belongs to this unit; the district is one
   of the unit's rooms; the spec is within length. Doctrine says when to use
   it; the verb's description does not enumerate cases.

`world_place` writes her act into the Record, never the Notebook, so it keeps
"one writer per tier" and "the conscious never writes the Notebook". It is
still a new act with model cost, so it needs a measurement before it ships
on (`50-open-questions.md` §4).

## 6. What would reopen this

- A world element is wanted that needs an observation of a person (a quote,
  a photo from chat). That breaches the operator's rule and needs a ruling.
- The operator rules that people from different units should meet in one
  place. Then that place needs its own ruling on what it may show
  (`50-open-questions.md` §3).
