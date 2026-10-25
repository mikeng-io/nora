---
type: design
title: "Intent: from reminding to proposing, on one engine"
description: "What the operator asked for, why personalisation and recommendation are one engine, why the classic recommender toolbox does not fit a friend group, what the engine reads that already exists, and the split between what the model does and what code does."
tags: [nora, fit, recommendation, personalisation, proactive, member]
timestamp: '2026-10-25'
edges:
  - {dst: 10-preference-model, rel: sequences, provenance: asserted}
---

# 00 · Intent

## 1. The ask

Today the Cognition session's one act verb is `remind` (DEC-277). Asked about
an undecided Halloween venue, the best she can do is say it is undecided. The
operator wants a member who proposes: "they want to play this sort of game"
followed by a shortlist; "the party has no venue" followed by two places that
are on everyone's way, accounting for who is working late.

## 2. One engine, two consumers

Both cases rank options against people. Only the options differ.

| consumer | ranks | for | output |
|---|---|---|---|
| **recommendation** | outside candidates: venues, games, films, trips | the group, or one person in a DM | a Standing decision ([standing-ledger 20](../standing-ledger/20-the-ledger.md) §2) whose `Options` are the shortlist with scores, posted by a fire turn |
| **personalisation** | her own choices: which option to mention first, what to leave out, when to raise something | the people in the room | the same preference facts, rendered into her turns |

So the bundle builds one **preference model** ([10](10-preference-model.md))
and one **ranker** ([20](20-ranker.md)), and a domain is a **recipe**
([30](30-recipes.md)), not new machinery.

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
- **multi-criteria scoring**: preference fit, logistics, timing, cost, novelty,
  each normalised, weighted per recipe;
- **group recommendation** aggregation strategies (Masthoff's taxonomy:
  average, least misery, average without misery, fairness over time);
- **diversified shortlists** (maximal marginal relevance);
- **bandit-style learning** of when and what to propose, from the response
  each proposal got.

## 4. What it reads, and what exists today

| signal | what it says | store | exists |
|---|---|---|---|
| what people say | likes, dislikes, plans, constraints ("I can't do Sai Kung", "I'm working late on the 31st") | messages; dated facts (`fact.valid_from`/`valid_to`) | yes |
| **social engagement** | what members post (places they went, food, game clips), what they send each other (reels, links), what they react to | the social stream's `social_item` ([22](../cognition-loop/22-social-stream.md)); reactions; links in rooms | reactions and links yes; the social stream is designed |
| where people are, and usually are | origin at a given hour; usual routes and their speeds | `presence_place_rhythm`, `presence_trips`, `presence_corridors`, `presence_derived_places` | yes |
| where the group has been together | revealed venue preference | `rollup_outing`, `rollup_colocation` | yes |
| who does things with whom | likely attendee sets | `rollup_tie` | yes |
| what they play | genres, hours, who with | Steam current game (`nora/infra/steam.py`), Discord rich presence (`nora/platform/discord/discord_presence.py`); the play-profile rollup ([25](../cognition-loop/25-rollup.md) §6) | live reads yes; the rollup is designed |
| plans | the event, its RSVPs, its open venue | `events`, `event_rsvps`, notes | yes |
| how her proposals land | replies, reactions, edits, silence | `rollup_response` episodes, carrying the send's intent ([learning-from-acts](../unified-memory/50-learning-from-acts.md) §1) | yes |
| candidates | venues and travel times; games | `nora/infra/routes/directions.py`, `nora/infra/transit/arrivals.py`, `place_info`; a game catalogue source | venues yes; games new |

## 5. Who does what

**The model reads; code decides the numbers.** The model turns unstructured
evidence (a reel, a message, a venue's description) into cited facet evidence,
and judges fit where a facet cannot be computed. Code aggregates evidence,
decays it, computes travel, applies constraints, aggregates for the group,
picks the shortlist and records why. A ranking made of code-computed parts can
be replayed and measured ([50](50-verification.md)); a ranking a model asserts
cannot.

## 6. Out of scope

- Acting on another network (booking a table, buying a game). A proposal can
  offer to; the act is its own decision.
- The unit topology and DM bundles, beyond reading their scopes.
