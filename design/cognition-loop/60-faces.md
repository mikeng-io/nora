---
type: design
title: "Faces — detect, embed, match; labels only from the subject or the operator, effective immediately"
description: "How face recognition works without training (templates, nearest neighbour), who may label and how (reference photos, the portal, the subject in chat), unknown faces and transient embedding of non-members, custody of crops and images, what is stored, where it runs, and how it is measured."
tags: [nora, perception, faces, recognition, labelling, portal, instagram]
timestamp: '2026-09-28'
edges:
  - {dst: 20-observe, rel: extends, provenance: asserted}
---

# 60 · Faces

In scope now (operator 2026-09-28). A perception step of Observe: its output
is stored on the media item as `faces: [{box, person_id | unknown,
confidence}]` and read by cognition like any other perception. A face places
a person, so every row resting on one carries `presence` lineage
([10](10-tiers.md) §3).

## 1. How it works: no training

Two models, both open, both CPU-capable on norty:

1. **Detect** — find every face and its box in an image or video frame. A
   face-specific detector (SCRFD, or a YOLO face model); a general YOLO finds
   people, not faces.
2. **Embed** — turn each face crop into a vector (ArcFace, e.g. through
   InsightFace). Two crops of the same person land close together.

**Matching is nearest neighbour** against the labelled vectors (templates) of
each person, with a distance threshold set for precision. Templates are keyed
by Nora workspace, because a person is one peer per workspace (DEC-214).
**Adding a label adds a template**, effective on the next image: there is no
training run, which is what makes real-time labelling possible.

A face below the threshold is stored as `unknown`, with no person id.

## 2. Who may label, and how

**Only the subject or the operator may label a face.**

| way | who | how |
|---|---|---|
| **reference photos** | the operator | a few photos per person (a folder keyed by person id, beside `identity.toml`); each detected face becomes a template |
| **the portal** | the operator, or the subject for their own face | upload a photo; the detector draws a box per face; tap a box, pick a person. The operator's "yellow circle is one person, blue circle is another" is exactly this, with the circles drawn by the detector |
| **chat** | the subject only | Nora replies with the detected faces numbered on the image; "1 is me" labels face 1 as the speaker |

A chat label needs the tag model to read "1 is me". That reading is the tag
turn's `face_label` verb, which records the label as a Record item authored
by the person who gave it and citing their message ([10](10-tiers.md) §1): the
Record stays code-written, and the item can be checked against the message it
cites.

**Cognition may ask**, as any act: a reminder whose brief is `ask` with the
crop, only into the room the media came from ([30](30-cognition.md) §2). A
label results only when the subject answers about themselves.

Every label is the same row: `(person_id, template, source item,
labelled_by, at)`. Removing one withdraws it ([10](10-tiers.md) §2 rule 7).

## 3. Faces that are not members'

Matching needs a vector, so **every detected face is embedded, including a
non-member's; a non-member's vector is discarded as soon as it has been
matched**. What is kept of it is `unknown` and its box: no vector, no crop,
no clustering (operator 2026-09-28, members only). An unknown face is never
named by inference from who else was there.

## 4. Custody

- **A crop goes only to the room its media came from** (R-33's custody).
- **Every image she sends passes a custody and face check**: its source
  media is in the target room's custody, and it shows no face that is not in
  that source (R-33, and R-18 amended for images, [70](70-migration.md) §3).

## 5. Where it runs and what it costs

In the ingestion path, once per new media item: story frames, posts, reels,
and photos posted in rooms, beside the existing comprehension pass
([20](20-observe.md) §1). The perception container sits beside the Instagram
bridge. A ten-frame story is ten detections and at most a few dozen
embeddings: CPU seconds, not model-API calls. Backfilling months of photos is
long work for the compute path (a later `delegate` verb, [30](30-cognition.md)
§7).

Retention: templates live until their label is removed or the person is
forgotten ([70](70-migration.md) §9); reference photos until the operator
removes them.

## 6. Measure

- A labelled set of frames from the unit's own stories, held out from the
  templates: precision per person at the chosen threshold, and the rate of
  known people returned as `unknown`.
- A wrong name is worse than `unknown`; the threshold is set for precision.
- `nora_face_match_total{outcome}` (known, unknown).
