---
type: design
title: "Intent: one personalisation engine, three jobs"
description: "What the operator asked for, why personalisation, recommendations and suggestions are one engine with three jobs, the future jobs it can take, why the classic recommender toolbox does not fit a friend group, what the engine reads that already exists, and the split between what the model does and what code does."
tags: [nora, recommendations, recommendation, personalisation, proactive, member]
timestamp: '2026-10-25'
edges:
  - {dst: 10-likes, rel: sequences, provenance: asserted}
---

# 00 · Intent

## 1. The ask

Today the Cognition session's one act verb is `remind` (DEC-277). Asked about
an undecided Halloween venue, the best she can do is say it is undecided. The
operator wants a member who proposes: "they want to play this sort of game"
followed by a shortlist; "the party has no venue" followed by two places that
are on everyone's way, accounting for who is working late.

## 2. One engine, three jobs

The engine is **personalisation**. Its future code home is `nora/personalisation/`.
Personalisation, recommendation and suggestion are three jobs in one
pipeline. They are not three engines: two engines would each keep their own
reading of what a person likes, and the readings would drift.

| job | answers | owner | output |
|---|---|---|---|
| **likes** | what each person likes, needs or will not do | a read view of profiles ([10](10-likes.md)); no store of its own | nothing visible by itself |
| **personalised replies** | what Nora leads with, leaves out or checks, for the people in this turn | likes in her turn context ([25](25-personalisation.md)) | how she answers; no list, no announcement |
| **recommendations** | which outside options are best for these people, here, now | the one **ranker** ([20](20-ranker.md)), with each domain as a **recommendation type** ([30](30-recommendation-types.md)), as data | a ranked shortlist with reasons: a Standing decision ([standing-ledger 20](../standing-ledger/20-the-ledger.md) §2) |
| **suggestions** | whether to offer it now, and to whom | the existing speaking path: Cognition, fire turns, the weekly cap and **learning from picks** ([40](40-feedback.md)) | the shortlist said in the room, or held back |

Only the likes view and the ranker are new. Profiles and the speaking path
already exist. These names are the operator decision of 2026-10-25, the same
convention that Netflix and Spotify use: personalisation is the umbrella, and
recommendations are one product built on it. Profile parts and prompt
identities follow
[ADR 0029](../../docs/adr/0029-a-prompt-block-names-its-subject-and-author-by-id.md).

## 3. Why not the usual recommender toolbox

Collaborative filtering, matrix factorisation and learned ranking models need
many users and many interactions per item. A unit is a handful of people and a
few dozen outings or games a year. Those methods have nothing to learn from
here, and they explain nothing. What fits a small group with rich context is
the older, well-studied family:

- **knowledge-based recommendation**: hard constraints first (open at that
  hour, enough players, under budget, not already owned);
- **content-based scoring**: compare a candidate's attributes and embedding to
  what each person has shown they like;
- **multi-criteria scoring**: likes score, logistics, timing, cost, novelty,
  each normalised, weighted per recommendation type;
- **group recommendation** aggregation strategies (Masthoff's taxonomy:
  average, least misery, average without misery, fairness over time);
- **diversified shortlists** (maximal marginal relevance);
- **learning from picks**: when to propose, from the response
  each proposal got.

## 4. What it reads, and what exists today

| signal | what it says | store | exists |
|---|---|---|---|
| what people say | likes, dislikes, plans, constraints | messages; write-only extraction; profiles for likes | messages and reading writer exist; the destination-scoped constraint and likes readers are step 1 ([10](10-likes.md) §1); the deleted fact tier is not a source |
| **social engagement** | what members post (places they went, food, game clips), what they send each other (reels, links), what they react to | the social stream's `social_item`, perceptions and counts, read through `social_read` ([22](../cognition-loop/22-social-stream.md) §8, built S0 to S8); reactions; links in rooms | yes (the poller ships off by default) |
| where people are, and usually are | origin at a given hour; usual routes and their speeds | the hour-of-week forecaster `nora/domain/mobility_forecast.py` over historical stays; separate `presence_occupancy` bins; `presence_place_rhythm` (person/place circular means, no bins); `presence_trips`, `presence_corridors`, `presence_derived_places` | yes |
| where the group has been together | revealed venue preference | `rollup_outing`, `rollup_colocation` | yes |
| who does things with whom | likely attendee sets | `rollup_tie` | yes |
| what they play | genres, hours, who with | Steam current game (`nora/infra/steam.py`), Discord rich presence (`nora/platform/discord/discord_presence.py`); the play-profile rollup ([25](../cognition-loop/25-rollup.md) §6) | live reads yes; the rollup is designed |
| plans | the event, its RSVPs, its open venue | `events`, `event_rsvps`, notes | yes |
| how her proposals land | replies, reactions, edits, silence | `rollup_response` episodes, with responders; recommendation sends need a separate decision link ([learning-from-acts](../unified-memory/50-learning-from-acts.md) §1) | yes |
| candidates | venues and travel times; games | `nora/infra/routes/directions.py`, `nora/infra/transit/arrivals.py`, `place_info`; a game catalogue source | venues yes; games new |

## 5. Who does what

**The model reads; code decides the numbers.** The model projects eligible profile claims and their cited
evidence into facets, and extracts a candidate description once,
and judges suitability where a facet cannot be computed. Code aggregates evidence,
decays it, computes travel, applies constraints, aggregates for the group,
picks the shortlist and records why. A ranking made of code-computed parts can
be replayed and measured ([50](50-verification.md)); a ranking a model asserts
cannot.

## 6. Future jobs on the same engine

Each future job is a new recommendation type, or a new use of likes. None
is a new engine. Each one ships only with its own replay or eval gate, as
in [50](50-verification.md). The order is a proposal.

| job | layer | what it adds | needs |
|---|---|---|---|
| **venues** | recommendations | the first recommendation type | built inputs ([60](60-migration.md) step 4) |
| **games** | recommendations | catalogue and coverage | a game catalogue source (step 5) |
| **reply style** | personalised replies | length, language and register per person, read from profile traits | nothing new ([25](25-personalisation.md)) |
| **times** | recommendations | rank candidate slots for a plan from stated availability, dated plans and the presence forecast | the step-1 constraint reader, the step-2 origin model |
| **shared meals** | personalised replies + recommendations | gather the group's dietary rules and likes for one order or one place, without listing who said what | the constraint reader |
| **meeting point** | recommendations | rank a place to meet, or the next place to go, by the logistics feature alone | the origin model |
| **reminder timing** | suggestions | when, and in which room or DM, each person is most likely to act on a reminder, learnt from response episodes | learning from picks over reminder sends; [private-dm](../private-dm/) for DM routing |
| **things to share** | recommendations | which link, release or news item is worth raising, and to whom, from the social stream | the social poller enabled |
| **gifts and occasions** | recommendations | ideas for a member's occasion from their likes, offered only in a destination that excludes that member | birthdays stay told-only (the existing contract) |

Rejected as a job: **who to invite.** Ranking people by tie strength would
put an exclusion list in a friends' room. Ties choose likely attendees for a
frame someone else set, never a list Nora proposes.

## 7. Out of scope

- Acting on another network (booking a table, buying a game). A proposal can
  offer to; the act is its own decision.
- The unit topology and DM bundles, beyond reading their scopes.

**Inventory evidence.** Retained fact validity columns are
`nora/infra/sql/schema.py:2015`; deletion of their writers is
`design/09-decision-index.md:268`. Extraction are write-only today
(`docs/maintainers/extraction.md:9`), so their consumer is named build
work. Occupancy bins and person/place rhythm differ at
`nora/infra/sql/schema.py:517` and `nora/infra/sql/schema.py:543`; the forecast
reads stays at `nora/domain/mobility_forecast.py:235`. The built social
reader, including current place, readings and counts, is
`nora/infra/sql/social_stream.py:893`. Built does not assert enabled or deployed.
