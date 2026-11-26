---
type: design
title: "Nora World: presentation and avatars"
description: "An isometric toon-shaded diorama in the portal, one world per unit; VRM avatars in two parts, the person's own look and Nora's reading of them, drawn per unit; the person's veto."
tags: [nora, world, avatar, vrm, presentation, three-js, unit]
timestamp: '2026-10-16'
status: proposed
governance: pre-governance-input
edges:
  - {dst: 00-overview.md, rel: realizes, provenance: asserted}
  - {dst: 10-world-as-a-view.md, rel: depends_on, provenance: asserted}
---

# Presentation and avatars

## 1. The look

An **isometric, toon-shaded diorama** (the Animal Crossing family), rendered
with react-three-fiber in the portal.

- It runs in a phone browser, and needs no first-person controls.
- Stylised avatars avoid the uncanny valley that realistic 3D falls into.
- Generated items sit on a grid, so an item that is slightly off in scale or
  style still reads as belonging (`30-asset-pipeline.md` §4).
- One fixed palette and one toon material for everything; the pipeline
  applies both, so library items and generated items match.

**Layout.** One world per unit (`10-world-as-a-view.md` §1). Nora's home at
the centre, one district per room of the unit around it, and the shared venue
as its own building (`40-shared-venue.md`). The world follows Hong Kong time:
day and night, and events that appear before their date and leave after it,
so returning tomorrow shows something changed.

**You visit Nora's world.** People do not get their own worlds. That keeps
her cognition as the single source and gives one place to look for what
changed and why.

## 2. Avatars: the person's look and Nora's reading

| part | what | who sets it | changes |
|---|---|---|---|
| **the person's look** | body, face, hair, base outfit | the person, in a VRM editor (VRoid) or from a preset | only when they change it; Nora never touches it |
| **Nora's reading** | items attached to one point: something held, a hat, a badge, a backpack; a title or the nickname she uses; a small memo bubble | Nora, from the person's Character and Register entries in this unit | as her picture of the person changes |

Examples of Nora's reading: hiking boots in hand for someone she knows climbs
every weekend; a lucky charm before an exam she knows about, gone the day
after.

If Nora decided the whole character, people would feel labelled; if she had
no part, it is any avatar app. The split keeps the body theirs and the
reading hers.

**Veto.** A person can hide any single item of Nora's reading, or all of it.
A hide is that person's act in the Record (`10-world-as-a-view.md` §4).
Whether Cognition may ever read hides as a signal about the person is open
(`50-open-questions.md` §6); until a ruling, no read verb returns them.

**Only attached items.** Clothing that has to deform with the body needs
rigging, and automatic generation does that badly. Nora's reading is limited
to items attached to one bone.

## 3. Nora's reading differs per world

A person in two units has an avatar in two worlds, and Nora's reading in each
comes only from that unit's Notebook. Nora sees you differently in each
group, and the worlds show that. The person's look is the same in both: it
is theirs, not knowledge of either unit.

## 4. Nora's own avatar

Her VRM is the parked spike on branch `mikeng-io/avatar-3d-spike` (Blender
build script, GLB→VRM converter, contract check, three.js viewer). It was
parked because the procedural look was poor and the image-to-3D exports were
paywalled. The Hunyuan3D path in `30-asset-pipeline.md` is a candidate source
mesh for it, run through the same converter.
