---
type: contract
title: "Retrieval — Two Surfaces, Three Channels, No Cross-Encoder"
description: "A tier whose items persist must be searched. What runs on every turn is cheap and deterministic; hybrid retrieval runs when Nora reaches for it. bge-m3 earns its place on cross-lingual recall, not on semantics in the abstract."
tags: [nora, knowledge-tier, retrieval, embeddings, bm25, rerank, bge-m3]
timestamp: '2026-08-23'
edges:
  - {dst: 20-knowledge-tier, rel: realizes, provenance: asserted}
  - {dst: 21-substrate-contract, rel: depends_on, provenance: asserted}
  - {dst: 16-open-items, rel: corrects, provenance: asserted}
---

# Retrieval

## 1. Two surfaces, and conflating them is the trap

[20-knowledge-tier.md](20-knowledge-tier.md) §6 derives the rule — *a tier whose
items are designed to close can be fully injected; a tier whose items are
designed to persist must be searched* — and says Nora needs **two** things at the
read end. This node says what they are.

| | when | cost | what it carries |
|---|---|---|---|
| **the block** | every turn | no model, no embedding | the store's **extent**, plus facts anchored to entities named *in this turn* |
| **the verb** | when she reaches for it | three channels + fusion | whatever answers the question she actually has |

Everything below follows from keeping these apart. The failure mode is putting
the verb's machinery on the block's schedule.

## 2. Hybrid retrieval must NOT run on every turn

Three reasons, in increasing order of how badly they bite.

**There is no query.** The "query" for a turn is the whole inbound message.
Lexical search over *"lol ok see u there"* retrieves noise; dense search over it
retrieves mood. Retrieval needs a question, and a turn is not one — it is an
utterance that may or may not contain one.

**It is the polling anti-pattern, moved onto the reply path.**
[06-trigger.md](06-trigger.md) §3 already names this shape: *a pass that runs
every five minutes is not dreaming, it is polling.* Running two candidate
generators and a fusion over every inbound message — most of which are
chatter — is the same mistake, except here it also costs latency in front of the
group, which the background version did not.

**It spends the budget on the turns that need it least.** Ordinary chatter is
the majority of messages and the minority of questions. A per-turn retrieval
pays most where it returns least, which is the precise inverse of what the
trigger seam concluded for the write side.

## 3. What DOES run every turn

**An extent line, and a turn-anchored neighbourhood.**

The extent line answers *how much do I know* — a count of standing edges and
distinct entities in the workspace. This is the direct fix for the defect the
recency slice had: a slice tells Nora *"we have eight facts"* and leaves her
blind to the rest; **an extent tells her the size of what she has not been shown,
which is what makes reaching for the verb a reasonable act rather than a guess.**

The neighbourhood answers *what do I know about what is being discussed*: resolve
the entities named in this turn, then read their 1-hop edges — one lookup against
`(workspace, subject_id)`, the index that already exists, partial on standing
rows.

**No model, no embedding, no fusion.** This is the focal-node signal
[20-knowledge-tier.md](20-knowledge-tier.md) §6 identified as the useful one,
with the focal node being simply *what the room just mentioned* — and therefore
with the candidate-generation stage skipped entirely.

It is bounded by the turn rather than by a window, which is why it does not
reproduce the slice's defect at a different size.

## 4. The verb: three channels, fused, then ranked by distance

| channel | mechanism | why it is here |
|---|---|---|
| **lexical** | Postgres full-text search, `pg_trgm` for fuzzy | free, already installed, exact on the words the room used |
| **dense** | bge-m3 | the only channel that can cross languages |
| **structural** | 1-hop and 2-hop expansion from resolved entities | the only channel that uses the graph the substrate exists to build |

Fused by **reciprocal rank** — arithmetic, not a model — because three ranked
lists with incomparable scores cannot otherwise be combined. Then ranked by
**distance from the focal node**, also arithmetic.

## 5. No cross-encoder

"Reranker" names three different things and only one of them is a model:

- **fusion** (reciprocal rank) — necessary, arithmetic;
- **structural rank** (focal-node distance) — necessary, arithmetic;
- **a cross-encoder** that jointly re-scores query and document — **not needed
  here.**

**The reason is document length.** A cross-encoder's advantage is joint attention
across a passage. This store's documents are triples — `big walk │ made_by │
House House` is a handful of tokens. There is very little for joint attention to
exploit that lexical and dense recall did not already surface, and the cost is N
forward passes inside a call on the reply path.

**When it would earn its place:** reranking over `source_excerpt` — an actual
paragraph of a fetched page — rather than over the triple. That is a different
retrieval target against a different column, and it is worth revisiting when
facts carry real excerpts. It is not this.

## 6. bge-m3's argument is cross-lingual recall, not "semantics"

This is the part most easily got wrong, because "add embeddings" is the reflex
answer and the reflex reason is not the operative one here.

**Postgres full-text search cannot match `Big Walk` against `大步行` at all.** It
does not segment Chinese usefully under the `simple` configuration, so in a
mixed English/Cantonese room the lexical channel is structurally blind to a large
share of the corpus — not weak on it, blind.

That gap is the dense channel's job, and it is why the model choice is
**bge-m3** specifically rather than an embedder generally.

It also explains the retracted measurement in
[16-open-items.md](16-open-items.md) §4.1: an English-centric embedder scoring
`Big Walk`/`大步行` *worse* than `Big Walk`/`a big walk` is the expected result
for such a model, not a finding about embeddings. The instrument was never
recorded, so the table cannot distinguish those two readings.

**What must be measured before the dense channel is load-bearing**, stated so it
is not skipped again: re-run those pairs with **the model named in the result**,
and state what a passing reading looks like first. The claim to test is narrow —
*does bge-m3 rank a true cross-lingual alias above a same-language literal
misreading?* If it does not, the dense channel is recall-only at best and the
lexical channel carries the tier.

## 7. Vectors do not need pgvector

Verified 2026-08-23: `vector` is **absent** from `pg_available_extensions` on the
deployed `postgres:18-alpine`; `pg_trgm` is installed and full-text search works.

**This is not a blocker, because pgvector is an index optimisation.** At this
tier's expected size a brute-force scan over an ordinary column beats an index
probe plus its overhead. Store the vectors in a plain column and adopt pgvector
if and when the corpus makes an index pay — which converts a deployment change
into a deferred one.

[16-open-items.md](16-open-items.md) §4.1 cited the same fact as a reason to
refuse the capability outright. It is a reason to defer the *index*, not the
*channel*.

## 8. Retrieval and resolution are different questions

§4.1 refused embeddings once, for both, in one paragraph. They separate cleanly:

- **Resolution** — *are these two names the same thing?* Stays **exact and
  fail-closed** ([21-substrate-contract.md](21-substrate-contract.md) §5), never
  similarity-scored, defaulting to split. A wrong merge corrupts a node.
- **Retrieval** — *which facts bear on this question?* Ranked, plural, and
  tolerant. A wrong result is one irrelevant row in a list.

The asymmetry is the whole point: **resolution writes and retrieval reads.** A
ranking mistake is recoverable by the reader; a merge mistake is not recoverable
at all until §7's provenance exists.

## 9. What this node does not decide

- **Where bge-m3 runs.** Nora has **no embedding call path of her own** — every
  semantic operation today goes through Honcho, against Honcho's corpus and
  Honcho's unrecorded model. A dense channel over *facts* is a new dependency —
  a local embedder or a route to one — not a config line. This is the one real
  cost in this node.
- **The verb's exact signature** — arity, filters, result shape. It is a tool on
  the registry seam ([08-state.md](08-state.md) §10), and its shape follows the
  read-tool pattern rather than being invented here.
- **Thresholds and `top_k`.** Every number waits on §6's measurement, for the
  reason [09-measurement.md](09-measurement.md) §6 gives: sizing before the
  reading is choosing values for knobs not known to be connected.

## Status / Checkpoint

2026-08-23 — written after the operator chose bge-m3 + lexical and asked whether
a reranker is needed. It **retracts** [16-open-items.md](16-open-items.md) §4.1's
refusal of a semantic index for this tier: the measurement's instrument was never
recorded, the Honcho leg is a category error against ADR-0001, the pgvector leg
is an index concern rather than a capability one, and the corpus-size leg was
sized against a corpus the tier is specified not to remain at.
