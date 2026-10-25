---
type: design
title: "Migration: evidence first, replay before any proposal, one recipe at a time"
description: "Six steps: the evidence store and extraction in shadow, the origin model, the ranker measured on replay, the venue recipe live behind the cap, the games recipe once the play and social streams exist, then personalisation reads. What it depends on and what stays open."
tags: [nora, fit, migration, steps]
timestamp: '2026-10-25'
edges:
  - {dst: 50-verification, rel: depends_on, provenance: asserted}
---

# 60 · Migration

| step | ships | gate |
|---|---|---|
| **1. Evidence, in shadow** | the evidence table with per-row disclosure requirements; extraction from messages, reactions, outings and (when it exists) social items; posteriors computed at read time; nothing reads them yet | extraction agreement on a hand-labelled sample of messages and items; every row cites its source |
| **2. The origin model** | rungs 1 to 4 with provenance; a read for "where will each attendee be at `T`" | on past outings with known arrivals, the rung-3 prediction against where people actually came from |
| **3. The ranker, offline** | the eight stages; the venue recipe; runs only in replay | replay beats both baselines on hit@3 and NDCG@10 for venues ([50](50-verification.md) §1) |
| **4. Venues, live** | the Cognition act verb that starts a run; the proposal as a Standing decision; the bandit and the cap; the `proposal` intent on the send | a shadow week (runs and logs, posts nothing), then live with the cap at its lowest; the negatives (§3) |
| **5. Games** | the games recipe and its catalogue source | depends on the play-profile rollup and the social stream; replay as in step 3 |
| **6. Personalisation reads** | the preference facts rendered into her turns for the people in the window | eval-gated like any prompt change |

## Depends on

- **The standing ledger** (decisions, turn records): the ranker's output and its
  audit trail. Step 4 needs its step 2.
- **The social stream and the play-profile rollup** for the social and played
  evidence. Steps 1 to 4 work without them, with weaker evidence.
- **The agent worker port** is not required: candidate generation runs as an
  ordinary sub-agent.

## Open

| question | who decides | when |
|---|---|---|
| the engine's name ("Fit" is provisional) | the operator | before step 1 |
| the games catalogue source | the operator, after a spike on coverage and terms | before step 5 |
| whether a DM can ask for a recommendation that ranks for a group the person is in | the private-dm amendment | before step 4 |
| the starting weights and half-lives | replay, step 3 | step 3 |
