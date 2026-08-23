---
type: contract
title: "The Shared Retrieval Seam — One Lexical Channel, One Embedder, Three Tiers"
description: "Three tiers need to find rows by words and by meaning. Built once: pg_trgm word_similarity for lexical (measured working on Cantonese AND English), one Embedder port, one vector table keyed by kind. What abstracts, what stays per-tier, and which tiers actually need it."
tags: [nora, retrieval, embeddings, pg_trgm, seam, note-tier, language-tier]
timestamp: '2026-08-23'
edges:
  - {dst: 23-retrieval, rel: extends, provenance: asserted}
  - {dst: 19-between-tiers, rel: depends_on, provenance: asserted}
---

# The Shared Retrieval Seam

## 1. Why this is one seam and not three

[23-retrieval.md](23-retrieval.md) specifies retrieval for the knowledge tier.
The note tier has the same need, and the language tier may. Building it three
times produces three lexical channels, three embedding call paths, and three
places that know which model was used — which is
[04-designed-vs-shipped.md](04-designed-vs-shipped.md)'s drift pattern with a
new subject.

The repo's own convention already forbids the shape: *a port never gains a second
implementation without a shared contract suite both pass.* This node says what
the port is.

## 2. The lexical channel is `pg_trgm`, and this supersedes 23 §6.1

**Measured on the live database, 2026-08-23:**

| expression | result |
|---|---|
| `word_similarity('大步行', '我今日去咗大步行')` | **0.5** |
| `word_similarity('大步行', '機器學習')` | **0** |
| `word_similarity('walk', 'we went for a big walk')` | **1** |
| default threshold | 0.3 |
| `to_tsvector('simple','大步行')` | **`'大步行':1`** — one token for the whole run |
| `similarity('大步行', '我今日去咗大步行')` | 0.18 — **below threshold** |

**One operator handles Cantonese and English, finds a term inside a sentence,
scores cleanly above the threshold, and returns a clean zero on an unrelated
Chinese string.** No tokenizer configuration, no language detection, no
per-language branch.

Note the two traps the measurement catches: full-text search under `simple`
collapses an entire CJK run into **a single token**, so it matches only the whole
run and never a term inside it — and plain `similarity` scores the true match at
**0.18, under the 0.3 default**, so the obvious operator silently returns
nothing. The working operator is `word_similarity`, which scores a needle
against the best-matching substring of the haystack.

### 2.1 What this supersedes, and why the correction is worth recording

[23-retrieval.md](23-retrieval.md) §6.1 recommends porting a technique from a
SQLite-based tool: space every CJK character into its own token at index time and
query CJK terms as a quoted phrase. **That is a workaround for an engine with no
trigram operator.** Postgres has one, and the measurement above shows it solving
the same problem in one expression with no reindex trick and no degenerate IDF.

**The note tier already decided this and shipped it.** Migration `0026` chose
`pg_trgm` with a GIN index over `tsvector`, and its recorded reason is the same
one measured here: *"the corpus is mixed English and Cantonese and
`to_tsvector('english', …)` does not segment Chinese at all."*

So the house pattern already existed, in a tier that reached the conclusion
first. 23 §6.1 should be read as *the problem is real and here is how a different
engine solves it*, not as a build instruction.

## 3. The four layers

| layer | what it owns | shared? |
|---|---|---|
| **lexical** | `pg_trgm` + `word_similarity`, GIN index per searchable column | **shared** — one expression, every tier |
| **embedder** | a port: text in, vectors out. Model identity, prefixes, batching, the executor offload, the fingerprint | **shared** — exactly one implementation |
| **vector store** | one table keyed `(workspace, kind, row_id, seq)` carrying vector + fingerprint; `kind` discriminates the tier | **shared** |
| **fusion** | reciprocal rank over whatever channels a tier supplies | **shared**, arithmetic |

**One embedder means one dimension**, which is what makes a single vector table
legitimate rather than a premature merge. A tier wanting a different model is a
decision that reopens this node, not a config line — and the fingerprint (§5) is
what makes such a change survivable.

## 4. What does NOT abstract

Three things are per-tier, and pretending otherwise is how a shared seam becomes
a shared constraint:

**Whether the tier needs search at all.** [20-knowledge-tier.md](20-knowledge-tier.md)
§6's rule decides it: *a tier whose items are designed to close can be fully
injected; a tier whose items are designed to persist must be searched.*

**Chunking.** Knowledge-tier rows are pre-chunked by construction — one triple is
one unit. Note bodies are long enough to need boundaries. Lexemes are trivially
short. There is no shared chunker, and inventing one would serve nobody.

**The structural channel.** 1-hop expansion needs a graph. Only the knowledge
tier has one. Notes anchor on entities and could borrow it later; the language
tier never will.

## 5. Which tiers actually need it — the honest answer

| tier | needs the seam? | for whom |
|---|---|---|
| **knowledge** | **yes** | Nora and the Dreamer both. Unbounded by design ([20](20-knowledge-tier.md) §6) |
| **note** | **yes, but not for Nora** | the note index is **uncapped and fully injected** on every tag, deliberately — *"a note missing from the index is invisible to her."* Nora needs no search. **The Dreamer does**: an adaptor gets no injected block, so without a verb it cannot see the notebook at all |
| **language** | **probably not** | six rendered lexemes, bounded by the room's own slang. Building search here is machinery for a corpus that fits in the prompt |

**The note tier's case is the interesting one and it is easy to get backwards.**
Search there is not a better way to show Nora her notes — it is the *only* way a
background adaptor can read them. That is a real, non-redundant need, and it is
why the seam is shared rather than knowledge-tier-private.

The language tier's answer is *no*, and saying so is the point: a shared seam
that every tier is obliged to adopt is worse than three bespoke ones.

## 6. The fingerprint is the seam's load-bearing discipline

Every vector row carries a hash of the model identity and every parameter that
affects the embedding. One predicate — *fingerprint is not the current one* —
names exactly which rows are stale, across every tier at once.

Without it, changing the model leaves a store where some vectors are comparable
and some are not, **with no way to tell which**: a corpus that returns confident
nonsense and cannot be audited. That is `unavailable` presenting as
`nothing_found` at the storage layer, and it costs one column.

It is also what makes §3's single shared table safe. Three tiers embedded at
different times against different model versions is exactly the drift this
column detects and nothing else would.

## 7. The cost, measured rather than assumed

The dense channel is an **in-process model on a host that is already full.**
Measured 2026-08-23: **27 GB total, ~6 GB available**, with the largest
residents being a crawler (2.7 GB), a speech service (2.7 GB), a model router,
an observability stack, and a graph database.

bge-m3 is materially larger than the small embedders such tools usually default
to. It fits in the current headroom, but it is **not free**, and this host has no
slack to discover that later.

Three consequences, none of them optional:

- **Offload the inference.** Nora is a long-lived async service; a synchronous
  embed on the reply path stalls every conversation in flight
  ([23](23-retrieval.md) §11).
- **Consider an idle-unload timer**, the pattern the prior art uses, if resident
  footprint proves tight.
- **The lexical channel costs nothing and works today.** It needs no model, no
  memory, and no new dependency — and §2 shows it already answers the Chinese
  question that motivated the dense channel's urgency. **Ship the lexical channel
  first, measure what it misses, and let that reading size the dense one.**

That last point is this node's practical recommendation: the two channels are
separable, and only one of them has a resource bill.

## 8. What this node does not decide

- **The embedder's runtime** — in-process bindings versus a small sidecar. The
  prior art is in-process and never considers an alternative
  ([23](23-retrieval.md) §11); this host's memory picture may argue for a
  sidecar that can be stopped. Undecided, and cheap to defer because §7 ships
  the lexical channel first.
- **Whether notes join the entity graph**, which would give the note tier a
  structural channel. It anchors on the same entities already
  ([21](21-substrate-contract.md) §2); nothing reads that yet.
- **Thresholds.** `word_similarity`'s 0.3 default was not chosen for this corpus.
  It is a knob with a measurement attached, not a constant to inherit —
  [23](23-retrieval.md) §10's rule applied to the channel that costs nothing.

## Status / Checkpoint

2026-08-23 — written after the operator asked what the universal embedding
architecture is and whether the note and language tiers reuse it. It **corrects
[23-retrieval.md](23-retrieval.md) §6.1**: the CJK technique recommended there is
a workaround for an engine without trigram support, and Postgres solves the same
problem in one operator — which the note tier had already concluded and shipped
in migration `0026`.
