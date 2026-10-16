---
type: design
title: "Nora World: presentation and avatars"
description: "An isometric toon-shaded diorama in the portal; VRM avatars in two layers, the person's own body and Nora's reading of them; why Nora's layer varies by district; the person's veto."
tags: [nora, world, avatar, vrm, presentation, three-js, lineage]
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
  style still reads as belonging (`30-asset-pipeline.md` §3).
- One fixed palette and one toon material for everything; the pipeline
  applies both, so library items and generated items match.

**Layout.** Nora's home at the centre. One district per room the viewer
belongs to, around it. The shared venue as its own building
(`40-shared-venue.md`). The world follows Hong Kong time: day and night, and
events that appear before their date and leave after it, so returning
tomorrow shows something changed.

**You visit Nora's world.** People do not get their own worlds. That keeps
her cognition as the single source and gives one place to look for what
changed and why.

## 2. Avatars: two layers

| layer | what | who sets it | changes |
|---|---|---|---|
| **the person's** | body, face, hair, base outfit | the person, in a VRM editor (VRoid) or from a preset | only when they change it; Nora never touches it |
| **Nora's** | items attached to one point: something held, a hat, a badge, a backpack; a title or the nickname she uses; a small memo bubble | Nora, from the person's Character and Register entries | as her picture of the person changes |

Examples of Nora's layer: hiking boots in hand for someone she knows climbs
every weekend; a lucky charm before an exam she knows about, gone the day
after.

If Nora decided the whole character, people would feel labelled; if she had
no part, it is any avatar app. The split keeps the body theirs and the
reading hers.

**Veto.** A person can hide any single item from Nora's layer, or the whole
layer. A hide is presentation state (`10-world-as-a-view.md` §3), never
evidence about the person, and never fed back to Nora as a signal unless a
later ruling says it should be.

**Only attached items.** Clothing that has to deform with the body needs
rigging, and automatic generation does that badly. Nora's layer is limited to
items attached to one bone.

## 3. Nora's layer varies by district

An avatar walks into every district its person belongs to. An item drawn from
group A's entries, shown in group B's district, would carry A's knowledge to
B. So Nora's layer is assembled per district, from the View for that
district's room under lineage: Nora sees you differently in each group, and
the world shows that.

In the shared venue, where people from several rooms meet, Nora's layer shows
only items whose lineage reaches every room present, which in practice means
few or none. That is the safe default, not a bug.

## 4. Nora's own avatar

Her VRM is the parked spike on branch `mikeng-io/avatar-3d-spike` (Blender
build script, GLB→VRM converter, contract check, three.js viewer). It was
parked because the procedural look was poor and the image-to-3D exports were
paywalled. The Hunyuan3D path in `30-asset-pipeline.md` is a candidate source
mesh for it, run through the same converter.
