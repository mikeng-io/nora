---
type: contract
title: "The Knowledge Tier End to End — Five Passes, and Traversal Without a Graph Database"
description: "Ingestion to retrieval as five named passes with their ordering constraints, plus the multi-hop design: bounded recursive traversal in Postgres, depth 2, with a fan-out cap and a dense-node guard. No AGE, no Neo4j — decided, not deferred."
tags: [nora, knowledge-tier, pipeline, ingestion, extraction, multi-hop, pgvector]
timestamp: '2026-08-23'
edges:
  - {dst: 21-substrate-contract, rel: depends_on, provenance: asserted}
  - {dst: 23-retrieval, rel: extends, provenance: asserted}
  - {dst: 20-knowledge-tier, rel: realizes, provenance: asserted}
---

# The Knowledge Tier End to End

## 1. Five passes, and the order is a constraint

| # | pass | key | trigger | exists? |
|---|---|---|---|---|
| 1 | **extraction** | scope | watermark over new messages | **yes** |
| 2 | **adjudication** | workspace | work queue over pending candidates | **yes** |
| 3 | **promotion** | workspace | after any write to `fact` | **no** |
| 4 | **embedding** | workspace | after promotion | **no** |
| 5 | **retrieval** | — | on a turn, and on a verb | **no** |

**Three of the five do not exist**, and the two that do have never produced a
row ([01-live-state.md](01-live-state.md)). This node specifies all five and the
ordering that binds them, so no pass is discovered by the pass after it.

## 2. Pass 1 — extraction

Shipped: adaptor `fact-candidates`, **scope-keyed**, watermark-driven, no tools
today ([03-no-agent-loop.md](03-no-agent-loop.md); D5 gives it tools).

Reads a batch of messages rendered as `id: body` — **speakers deliberately
absent** ([21](21-substrate-contract.md) §7.1). Emits `CandidateClaim`:

```
subject: str    predicate: str    object: str    message_id: int
```

Written to `fact_candidate` with `observed_in` carrying the message ids, and a
`occurrence_count` that is **the recurrence signal Phase A must not
deduplicate against standing facts** (D5's load-bearing half).

**The asserter join happens here** ([21](21-substrate-contract.md) §7.2):
`message_id → sender_id → person_id`, in code, at the sink. The model
contributes only the `message_id` it already returns and `accept` already
validates.

## 3. Pass 2 — adjudication

Shipped: adaptor `fact-adjudication`, **workspace-keyed**, `uses_watermark =
False` — a work queue, not a clock. Holds `web_search`, `web_fetch`, `now`,
`calculate`. Emits `FactVerdict`:

```
subject  predicate  object  status  addresses_claim  source_url  source_excerpt  supersedes
```

`status ∈ supported | contradicted | not_covered`, and R-53's rule holds: a
source that does not mention a thing has not denied it, so `not_covered` is
never a contradiction.

**The join key is the raw strings** — the objective says *"the words are how it
is matched back"* — which is why neither pass may canonicalise
([21](21-substrate-contract.md) §6.2).

## 4. Pass 3 — promotion (new)

Implements [21-substrate-contract.md](21-substrate-contract.md) §6.1. One
`GROUP BY` over standing facts per workspace:

- **veto** dates, quantities, negations, URLs, and strings over 64 characters;
- **promote** any surviving object string whose normalised form appears in a
  second edge position — as some fact's `subject_id`, or another fact's
  `object_literal`;
- **upsert** `fact_entity` (canonical name = most frequent surface form, all
  distinct surface forms into `aliases`), then rewrite the promoted rows to
  `object_entity_id`.

Runs **after any write to `fact`**, because degree changes only when a fact
lands. Cheap enough to run every adjudication tick: one aggregate over a
few thousand rows.

**This pass is what makes §7 possible.** A literal object is a dead end; there
is nothing to traverse to until promotion has run.

## 5. Pass 4 — embedding (new)

**What is embedded: the rendered triple, not the object string.**
`subject │ predicate │ object` — measured in
[23-retrieval.md](23-retrieval.md) §6.2 to retrieve correctly across languages,
and it is already the context-prefix shape the prior art constructs by hand.

**The fingerprint covers model identity, parameters, AND the exact text
embedded.** This is one mechanism doing two jobs, and the second is not
optional:

> **Promotion rewrites the triple's rendered text**, so a promoted row's stored
> vector is stale the moment pass 3 touches it.

A fingerprint over the model alone would not see that — the model did not
change, the *row* did. Hashing the embedded text closes it, and the same
predicate (`fingerprint ≠ current`) then finds both a model swap and a
promotion, with no second mechanism and no ordering trap.

**Ordering is therefore load-bearing: 3 before 4, always.** Embedding a row
that promotion is about to rewrite wastes the call and leaves a vector that
disagrees with its own row until the next sweep.

Batched through the Ollama endpoint; a service that is down is `unavailable`,
never `nothing_found`.

## 6. Pass 5 — retrieval

Specified in [23-retrieval.md](23-retrieval.md): an extent line and a
turn-anchored 1-hop neighbourhood on every turn, a three-channel verb on demand.
§7 below is the part 23 left at one hop.

## 7. Multi-hop, decided

### 7.1 The mechanism is `WITH RECURSIVE`, and it is already available

Verified on the live database, 2026-08-23: `WITH RECURSIVE` executes to depth 3.
Nothing needs installing.

Traversal starts from the entities resolved out of the turn (or out of the
verb's query), walks `fact` rows whose `subject_id` matches a frontier entity,
and collects `object_entity_id` as the next frontier. **Only entity objects
extend a path** — a literal terminates it, which is §4's dependency stated as a
query property.

### 7.2 Four bounds, and each has a reason rather than a feeling

| bound | value | why this and not more |
|---|---|---|
| **depth** | **2** | On a workspace this size, hop 3 reaches most of the graph, and a filter that returns everything is not a filter. Hop 2 answers the questions worth asking — *what else did House House make*, *who else is at that wedding* |
| **fan-out** | **8 edges per node per hop** | Bounds the worst case at 1 + 8 + 64 rows before ranking. Ordered by the fused score of the parent, so the cap drops the least relevant branch, not an arbitrary one |
| **dense-node guard** | **do not expand through an entity of degree > 30** | The super-node problem. A hub — a person, a recurring venue — connects to everything, so expanding through it returns the workspace. Related prior art calls a node dense at 50+ and warns the condition is not reversible; 30 is the conservative form at this scale |
| **cycle** | visited set carried in the CTE | `A → B → A` otherwise recurs to the depth bound and returns duplicates dressed as results |

### 7.3 Distance decays, it does not gate

A hop-2 result is not disqualified, it is **discounted**: its fused score is
multiplied by a decay per hop, so a distant-but-strong match can still outrank a
near-but-weak one. This is the focal-node reranker of
[20-knowledge-tier.md](20-knowledge-tier.md) §6 with the hop count as its
distance, and it is arithmetic — no model.

A hard depth gate would make hop 2 either free or forbidden. Decay makes it
*earn* its place, which is the behaviour a mixed corpus needs.

### 7.4 What multi-hop is for, and what it is not

**For:** the questions one hop cannot express. *What else did they make*, *what
does this unlock that unlocks something else*, *who else is connected to this*.

**Not for:** recall in general. The lexical and dense channels find rows that
*mention* a thing; traversal finds rows *related to* a thing that never mention
it. They are different failures and the structural channel only fixes the second.

**And it does not fix `打風`.** The Cantonese-slang miss in
[24-embedding-seam.md](24-embedding-seam.md) §7.6.3 is an alias problem — the
query never reaches the entity, so there is no frontier to expand from.
Traversal amplifies a good starting point; it cannot supply one.

## 8. No graph database — decided, not deferred

**The store is Postgres, with `pg_trgm` and `pgvector`. There is no AGE
extension, no Neo4j, and no plan for either.**

The earlier framing kept a graph engine as a deferred option whose reversibility
was bought by canonical entity ids. **The entity ids are still the thing that
matters — and they were never really about AGE.** They are what makes
resolution, conflict detection, ranking and traversal expressible at all
([20-knowledge-tier.md](20-knowledge-tier.md) §5). A store swap was one
beneficiary among four, and the smallest.

Three reasons this closes rather than defers:

- **§7 needs no engine.** Bounded traversal is a recursive CTE, verified
  present. What a graph database adds at this scale is Cypher syntax, not
  capability.
- **AGE is a second extension on the same server**, with its own query language
  and its own version coupling — a boundary inside one store, for a traversal
  that is already one statement.
- **The Neo4j on this host belongs to a neighbouring system.** It is not
  evidence that this tier wants one; it is a different application's store, and
  reaching into it would be the boundary error
  [24-embedding-seam.md](24-embedding-seam.md) §2.1 catalogues for Honcho.

**What would reverse it**, stated so the decision is falsifiable rather than
final: traversal that needs **variable-length paths without a depth bound**, or
**path-shape predicates** ("a route from X to Y through any number of
`made_by` edges"). Neither is expressible in a bounded CTE without pain, and
neither appears in any question this room has asked. If one does, that is the
reading that reopens this — not a preference for graph databases.

## 9. What each pass owes its instrument

[09-measurement.md](09-measurement.md)'s rule, applied per pass, since three of
them do not exist yet and can therefore ship with their instrument rather than
after it.

| pass | the reading |
|---|---|
| extraction | candidates written per tick, **and the uncounted parse drop given a counter** ([23](23-retrieval.md) §8.4) |
| adjudication | verdicts by `status`; how many candidates were re-adjudicated (a non-zero steady state means the negative cache is broken — [21](21-substrate-contract.md) §6.4) |
| promotion | entities minted, rows rewritten, and **objects vetoed by rule**, so a veto list that is eating real entities is visible |
| embedding | rows re-embedded and **why** — model change or promotion — because one is expected and the other is a rewrite storm |
| retrieval | per channel: candidates returned, and whether the channel was *reachable*. `nora_fact_entity_unresolved_total` finally gets a call site |

## 9.1 The tier's caps, gathered

Scattered across code and config, and never stated together — which is how the
note body came to have none at all.

| field / knob | value | layer |
|---|---|---|
| `predicate` | **100** chars | domain constant today; becomes `[facts] max_predicate_chars`, ceiling **200** |
| `subject_id` / `object_literal` | **uncapped today** | become `[facts] max_term_chars`, operating **200**, ceiling **800** |
| promotion veto length | **64** chars | §4 / [21](21-substrate-contract.md) §6.1 — a string longer than this is a sentence, not a thing |
| `max_adjudications_per_tick` | 10 | config, exists |
| `max_rendered_facts` | 8 | render cap, config, exists |
| `retention_days` | **none — keep indefinitely** | [21](21-substrate-contract.md) §10 |

**`subject_id` and `object_literal` being uncapped is the same defect as the
note body**, and it bites harder here: the subject is a **join key**
([21](21-substrate-contract.md) §6.2), so an unbounded subject is an unbounded
index entry on the one column every read path uses.

**200 characters, because the promotion veto already says 64.** A term that will
never be promoted still has to be stored and matched; 200 leaves room for a
descriptive object while keeping the index key small, and anything approaching
it is a claim the extractor should have split.

## 10. Nothing here is deferred

The decay constant is **0.6 per hop** ([23](23-retrieval.md) §9). The veto list
is an **operator-owned, versioned file** ([21](21-substrate-contract.md) §10) —
its contents are configuration, and its ownership, versioning and hashing are
decided. Every structural choice above is made, and every number has a default
with its reasoning.

## Status / Checkpoint

2026-08-23 — written to close the pipeline end to end after the operator asked
how extraction, ingestion and retrieval fit together and how multi-hop works,
and required that the bundle decide rather than defer. It **corrects
[23-retrieval.md](23-retrieval.md) §7**, which had argued for a plain vector
column: that conflated *no ANN index needed* with *no extension needed*, and
pgvector is adopted on `pgvector/pgvector:pg18`.
