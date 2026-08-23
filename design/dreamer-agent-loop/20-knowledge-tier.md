---
type: decision
title: "The Knowledge Tier — The Schema Is Already a Graph; One Corner Ships"
description: "The fact schema models a bitemporal knowledge graph with four kinds of warrant. Its only writer emits string triples with one warrant and one fidelity. The name followed the implementation, and the name has been setting everyone's idea of the scope."
tags: [nora, knowledge-tier, fact-tier, entity, graph, warrant, naming]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 12-self-correction, rel: corrects, provenance: asserted}
  - {dst: 19-between-tiers, rel: extends, provenance: asserted}
---

# The Knowledge Tier

## 1. What the schema actually models

```
subject_id · predicate · object_entity_id | object_literal
warrant_kind : source | premises | observation | stated
fidelity     : tentative | proven | deprecated
valid_from / valid_to           ← event time
superseded_at / superseded_by   ← transaction time
```

An entity-to-entity edge, bitemporal, with a warrant vocabulary and an epistemic
status. The store-choice spec verified the correspondence to a mature graph
engine's own edge type — *"a verified correspondence rather than a hope"* — and
found its scoping key maps 1:1 onto `workspace`.

**That is a knowledge graph.** It is in the schema, on `master`, today.

## 2. What the only writer emits

In the store's own words:

> `propose` writes `subject_id = payload.subject` and
> `object_literal = payload.object` **verbatim**, never `object_entity_id` — a
> fact written by this store **always** has `warrant_kind = 'source'` … and
> **always** lands `fidelity = 'tentative'`.

| the schema offers | the writer emits |
|---|---|
| entity → entity edges | entity → **literal**, always |
| `subject_id` as an id | a **raw string** |
| four warrant kinds | **one** |
| three fidelity states | **one** |

**Exactly one corner is implemented**, and the tier was named after that corner.

## 3. The other three warrants are the other tiers

This is what makes the reframe precise rather than merely broader.

| warrant | means | which tier |
|---|---|---|
| `source` | a page said so | **facts** — the only one with a writer |
| `observation` | seen repeatedly, here | **lexemes**; memory conclusions |
| `stated` | somebody decided it | decisions, preferences |
| `premises` | follows from other edges | inference — the relation table exists, nothing walks it |

A lexeme **is** `(term, means_here, sense)` warranted by observation. A memory
conclusion **is** a claim about a person warranted by observation. Neither is a
different kind of thing; they are the same relation with a different warrant —
**and the vocabulary is already in the type.**

So Phase 3's lexeme migration stops being *"move a tier onto the runtime"* and
becomes *"give `observation` a writer."* Same work, coherent reason.

## 4. The escape hatch the store choice depends on was not installed

The relational-over-graph decision was made carefully, re-verified at source, and
rests on one claim:

> **What forecloses traversal later is not relational-vs-graph. It is
> triple-with-canonical-subject versus free-text statement.** Storing
> `(subject_id, predicate, object)` against a canonical entity table makes a
> later migration `INSERT … SELECT` — a query-layer swap.

**The tier shipped the free-text shape.** `subject_id` holds a raw string,
`object_entity_id` is never written, and the entity table holds zero rows.

> The decision was *"relational now, a graph stays a swap."* What shipped kept
> the relational half and dropped the property that made the swap possible.

That is not an argument for adopting a graph engine — the rejection's grounds are
specific, measured and still stand (a contradiction resolver that drops flagged
contradictions with no counter; a judge that scored 1/3 and 0/3 on this shape).
It is an argument that **deferral is only reversible if the thing that keeps it
reversible exists.**

## 5. One missing column, four problems

Canonical entity ids are simultaneously:

| | why it needs them |
|---|---|
| **retrieval / relevance** | ranking by distance from a focal node has nothing to compute over without a graph. **This is why the block is a recency slice** — not a lazy render, but the only orderable property left when there is no graph |
| **conflict detection** | two tiers cannot be joined on a thing they name differently ([19-between-tiers.md](19-between-tiers.md) §4) |
| **resolution** | the alias mechanism has nothing to hang aliases on |
| **the deferral being reversible** | `INSERT … SELECT` versus a re-extraction project |

These have been argued as four separate problems across this bundle. **They are
one column.**

## 6. Retrieval, which the store choice dismissed on size

The same section called hybrid retrieval *"inert at this tier's expected size."*
That was a size argument, and the size assumption has been challenged: **notes
are bounded by design** — each carries a closing condition, so a large notebook
is a curator defect — while **facts are unbounded by design**, each carrying a
warrant and no reason to close. A thousand-plus edges in one workspace is the
expected steady state, not a stretch.

So the derivable rule, and it is why the two tiers need different treatment:

> **A tier whose items are designed to close can be fully injected. A tier whose
> items are designed to persist must be searched.**

The retrieval shape that follows is prior art, and it does not require adopting
the engine it comes from: **plural candidate generation** (lexical, semantic,
structural) fused by rank, then **a reranker chosen per use case** — of which the
useful one here is **distance from a focal node**, the focal node being the room,
the speaker, or the subject in play. Every piece is expressible over relational
tables; what none of it is expressible without is the entity graph.

**What Nora needs at the read end is therefore two things, not one:** a block
that tells her the store's *extent* rather than a slice of its contents, and a
verb to query it ([19-between-tiers.md](19-between-tiers.md) §5). Neither is
possible while the graph is a set of strings.

## 7. The decision

**D11. This is the knowledge tier.** The schema already is one; the name
followed the implementation, and the name has been setting the scope everyone
reasons within — including this bundle, for most of its length.

**What changes**

- The entity table is the **shared knowledge substrate**, not one tier's. Its
  current name says otherwise while another tier already foreign-keys into it.
- **Canonical entity ids get written**, which is the precondition for §5's four
  problems and the only thing that keeps the store choice reversible.
- The remaining warrants **get writers**, one per existing tier, incrementally.

**What does not change**

- **The store choice.** Relational now, on grounds that were measured and hold.
- **Notes stay separate.** A note is a *question*, not a claim. It anchors on the
  same entities — the foreign key already assumes it — but it is not an edge.
- **Memory stays external.** Its schema is not ours. It becomes *joinable* by
  entity, not absorbed.

**The naming discipline, because this bundle is about drift.** The rename is a
design decision. The shipped identifiers still say `fact`, and every historical
citation in this bundle still refers to what those documents actually said. A
prose rename that runs ahead of the code produces exactly the divergence
[04-designed-vs-shipped.md](04-designed-vs-shipped.md) catalogues. Rename the
code, or carry both names honestly — never only the aspiration.

## 8. Timing

**Zero rows exist.** Writing canonical ids costs nothing today: no backfill, no
migration of live data, no re-extraction. At a thousand edges it is a cleaning
project against free-text subjects.

The design already committed to this column. It is cheaper to install now than
it will ever be again.

## Status / Checkpoint

2026-08-24 — D11, the reframe. Its four-problems-one-column argument is discharged by [21](21-substrate-contract.md); its retrieval half by [23](23-retrieval.md) and [25](25-knowledge-pipeline.md).
