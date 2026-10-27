---
type: design
title: "Migration: profile reads first, replay before any recommendation send"
description: "Six proposed build steps: profile likes and constraint reads, origins, historical replay and ranker, linked venue sends, games catalogue, then named recommendations in prompt context. No implementation ships in this bundle."
tags: [nora, recommendations, migration, steps]
timestamp: '2026-10-25'
edges:
  - {dst: 50-verification, rel: depends_on, provenance: asserted}
---

# 60 · Migration

| step | ships | gate |
|---|---|---|
| **1. Profile likes and constraints** | in future `nora/personalisation/`, a read-time likes projection over profile facts/traits/guesses and their citations, no second store; a destination-filtered constraint/dated-plan reader over original messages, extraction and applicable typed profile facts; preserve valid time and recorded revision | labelled extraction and source attribution; unknown stays unknown; group choices create no individual likes; no prompt or send changes |
| **2. The origin model** | rungs 1 to 4, with rung 1 supplied by step 1 and rung 3 using the existing stays-based forecaster; destination location gates and provenance | historical known origins versus predicted distributions, with fallback/support recorded |
| **3. The ranker, offline** | eight stages, venue type data, **coverage feature** with formula/unknown handling, frozen frames and historical revision reads for replay; retained run inputs, no sends | paired held-out replay beats both baselines, coverage reported; disclosure invariance and future-leak probes ([50](50-verification.md)) |
| **4. Venues, live** | a recommendation act and Standing decision; **recommendation send** metadata (`kind`, `decision_id`) on all delivered parts, including tag/fire paths; intent stays `tell`/`ask`; learning from picks under the cap | validate intent and abduced-question rules; read back the decision/send/episode link; open/unknown/unanswered never become negatives; disclosure checked before delivery |
| **5. Games** | games recommendation type and catalogue source; map platform, cross-play and supported-player fields into the step-3 coverage feature | depends on available play evidence; replay as in step 3, coverage 0.75/1/unknown measured against the actual catalogue contract |
| **6. Personalised replies and the recommendations block** | the `<likes of="ada" written_by="nora">` view in tag and fire turns ([25](25-personalisation.md)), destination-filtered; ranked items with cited reasons via `<recommendations for="weekend-hikes" written_by="nora">` through existing context assembly; synthetic ids shown here | ADR 0029 blind-reader check on both blocks for Nora and a sub-agent; before/after reply eval ([25](25-personalisation.md) §4); the no-reveal probe; every actual subject/author uses ids |

## Depends on

- **The standing ledger** (decisions, turn records): the ranker's output and its
  audit trail. Step 4 needs its step 2.
- **The play-profile rollup** for the played evidence (designed, not built).
  The social stream is built; its poller ships off by default, so social
  evidence exists only once it is enabled. Steps 1 to 4 work without either,
  with weaker evidence.
- **The agent worker port** is not required: candidate generation runs as an
  ordinary sub-agent.

## Open

| question | who decides | when |
|---|---|---|
| the games catalogue source | the operator, after a spike on coverage and terms | before step 5 |
| whether a DM can ask for a recommendation that ranks for a group the person is in | the private-dm amendment | before step 4 |
| the starting weights and half-lives | replay, step 3 | step 3 |

## Scope and evidence

This bundle is design only: none of these build steps is implemented by this
PR. The retained fact table is not a writer, and the new extraction
writer has no retrieval consumer (`docs/maintainers/extraction.md:9`).
Profiles retain their one existing owner; the new ranker projects likes at
read time. ADR 0029's naming/identity migration and historical profile reads
are explicit prerequisites, not assumed completed work. The existing profile
writer will need to make the response-attribution rule in [40](40-feedback.md)
measurable before response-derived likes are consumed.
