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

> **DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") has been deprecated and its code deleted.**
> This document is retained as a record of the reasoning, not as a description of the system. **Nothing here describes code that exists.**
> The database tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.

# The Knowledge Tier End to End

## 1. Five passes, and the order is a constraint

| # | pass | key | trigger | exists? | ours or vendored |
|---|---|---|---|---|---|
| 1 | **extraction** | scope | watermark over new messages | **yes** | **ours** |
| 2 | **adjudication** | workspace | work queue over pending candidates | **yes** | **ours** |
| 3 | **promotion** | workspace | after any write to `fact` | **no** | ours, over a **vendored matcher** — §4.1 |
| 4 | **embedding** | workspace | after promotion | **no** | ours |
| 5 | **retrieval** | — | on a turn, and on a verb | **no** | ours |

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

### 2.1 D30 — we build this pipeline; exactly one component is vendored

**Decided 2026-08-27**, after a runtime investigation of `graphiti-core` 0.29.3
in an isolated venv, with the driver, embedder and cross-encoder replaced by
stubs that **raise** on access.

> **No dependency on `graphiti-core`. Two files are vendored under Apache-2.0
> with attribution — `prompts/dedupe_nodes.py` (225 lines) and
> `dedup_helpers.py` (296 lines). Everything else in this pipeline is ours.**

**The line falls out of the schema, not from a preference.**
[21](21-substrate-contract.md) §6 makes the object **always a literal**. That
library's edge connects two entity uuids and has **no literal form** — a
literal-valued claim produces no edge at all, dropped with a `logger.warning`
and no counter. So its *claim* layer cannot emit our shape, and that is
architectural rather than tunable.

But **entities are entities in both models**. Its dedup layer operates purely on
names, so it ports without translation — and names are what `fact_entity` holds.
One distinction, and it decides every row:

| | verdict |
|---|---|
| entity resolution (the dedup prompt + the pure matcher) | **vendor** — shape-neutral, and the genuine moat |
| claim extraction | **ours** — `object_literal` has no edge form |
| `extract_edges`, `resolve_extracted_edge`, `resolve_edge_contradictions` | **never** — §2.2 |
| their `LLMClient` | **never** — it appends a translate-to-English instruction to every system message, and is invisible to Langfuse and to `budget_for` |

**Why vendor rather than depend.** The LLM client has to be ours regardless —
for the language rule, for the trace, and for the budget. Once it is, what
remains is prompt text and one module with no `async` and no imports outside the
standard library. A dependency would add `neo4j`, `openai` and `posthog` to the
image for code never called.

### 2.2 What is refused, and one refusal is not optional

`resolve_edge_contradictions` invalidates only when **both** timestamps are
non-`NULL` **and** strictly ordered; a tie or a missing timestamp falls through,
and the function contains **zero** `logger`/`warn`/`count`/`metric` — an ERROR
reading as a PASS, which is R-27's own shape.

**It cannot be refused by not calling it.** Verified at
`utils/maintenance/edge_operations.py:842`: it is invoked **unconditionally from
inside `resolve_extracted_edge`**, and those are the only two references to it in
the package. Worse, it is not pure — it writes `invalid_at` and `expired_at`
**onto the candidate rows the caller passed in**, so discarding its return value
does not discard its effect. The raw pre-filter candidate list is a local and is
never returned.

**So `resolve_extracted_edge` is refused whole.** An earlier draft of this node
claimed the function was "never called" and that pass 2 could take the raw
contradiction list — both false, and both found by external review after this
bundle had already asserted them.

### 2.3 D31 — the episode is a conversational chunk

A single episode is passed through **verbatim** (`utils/text_utils.py:69`);
`[Episode N]` headers appear only with a list of two or more, and a list is still
**one** LLM call, not N. Those headers carry a full ISO timestamp each:

| batch | plain | with headers | overhead |
|---|---|---|---|
| 100 messages | 3,600 chars | 9,588 | **+166%** |
| 400 messages | 14,400 chars | 38,688 | **+169%** |

Per-message episodes nearly triple the transcript and **fragment one claim across
three anchors** — a correction stated over three consecutive messages has no
single episode containing it. One episode for the whole batch loses the message
link §7.2 needs. **A conversational chunk** is narrow enough that its
`message_ids` resolve to one or two senders, at roughly a tenth of the header
cost. `exchange_observation` already models the shape; segmentation on a quiet
gap is the fallback that avoids depending on the language tier having run.

### 2.4 The batch renders `body` alone, and the sink enforces what the prompt cannot

[21](21-substrate-contract.md) §7.1 keeps the speaker out of the prompt. A probe
rendered `speaker: body` **with the roster supplied and a `Person` type
declared** and returned **five person-claims out of six edges**. §7.1's
prediction, measured.

Attribution therefore comes from episode **position**, not from a name in the
text, and §7.2's code-side join is preserved. And the consequence the prediction
could not state: the roster grant makes the boundary *checkable*
([19](19-between-tiers.md) §3) and this measurement shows **checkable is not
enforced** — a claim whose subject resolves to a roster `person_id` is **refused
at the sink with a counter**, never silently dropped
([17](17-failure-classes.md) §7's rule: refuse loudly, never accept and forget).

**Media.** Bytes reach a model for stickers and voice notes only; that policy is
unchanged. A voice note carries its STT transcript inline. An image carries
`[image a3f2c1]` and nothing behind it — and the handle reads exactly like a
named thing, the same shape as nine junk entities named `'1'`…`'9'` that a probe
produced from an `id:`-prefixed render. **Placeholders are reduced to a bare
label before extraction**, which also keeps a custody handle out of an external
model's input.

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

### 4.1 The vendored matcher, and the candidate query that feeds it

Promotion decides which *literals* have earned entity-hood. Resolving a **name**
to an existing entity is the separate half, and it is the vendored one:
deterministic MinHash/LSH/Jaccard behind an entropy gate, **no model, no IO** —
the module has no `async` and imports only the standard library.

```
big walk       -> Big Walk        resolved
house  house   -> House House     resolved
HKO            -> ESCALATE
grant        -> ESCALATE
```

Two honesty notes, both from external review:

- **The two resolved rows are exact-branch hits** — `lower()` plus whitespace
  collapse — which never reach the fuzzy path. `_FUZZY_JACCARD_THRESHOLD` is
  **0.9**, so `Big Walk`/`Big Walks` (0.833) and `HK Observatory`/`Hong Kong
  Observatory` (0.500) both miss. The deterministic layer's real yield on this
  corpus is whitespace-insensitive matching for ASCII multi-token names.
- **ESCALATE is not REFUSE.** Upstream hands unresolved names to a dedup prompt.
  Treating escalation as [21](21-substrate-contract.md) §5's default-to-split is
  *our* choice; adopting the matcher does not implement it.

**Which is why the vendored prompt matters more than the vendored code.** Its
worked examples are this room's hard cases and are corpus-independent:
`NYC`/`New York City` (abbreviation), `Java` the language versus the island (same
name, distinct things), `Marco's car`/`Marco's vehicle` (synonym, same
possessor). `HKO`/`Hong Kong Observatory` is the first of those — and the
candidate query below scores it **0.043**.

**The candidate query is ours**: exact on the normalised name and the alias
array, then `pg_trgm`, then `pgvector`. Three channels because each covers a
different failure — measured on `nora-db-1`, threshold 0.3:

| | similarity | |
|---|---|---|
| `Big Walk` / `big walk` | 1.000 | ✓ |
| `大浪灣` / `大浪湾` | 0.333 | ✓ — trigram catches what the matcher cannot |
| `打風` / `打风` | 0.200 | ✗ two-character words make too few trigrams |
| `香港天文台` / `天文台` | 0.250 | ✗ |
| `Hong Kong Observatory` / `HKO` | 0.043 | ✗ — the vector channel exists for this row |
| `打風` / `大浪灣` | 0.000 | ✗ correctly |

**The matcher's fuzzy path is ASCII-only** — non-ASCII normalises to the empty
string, and `_jaccard_similarity(∅, ∅)` returns **1.0**, so the entropy gate
running *before* the fuzzy comparison is load-bearing rather than incidental.
Pure-CJK names therefore degrade to exact-match and never false-merge. **A mixed
name does not get that protection**: `大浪灣 Beach Trip` and `西貢 Beach Trip`
both normalise to `beach trip` and merge — and in a code-switching room that is
the expected name shape, so the sink applies §5's split rule over the matcher's
verdict rather than trusting it.

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
| **fan-out** | **8 edges per node per hop** | Bounds the worst case at 1 + 8 + 64 rows before ranking. Ordered by **the child edge's own fused score**, computed before expansion, ties broken deterministically on `(predicate, object)`. An earlier draft ordered by the *parent's* score — every edge of one parent shares it, so the sort tied and the engine returned an arbitrary eight ([30](30-review-response.md) §5) |
| **dense-node guard** | **do not expand along a PREDICATE whose fan-out exceeds 30** | The super-node problem. A hub — a person, a recurring venue — connects to everything, so expanding through it returns the workspace. Related prior art calls a node dense at 50+ and warns the condition is not reversible; 30 is the conservative form at this scale |
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

2026-08-27 — **§2.1–2.4 and §4.1 added.** The pipeline is **ours**; exactly two
files are vendored (the dedup prompt and the pure matcher, 521 lines). The line
is drawn by §6 of [21](21-substrate-contract.md) — the object is always a
literal and their edge has no literal form — not by preference. §2.2 records that
`resolve_edge_contradictions` **is** called, from inside the function an earlier
draft proposed adopting, and mutates caller rows: a claim this bundle asserted
before measuring, and which external review refuted. §8 is untouched.

2026-08-23 — written to close the pipeline end to end after the operator asked
how extraction, ingestion and retrieval fit together and how multi-hop works,
and required that the bundle decide rather than defer. It **corrects
[23-retrieval.md](23-retrieval.md) §7**, which had argued for a plain vector
column: that conflated *no ANN index needed* with *no extension needed*, and
pgvector is adopted on `pgvector/pgvector:pg18`.
