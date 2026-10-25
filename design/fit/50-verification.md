---
type: design
title: "Verification: replay against what the group actually chose, then live acceptance"
description: "Offline replay of past decisions with only the evidence before each one (hit-at-k, NDCG), per-feature ablation, the live acceptance and ignore rates under the cap, and the negatives: no disclosure, no default for an unknown, no proposal over the cap, no ranking from Character."
tags: [nora, fit, verification, replay, ndcg, acceptance, negatives]
timestamp: '2026-10-25'
edges:
  - {dst: 20-ranker, rel: measures, provenance: asserted}
---

# 50 · Verification

## 1. Replay: would she have proposed what they chose?

The grounded measure, not a proxy. Take past decisions the unit actually made:
an outing at a venue (`rollup_outing`), a game they started playing together
(play sessions), an event's venue once set. For each, rebuild the frame **using
only evidence dated before the decision**, run the ranker over the candidates
that existed then, and record where the actual choice landed.

- **hit@3**: the actual choice is in the shortlist.
- **NDCG@10**: how high it ranked among all candidates.
- **Comparison arms:** a popularity baseline (the unit's most frequent place or
  genre), similarity alone, and the full score. The full score has to beat both
  baselines on held-out decisions for the recipe to ship.
- **Ablation:** drop one feature at a time; a feature that does not move the
  number is removed or re-weighted, not kept on faith.

Replay leaks if extraction or a candidate's facets were computed with later
knowledge, so facets are extracted from the candidate's own description as of
the decision date, and evidence is cut by `at`.

## 2. Live

- **Acceptance rate** (picked or engaged) and **ignore rate** per recipe, from
  response episodes, reported weekly.
- **Unsolicited proposals per unit per week**, against the cap.
- A falling acceptance rate with a stable ignore rate means the shortlists are
  wrong; a rising ignore rate means she is proposing too often. They are read
  separately.

## 3. The negatives: what must NOT happen

- **No disclosure through a reason.** A reason whose evidence fails the room's
  gates (lineage, exclusions, location) is never rendered there, though the
  ranking may still have used it.
- **No default for an unknown.** A frame with a missing origin or schedule
  produces a proposal that names the assumption, or no proposal; never a
  silent home-origin ranking. Tested with a case whose answer flips when the
  missing schedule is known.
- **No proposal over the cap**, whatever the bandit samples.
- **No veto ignored.** A candidate that fails a vetoable facet for an attendee
  with confident evidence never appears.
- **No ranking from Character or from her own summaries.** The preference model
  reads evidence rows only.
- **No laundering across units.** A preference learned in one unit's rooms is
  never used to rank for another unit.
