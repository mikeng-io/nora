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

> **DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") has been deprecated and its code deleted.**
> This document is retained as a record of the reasoning, not as a description of the system. **Nothing here describes code that exists.**
> The database tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in §0 below. See issue #115.

# The Knowledge Tier

## 0. DEPRECATED 2026-09-07 — why, and on what evidence

**The knowledge tier has been deprecated and its code deleted.** Everything below
§0 is the reasoning that built it, kept as a record. It no longer describes
anything that runs. The `fact`, `fact_candidate`, `fact_entity`, `fact_premise`,
`fact_asserter`, `rendered_claim` and `embedding` tables are **not** dropped by
that change — the code goes, the schema
stays, and dropping them is a separate explicit decision.

All figures below were measured against the live database and the live traces on
**2026-09-06/07**, after the tier had been enabled for eleven days.

**1. It built the one warrant something else already does better.** Every fact
the tier ever wrote carried `warrant_kind = 'source'` — *"a page said so"* — five
facts in eleven days. A runtime `web_search` answers that question better,
fresher, and with more context than the stored row keeps: `Windrose has_chinese
true` knows *less* than one live search, because the row has dropped the context,
the date and the qualification. It is a cache of a cheaper, better call, and it
competes with `peer_card`/`representation` for the same per-turn context budget.

**2. The warrant it did not build is the one that was already running.** §3's own
table assigns `observation` to *"memory conclusions"* — i.e. Honcho, which holds
**8,301** of them against this tier's **5**. `stated` and `premises` never got a
writer at all.

**3. Its declared purpose is not expressible in what it wrote.**
`docs/superpowers/specs/2026-08-20-fact-tier-design.md` §5 ranks contradicting
candidates first, *"because this is the tier's purpose"*. Live, **zero
contradictions are expressible**: the extracted predicates carry their own object
— `has_chinese = true`, `has_puzzle`, `has_similar_price_to`. A predicate that
contains its answer can never be contradicted by a second value.

**4. Nothing read it.** `get_knowledge` was called **3** times, all
`nothing_found`. The `<facts>` block reached a turn **twice**, both times the
same single row.

**5. And the deepest reason — the apparatus is inert by CONSTRUCTION, not by
bug.** A knowledge graph encodes a **social contract**: somebody is answerable
for each claim, being wrong has a cost, somebody will audit it. A friends' group
has none of that. So the warrant/fidelity/dispute/supersession machinery this
bundle spent six nodes designing has nothing to bite on — measured on the live
table, `valid_to`, `disputed_at`, `superseded_at` and any non-`tentative`
fidelity are populated **0 times out of 5**.

The operator's own F/S/C model (2026-08-15 — **fidelity**, **salience**,
**currency**) had the right axes. What shipped built `fidelity` — the
accountability axis, the one a friends' group cannot supply — never built
`salience` at all, and wrote `currency` (`valid_to`) zero times. And the
mechanism a friends' group actually needs is **decay** — things stop mattering —
not **supersession**, which assumes an authority formally retracts.

**Note on issue #115.** The issue this decision is filed under proposed
*repositioning* the tier onto `stated` (group commitments) and says explicitly
*"this is not a teardown"*. The operator decided otherwise on 2026-09-07: the
code is deleted. §5 above is the argument that closed it — a `stated` writer
would inherit the same missing social contract, and the supersession machinery it
would need is the machinery measured at zero writes.

The issue also records that the machinery was *"verified working"* on 2026-09-07
— attribution, supersession, entity promotion, the negative cache. That is not in
tension with §5: it was verified at the code level, and separately measured at
**zero live writes**. Working machinery with nothing to work on is exactly the
finding.


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

## 6.1 Why no library supplies this — the problem is not recall

Written 2026-08-27, after a runtime investigation of `graphiti-core` 0.29.3 that
ran for a session and reversed itself four times before this became visible. It
is recorded here rather than in [25](25-knowledge-pipeline.md) because it is
about **what this tier is**, not about how a pass runs.

**Graphiti is agent memory for one user talking to one assistant.** Every design
choice follows from that: an episode is one interaction, its scoping key is one
tenant, the user is the authoritative narrator, and the job is **recall** —
surface the right memory at the right moment.

**This tier is a participant in a group that argues.** The dominant epistemic act
is contestation — *"no, big walk is a video game"* — and the bundle's own
acceptance test is a correction loop ([30](30-review-response.md) §2). The job is
**adjudication**.

The investigation found five defects, measured at runtime. They are not five
defects. **They are one design centre showing through in five places**, and every
one sits exactly where recall and adjudication diverge:

| measured | why it is that way |
|---|---|
| the contradiction resolver invalidates only when both timestamps are non-`NULL` and strictly ordered — a tie falls through, with **zero** `logger`/`count`/`metric` | in single-user memory, contradictions are rare and time-ordered. In a room they are the *point*, and they are not time-ordered: someone corrects a claim from three weeks ago |
| an edge connects two entity uuids; **a literal object has no edge form**, and one is dropped with a warning and no counter | recall wants *who works where*. Adjudication wants *does it support 4 players* — and §6 of [21](21-substrate-contract.md) makes the literal the **invariant** |
| the LLM layer appends *"Only output non-English text when the user has written full sentences in that language. Otherwise, output English"* to every system message | the target user is English-speaking. The room's own words are noise for recall and are **the entire asset** for the language tier |
| the entity prompt filters on *"Could this have its own Wikipedia article or database entry?"* | recall wants canonical entities. This room's value is `big walk`, `打風`, `食飯團` — exactly the non-canonical ones that filter rejects |
| **no warrant vocabulary, no three-state verdict, no asserter** | recall does not need to know *who* said it. Adjudication cannot function without it — a friend, a page, and a prank are three different kinds of knowing |

That last row is the one that settles it. Those are not implemented differently
elsewhere; **they are absent**, because no library is built for *a bot in a
friend group that must be correctable and must not repeat a prank as a fact*.

**Decomposed by value rather than by feature list:**

| | transfers | why |
|---|---|---|
| entity resolution | **~100%** | *is `NYC` the same as `New York City`* is corpus-independent. Genuinely solves our problem |
| extraction craft | **~80%, needs retuning** | the rules transfer; the corpus assumptions do not |
| temporal invalidation | **~0%, actively wrong** | their supersession is time-ordered; ours is research-adjudicated |
| graph queries, saga, communities | **0%** | relational by decision ([25](25-knowledge-pipeline.md) §8) |
| warrants, verdicts, asserters, the correction loop | **not present to transfer** | this is the tier's actual product |

> **80% of that library is roughly 30% of this tier. We can buy the reading. We
> cannot buy the judging.**

**And the real dependency was never the library.** Their prompts are good because
they were iterated against a corpus with graded evals; the craft in them is
residue from failures somebody measured. Writing our own without that apparatus
is writing blind — **a prompt tuned to the wrong corpus with an eval loop beats a
prompt tuned to the right corpus without one, because the second has no way to
know it is wrong.** That reorders the build ([00](00-overview.md) §7): the eval
corpus comes first, and it is the one asset here that cannot be copied from
anyone.

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

2026-08-27 — §6.1 added: **no library solves this tier's problem**, because
Graphiti solves recall for one user and this tier adjudicates in a group. Five
runtime-measured defects reduce to one design centre. The consequence is a
sequencing change, not a store change — §4's argument is untouched.

2026-08-24 — D11, the reframe. Its four-problems-one-column argument is discharged by [21](21-substrate-contract.md); its retrieval half by [23](23-retrieval.md) and [25](25-knowledge-pipeline.md).
