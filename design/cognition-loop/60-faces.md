---
type: design
title: "Faces — detect, embed, match; labels by reference photos and by labelling a face, effective immediately"
description: "How face recognition works without training (templates, nearest neighbour), the two ways labels arrive (reference photos; labelling a detected face in a photo, in the portal or in chat), unknown faces, what is stored, and where it runs."
tags: [nora, perception, faces, recognition, labelling, portal, instagram]
timestamp: '2026-09-28'
edges:
  - {dst: 20-observe, rel: extends, provenance: asserted}
---

# 60 · Faces

In scope now (operator 2026-09-28). A perception step of Observe: its output
is stored on the media item as `faces: [{box, person_id | unknown,
confidence}]` and read by cognition like any other perception.

## 1. How it works: no training

Two models, both open, both CPU-capable on norty:

1. **Detect** — find every face and its box in an image or video frame. A
   face-specific detector (SCRFD, or a YOLO face model); a general YOLO finds
   people, not faces.
2. **Embed** — turn each face crop into a vector (ArcFace, e.g. through
   InsightFace). Two crops of the same person land close together.

**Matching is nearest neighbour** against a set of labelled vectors
(templates) per person, with a distance threshold. **Adding a label adds a
template**, effective on the next image: there is no training run, which is
what makes real-time labelling possible.

## 2. Where labels come from

| way | how | when |
|---|---|---|
| **reference photos** | the operator supplies a few photos per person (a folder keyed by person id, beside `identity.toml`); each detected face becomes a template | bootstrap |
| **labelling in the portal** | upload a photo; the detector draws a box per face; tap a box, pick a person. The operator's "yellow circle is one person, blue circle is another" is exactly this, with the circles drawn by the detector | any time |
| **labelling in chat** | Nora replies with the detected faces numbered on the image; "1 is X, 2 is Y" labels them | any time |
| **cognition asks** | the act step may `ask` a member who a *member-matched but low-confidence* face is, once, with the crop | when a match is near the threshold |

A label given in any of these ways is the same row: `(person_id, template,
source item, labelled_by, at)`.

## 3. Unknown faces

A face with no template within the threshold is `unknown`, and that is all
that is kept: **no vector, no crop, no clustering** of faces that are not
members' (operator 2026-09-28, members only). An unknown face is never named
by inference from who else was there.

## 4. Where it runs and what it costs

In the ingestion path, once per new media item: story frames, posts, reels,
and photos posted in rooms. The perception container sits beside the Instagram
bridge. A ten-frame story is ten detections and at most a few dozen
embeddings: CPU seconds, not model-API calls. Backfilling months of photos is
long work for the compute path (a later `delegate` verb, [30](30-cognition.md)
§7).

## 5. Measure

- A labelled set of frames from the unit's own stories, held out from the
  templates: precision per person at the chosen threshold, and the rate of
  known people returned as `unknown`.
- A wrong name is worse than `unknown`; the threshold is set for precision.
- `nora_face_match_total{outcome}` (known, unknown, below-threshold).
