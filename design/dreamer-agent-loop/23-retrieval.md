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

**And the cheap channel runs first, with a short-circuit.** The prior art skips
its entire expensive path when the lexical top hit is both strong in absolute
terms *and* clearly ahead of the runner-up. That shape is worth copying exactly:
an exact-name hit on a canonical entity is the common case in this store, and
paying for a dense pass to confirm what an exact match already settled is the
per-turn mistake of §2 wearing a different hat.

The two thresholds are **ours to derive** (§10), not theirs to inherit.

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

**The obvious counter-example confirms the rule.** The prior-art tool examined
for this node *does* ship a cross-encoder reranker — and its unit is a ~900-token
chunk. It also reranks **one selected chunk per candidate document, never full
bodies**, with the comment: *"Reranking full bodies is O(tokens) — the critical
perf lesson that motivated this refactor."* Reranker cost scales with document
length, and their whole optimisation is to shrink what gets reranked. Our
documents are already six tokens. The lesson transfers as **do not**, not as
**do**.

## 6. bge-m3's argument is cross-lingual recall, not "semantics"

This is the part most easily got wrong, because "add embeddings" is the reflex
answer and the reflex reason is not the operative one here.

**The lexical channel has two Chinese-shaped gaps, and only one is the dense
channel's problem.** An earlier draft of this node conflated them.

| gap | example | whose job |
|---|---|---|
| a Chinese query does not match Chinese text | `大步行` → `大步行` | **the lexical channel's**, and it is fixable |
| a query does not match its translation | `Big Walk` → `大步行` | **the dense channel's**, and no lexical scheme can do it |

The first is a tokenisation failure: Postgres does not segment Chinese usefully
under the `simple` configuration, so a Chinese term produces no useful tokens and
the row is unfindable by its own words. **That is not a reason to reach for
embeddings** — it is a reason to fix the tokenisation, and §6.1 says how.

The second is genuine cross-lingual matching, which **no tokenizer at any
granularity can deliver**, and it is the whole argument for a dense channel here.
It is also why the model choice is **bge-m3** specifically rather than an embedder
generally: a strong multilingual embedder is the only thing that closes it.

### 6.1 The tokenisation fix, from prior art that tests it

> **SUPERSEDED by [24-embedding-seam.md](24-embedding-seam.md) §2** (2026-08-23).
> The technique below is a workaround for an engine with no trigram operator.
> Postgres has one, and `word_similarity` was measured solving the same problem
> in a single expression — which the note tier had already concluded and shipped.
> Kept because the *problem* it describes is real and the comparison is what
> justifies the choice.

A production markdown-search tool solves the first gap **without touching the
tokenizer**, in two coordinated places:

- **at index time**, every run of CJK characters is rewritten with each character
  spaced into its own token, on the stated grounds that the stock tokenizer *"does
  not segment CJK text into searchable words"*;
- **at query time**, a CJK term becomes a **quoted phrase** over those unigrams,
  requiring adjacency — while Latin terms keep prefix matching.

So `大步行` indexes as three tokens and is queried as the phrase `"大 步 行"`.
Effectively exact-substring matching riding on the stock engine. It is covered by
a test that seeds Chinese documents and asserts the right one comes back, paired
with a regression guard asserting English ranking is unchanged.

**The cost, stated because it is real:** with every character its own term, IDF
over CJK goes near-degenerate — adjacency carries the ranking, not term weighting.
For six-token triples that is arguably what we want; a bigram scheme buys better
IDF at roughly twice the index size.

**Enabling it is a reindex**, not a config flip: existing rows were tokenised the
old way, so the change requires rebuilding the index. At zero rows that is free
today, which is the same argument as everywhere else in this bundle.

It also explains the retracted measurement in
[16-open-items.md](16-open-items.md) §4.1: an English-centric embedder scoring
`Big Walk`/`大步行` *worse* than `Big Walk`/`a big walk` is the expected result
for such a model, not a finding about embeddings. The instrument was never
recorded, so the table cannot distinguish those two readings.

### 6.2 Measured, 2026-08-23 — and the two questions come out opposite ways

Run against `bge-m3` on the Ollama instance already deployed on this host.

**Resolution — bare name against bare name. FAILS.**
[16-open-items.md](16-open-items.md) §4.1's table reproduces to four decimal
places on this model. A cross-lingual alias sits *further* than a same-language
literal misreading. **The dense channel must never decide whether two names are
the same thing**, which is [21-substrate-contract.md](21-substrate-contract.md)
§5's rule, now measured with the instrument named.

**Retrieval — query against triple. WORKS, including from Cantonese.**

| query | top result | distance |
|---|---|---|
| `大步行` | `big walk │ made_by │ House House` | 0.3404 |
| `邊個做嘅大步行` | `big walk │ made_by │ House House` | 0.3310 |
| `who made big walk` | `big walk │ made_by │ House House` | 0.2814 |
| `big walk` (baseline) | `big walk │ made_by │ House House` | 0.2412 |

Every query put the three `big walk` triples above every unrelated one, and both
natural-language questions ranked `made_by` above `released_in` — the predicate
the question actually asked for.

**Why the same model succeeds at one and fails at the other:** a triple carries
its own context. `big walk │ made_by │ House House` is a sentence-shaped thing;
`Big Walk` alone is two tokens where surface-word overlap with `a big walk`
dominates everything else.

**This also closes the context-prefix question** ([§10](#10-what-to-copy-from-the-prior-art-and-what-not-to)):
the prior art prefixes a chunk with its document title to give the embedder
context. **A triple is already in that shape** — embedding the whole row rather
than the object string is the same technique, arriving for free.

So the split this node asserted in §8 on principle is now the measured result:
**exact and fail-closed for resolution, dense for retrieval.**

## 7. Vectors do not need pgvector

Verified 2026-08-23: `vector` is **absent** from `pg_available_extensions` on the
deployed `postgres:18-alpine`; `pg_trgm` is installed and full-text search works.

**This is not a blocker, because pgvector is an index optimisation.** At this
tier's expected size a brute-force scan over an ordinary column beats an index
probe plus its overhead. Store the vectors in a plain column and adopt pgvector
if and when the corpus makes an index pay — which converts a deployment change
into a deferred one.

**The prior art settles this more firmly than the argument does.** The vector
extension that tool depends on is **brute-force only at every scale** — its own
maintainers' ANN tracking issue is still open — and the tool additionally
*prefers* an explicit exact scan below a **20,000-vector** threshold, with the
threshold existing to stop a small collection being starved by a global top-k,
not to avoid an index. A widely-used production tool does linear vector search
throughout, and starts worrying three orders of magnitude above where this tier
sits.

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

- **Which embedding runtime.** Nora has **no embedding call path of her own**;
  every semantic operation today goes through Honcho, against Honcho's corpus
  and Honcho's unrecorded model — and §8's boundary means facts cannot go
  there. So a dense channel is a **new in-process dependency**, and §11 records
  what the prior art establishes about its shape and its one real hazard here.
- **The verb's exact signature** — arity, filters, result shape. It is a tool on
  the registry seam ([08-state.md](08-state.md) §10), and its shape follows the
  read-tool pattern rather than being invented here.
- **Thresholds and `top_k`.** Every number waits on §6's measurement, for the
  reason [09-measurement.md](09-measurement.md) §6 gives: sizing before the
  reading is choosing values for knobs not known to be connected.

## 10. What to copy from the prior art, and what not to

A markdown-search tool with the same three-channel shape was studied for this
node. Four mechanisms transfer; its numbers do not.

**Copy the shape.** Reciprocal-rank fusion with a fixed constant; list weights
assigned by **provenance** rather than insertion order, so a derived list cannot
inherit the original query's boost; retrieval trusted at the head of the ranking
and the reranker at the tail; the cheap-channel short-circuit in §4.

**Copy the staleness fingerprint — this one is a gift.** It hashes the model
identity and every parameter that affects an embedding into a short value stored
**on each vector row**. One predicate — *fingerprint is not the current one* —
then names exactly which rows are stale. Change the model, change a prompt
prefix, change any input to the embedding, and the affected rows identify
themselves.

Without it, swapping an embedding model silently leaves a store where some
vectors are comparable and some are not, with **no way to tell which** — a
corpus that returns confident nonsense and cannot be audited. That is the
`unavailable`-as-`nothing_found` failure at the storage layer, and it costs a
column. It is also exactly the provenance discipline the spine already
demands.

**Consider the context prefix.** That tool embeds a chunk together with its
title rather than alone. The analogue is embedding a triple with its subject and
predicate rather than the bare object string. Worth an A/B; not obviously a win
at six tokens.

**Do NOT copy its constants.** Its fusion weights, rank bonuses and blend
thresholds are unmeasured, and the one test that could detect a regression in
them is **skipped in CI** by an environment guard while the lexical floors run.
One is incoherent on inspection: a top-rank bonus larger than any single list's
entire maximum fusion contribution, so the bonus dominates the ranking it was
meant to nudge. Its published speed figures trace to a house style instructing
release authors to *"include numbers"*, and at least one is arithmetic over two
config constants rather than a measurement.

Derive every constant against our own triples.

## 11. The embedding runtime, and the hazard that is ours alone

> **ANSWERED 2026-08-23: `bge-m3` is already running on this host, on Ollama, as
> a separate service.** 1.16 GB, 1024-dimensional, reachable over HTTP. The
> in-process question below is therefore moot for this deployment — kept because
> it names the hazard that the chosen arrangement *avoids*, and a future move
> in-process would reintroduce it.

The prior art runs its embedder **locally and in-process** — GGUF weights loaded
into the running process, no remote API, with lazy loading, an idle-unload timer,
bounded contexts, and a memory budget. It never considers a hosted embedding
service, so it offers no evidence either way on that choice.

**Two differences make its arrangement not directly portable.** It is a CLI that
starts, answers and exits, so its engineering problem is amortising a cold load.
Nora is a **long-lived async service**, which makes loading easier — once, at
startup — and introduces the hazard that tool never faces:

> **In-process embedding inference will block the event loop** unless it is run
> in a thread or executor.

A synchronous embed on the reply path stalls every other conversation in flight.
This is the same class as the hung-Dreamer finding already in this bundle, where
a blocked call was invisible because nothing was waiting on it — except here
someone is waiting, in front of the group.

**The deployed arrangement sidesteps this entirely.** An HTTP call to a separate
service is naturally awaitable, carries no resident weights in Nora's process,
and can be given a timeout — which the in-process form cannot. It also inherits
the failure mode Nora already knows how to express: a service that is down is
`unavailable`, never `nothing_found`.

## 12. Measuring this, so a broken channel is not a quiet one

That tool's own benchmark harness **returns all-zero scores with a real latency
number when a backend throws**. A channel that is broken is therefore
indistinguishable from a channel that correctly found nothing — in the
measurement layer of the reference implementation.

That is precisely this repo's R-27 failure, and it is the one thing from the
study to treat as a warning rather than a pattern:

> In any retrieval eval built here, an exception is an **ERROR**. Never a
> zero-score PASS, and never folded into "found nothing".

Two further traps from the same audit, both cheap to avoid: a metric named
*precision* that divides by the number of expected results rather than by `k`
(making it recall under a precision label), and loose suffix matching that counts
a near-miss as a hit. Name the metric for what it computes, and match on
identity.

## Status / Checkpoint

2026-08-23 — written after the operator chose bge-m3 + lexical and asked whether
a reranker is needed; §6, §10, §11 and §12 added after a source study of a
production three-channel search tool, which **corrected §6**: an earlier draft
treated "cannot match Chinese text" and "cannot match across languages" as one
gap. They are two, and only the second needs embeddings. It **retracts** [16-open-items.md](16-open-items.md) §4.1's
refusal of a semantic index for this tier: the measurement's instrument was never
recorded, the Honcho leg is a category error against ADR-0001, the pgvector leg
is an index concern rather than a capability one, and the corpus-size leg was
sized against a corpus the tier is specified not to remain at.
