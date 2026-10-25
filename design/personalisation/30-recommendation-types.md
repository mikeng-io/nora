---
type: design
title: "Recommendation types: a domain is data on the engine"
description: "What a recommendation type declares, the two first recommendation types (a venue for a plan, games for the group) in full, and how a third is added without new machinery."
tags: [nora, recommendations, recommendation-types, venues, games]
timestamp: '2026-10-25'
edges:
  - {dst: 20-ranker, rel: details, provenance: asserted}
---

# 30 · Recommendation types

## 1. What a recommendation type declares

| field | holds |
|---|---|
| `facets` | the vocabulary the model extracts into, closed per recommendation type |
| `vetoable` | the facets average-without-misery may veto on ([20](20-ranker.md) §6) |
| `constraints` | the hard filters and which facts or columns feed them |
| `sources` | candidate generators and, for the outside-change trigger, what to watch |
| `weights` | per feature, summing to 1; and `λ`, `μ`, the misery threshold, the shortlist size |
| `evidence` | per evidence kind, the weight and half-life ([10](10-likes.md) §2–3) |
| `triggers` | which of the four apply, with thresholds and the urgency curve |
| `cap` | the most unsolicited proposals per unit per week |

A recommendation type is a reviewed data file, not code. A new domain is a new recommendation type plus,
if it needs one, a candidate source behind a port.

## 2. Recommendation type: a venue for a plan

A venue for an undecided group plan.

- **facets:** cuisine, drinks-led or food-led, price band, vibe (quiet, loud,
  dancing), group size it suits, open late.
- **vetoable:** cuisine, vibe.
- **constraints:** open at `T` through the plan's expected end; takes the
  group's size; dietary facts; places someone ruled out; within the frame's
  area.
- **sources:** places search along the attendees' routes to `T`
  (`place_info` and the routes client); the unit's past outings as familiar
  candidates (`rollup_outing`).
- **weights to start:** logistics 0.35, likes score 0.25, judged suitability 0.15,
  timing 0.1, cost 0.1, novelty 0.05. `λ` 0.6, shortlist 3.
- **triggers:** undecided plan (urgency rises from 10 days out, sharply from 4;
  doubled for a Friday or Saturday or a public holiday); asked.

## 3. Recommendation type: games for the group

The case of reels and links nobody acted on.

- **facets:** genre, co-op or competitive, player count, session length,
  platform, price band, art style.
- **vetoable:** platform, player count.
- **constraints:** supports the group's size; playable together by every frozen attendee (coverage 1);
  not already in their play history; within a stated price.
- **sources:** a games catalogue (store or a games database) queried for
  titles similar to the seeds; the play history and Discord and Steam presence
  for what they own and play; release and sale dates as watch sources.
- **weights to start:** similarity 0.3, likes score 0.25, coverage 0.2
  ([20](20-ranker.md) §5), timing 0.1, cost 0.1, novelty 0.05.
  Shortlist 3.
- **triggers:** interest burst (several members sharing or reacting to one
  genre within two weeks); outside change (a seed game's sequel, a sale on a
  shortlisted title); asked.

## 4. The third recommendation type

Films, trips, gifts, a restaurant for two in a DM: each is a vocabulary,
constraints, sources and weights. If one needs a new candidate source, the
source is the only code. If one needs a new feature, that is a change to the
ranker and to this bundle, and it is measured like one. Coverage is already
defined in [20](20-ranker.md) §5 and ships with that ranker in step 3; step 5
adds catalogue mappings and verifies them, rather than inventing a feature
inside type data. The venue and games weight lists both sum to 1.

**Review evidence.** A weighted feature must exist in the ranker's §5 feature
list; the earlier games declaration named an undefined group feature. The
complete coverage formula, normalisation and unknown rule now live at
`design/recommendations/20-ranker.md:78`, and the games weights reference the
feature in that file's list (`design/recommendations/20-ranker.md:72`).
[60](60-migration.md) ships it before games.
