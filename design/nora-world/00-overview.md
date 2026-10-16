---
type: design
title: "Nora World: overview"
description: "What the world is, what it is not, why it is a View rather than a store, and when each part can be built. Parked 2026-10-16: research only, nothing built."
tags: [nora, world, 3d, portal, view, avatar, venue]
timestamp: '2026-10-16'
status: proposed
governance: pre-governance-input
edges:
  - {dst: ../cognition-loop/05-data-model.md, rel: depends_on, provenance: asserted}
---

# Nora World: overview

**Status:** proposed, parked. Research only; nothing is built.
**Date:** 2026-10-16

## 1. What this is

A shared 3D place people visit in a browser, signed in with Discord. It has a
fixed base built by hand, and what fills that base, and how it changes, comes
from Nora's cognition: what she has noted, what she knows about each group,
what is planned, where people go, what she makes of each person. The
operator's word for it is 具現化: her understanding made visible.

It started as a narrower question (karaoke and concerts in a Discord voice
channel, `40-shared-venue.md`) and grew. The venue stays as the first building.

## 2. The rule everything else follows

**The world is a View, not a store.** In the data model (DEC-249,
`design/cognition-loop/05-data-model.md` §1 and §6) a View is a per-reader
slice assembled at read time through one path, under lineage and withdrawal,
and never stored as data. The world is one more portal View:

- it reads the **Notebook** (Register: note, memo, event; Character;
  Standing) and the **Rollup** (places, rhythm, ties), never the Record;
- lineage is applied for the viewer's rooms, so a viewer sees only what their
  rooms may see;
- withdrawal applies, so a withdrawn or forgotten entry takes its object out
  of the world with it.

Two things follow. The world never needs its own privacy model; it inherits
the one the Notebook already has. And the world can be rebuilt from scratch
at any time, because nothing in it is the source of anything.

What the world does store is presentation state: which asset stands for an
entry, where it was placed, the person's own avatar. That is decoration on
top of the View, keyed by entry id, and deleting it loses looks, never
knowledge (`10-world-as-a-view.md` §3).

## 3. Who decides what

| part | decided by |
|---|---|
| the base: terrain, Nora's home, districts, the venue | the operator, by hand (Authored) |
| what each district holds | the projection from the View (`10-world-as-a-view.md`) |
| what Nora chooses to make real, and how it looks | Nora, through a `world_place` act from Cognition (`10-world-as-a-view.md` §4) |
| turning a spec into a 3D item | the asset pipeline, code plus models, no agent (`30-asset-pipeline.md`) |
| a person's body and base look | that person (`20-presentation-and-avatars.md`) |
| Nora's reading of a person, worn on their avatar | Nora; the person may hide it (`20-presentation-and-avatars.md`) |

## 4. Where it lives

A route in the existing portal (`app/`, Next.js), with react-three-fiber for
the 3D. The portal already signs people in with Discord and resolves every
sign-in to a `person_id` from `identity.toml` (`app/src/auth.ts`). A
standalone page was chosen over a Discord Activity because an Activity's
iframe allows only mapped domains and its microphone access is unverified;
the same page can be wrapped as an Activity later (`50-open-questions.md` §1).

## 5. When

| part | can start | waits on |
|---|---|---|
| base, avatars (person's layer), the venue | now | nothing in memory |
| the projection | after the Register, Character and Standing kinds converge on the entry | the entry migration (`design/cognition-loop/05-data-model.md` §9) |
| Nora's layer on avatars | with the projection | per-district lineage (`20-presentation-and-avatars.md` §3) |
| `world_place` | after the projection is read back | Cognition's write verbs |
| asset generation | any time; it is independent | a Modal account and the license read (`30-asset-pipeline.md` §5) |

Each memory kind that is redesigned after its world mapping is built costs a
rewrite of that mapping, which is why the projection waits.
