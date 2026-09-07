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

> **DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") and the language tier have both been deprecated and their code deleted.**
> This document is retained as a record of the reasoning, not as a description of the system. **Nothing here describes code that exists.**
> The database tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> This node designed **one** seam for three tiers. Its only implementation was ever the knowledge tier's — `nora/language/fact_embedder.py`, `nora/language/knowledge_search.py` and the `embedding` table. The note tier, which survives, never adopted it; the language tier never ran. With both deprecated there is no consumer of the seam left in the repo.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.

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
| **language** | **not today** | six rendered lexemes, bounded by the room's own slang. But the *exchange* corpus has no closing condition, so this is a reading, not a verdict — see §5.1 |

**The note tier's case is the interesting one and it is easy to get backwards.**
Search there is not a better way to show Nora her notes — it is the *only* way a
background adaptor can read them. That is a real, non-redundant need, and it is
why the seam is shared rather than knowledge-tier-private.

The language tier's answer is *no today*, and saying so is the point: a shared
seam that every tier is obliged to adopt is worse than three bespoke ones.

### 5.1 Bounded BY DESIGN is not bounded IN PRACTICE

The rule in §4 keys on a design property, and a design property is a claim about
a mechanism that must actually exist.

**The note tier is the live counter-example.** It is bounded by design — every
note carries a closing condition — and **the curator that would close them does
not exist.** The only references to it in the codebase are docstrings describing
what it *will* do. `config.toml` says so plainly: *"No curator yet — nothing
merges, retires or promotes a note… The notebook only grows for now."*

So a tier the design calls bounded is, in the deployment, unbounded. The
instrument that comment names as the tell — `nora_note_index_entries` — had
**zero call sites** when this was written; it gained one on 2026-08-27
([27](27-note-dreamer.md) §6.1), labelled by workspace so two notebooks do not
overwrite each other's reading. **The growth is now counted; it is still
nobody's to bound until the curator exists**, which is what makes the threshold
below readable rather than hypothetical.

**Therefore adoption is a measured transition, not a design-time verdict:**

> A tier joins the seam when its **extent crosses what the prompt can carry** —
> read from a counter, not decided from a spec.

This is what the extent line in [23-retrieval.md](23-retrieval.md) §3 is
*actually for*. It reads like a courtesy to Nora — *here is how much I know* —
and it is simultaneously the instrument that detects the injectable→searchable
crossing for every tier at once. One number, two jobs, and the second is the one
that keeps this node from going stale.

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

**The bill is already paid.** `bge-m3` is **already running on this host**, on
Ollama, as a separate service — 1.16 GB, 1024-dimensional, reachable over HTTP,
and verified 2026-08-23 to embed Cantonese and English in one call.

That removes the concern an earlier draft of this node built its
recommendation on. The dense channel is **not** a new resident model competing
for the ~6 GB free on a host already carrying a crawler, a speech service, a
router, an observability stack and a graph database. It is an HTTP call to
something that is up.

Three consequences follow, and they are better than the in-process ones:

Three consequences, none of them optional:

- **The event-loop hazard does not arise.** An HTTP call is naturally awaitable
  and can carry a timeout, which an in-process call cannot
  ([23](23-retrieval.md) §11).
- **It fails in a shape this codebase already speaks.** A service that is down is
  `unavailable` — never `nothing_found`. An in-process model that OOMs is a
  process death.
- **It stays swappable.** The model lives behind a service boundary, so changing
  it is a pull and a re-embed keyed by §6's fingerprint, not a redeploy of Nora.

**The two channels remain separable and should still ship in order** — lexical
first, because §2 shows it already answers the Chinese question and it needs
nothing new at all. But the reason is now *sequencing*, not cost.

## 7.5 Designing for the tiers that do not exist yet

Everything above is sized against three tiers that exist. The seam's real test is
the fourth, and the fifth — Standing, a debrief, whatever follows. Four
properties decide whether adopting it later is a config change or a migration.

**`kind` is an open string, never an enum.** A closed set means tier five ships
with a schema change and a `CHECK` constraint edit before it can store its first
vector. The exchange tier's closed act set is right for *acts*, which are a
vocabulary; `kind` is a registry, and registries stay open.

**Dimension is per-`kind`, not global.** §3 says one embedder means one
dimension, and that is true *today* — but a future tier may want a different
model, and the seam should let it without a migration. **The plain column already
allowed this and a single `vector(N)` column would not**, since `vector(N)` fixes N at
DDL time. That is a second, better argument for the storage choice in
[23-retrieval.md](23-retrieval.md) §7. **Resolved there, not here:** with
pgvector adopted, per-kind dimension is expressed as **one column per
dimension**, routed by the seam's `kind` discriminator — so a new tier with a
different model adds a column rather than a type, and the "declaring three
things and writing no schema" bar in this section holds only for a tier reusing
an existing dimension. A tier bringing its own model brings a column.

**Adoption is declared by the tier, not enumerated by the seam.** The same shape
D2 gives triggers: a tier says *I am searchable, here are my text columns, here
is my key*, and the seam serves it. Nothing in the seam holds a list of tier
names — a list is what §5's table would become if it were code, and it would be
wrong within one release.

**The searchable projection is the tier's, not the seam's.** Each tier names
which of its columns are text worth matching. The seam never assumes a table
shape, which is what lets a tier with a completely different row join without the
seam learning anything about it.

Together these mean a new tier adopts retrieval by **declaring three things and
writing no schema**. That is the bar, and it is the reason to build the seam once
now rather than discover it on the fourth tier.

## 7.6 The model is bge-m3, and the bake-off is why

The operator asked whether bge-m3 — early 2024 — has been superseded. **On
published benchmarks, comprehensively. On this corpus, no.**

### 7.6.1 What the literature says

| model | Retrieval | BitextMining | Ollama size |
|---|---|---|---|
| bge-m3 | **54.6** — last in class | **79.1** — near-best | 1.2 GB |
| Qwen3-Embedding-0.6B | **64.7** (+10.1) | **72.2** (−6.9) | 639 MB |
| EmbeddingGemma-300m | 62.5 (+7.9) | **64.4** (−14.7) | 239 MB |
| harrier-oss-v1-270m | **66.4** (+11.8) | **81.5** (+2.4) | not on Ollama |

**BitextMining is sentence-level cross-lingual matching** — the closest published
proxy for *a Chinese string retrieving the English one that names the same
thing*, at roughly this store's document length. Most models that beat bge-m3 on
retrieval **do it by losing cross-lingual alignment**. That is a conflict in the
evidence, not a gap in it.

### 7.6.2 What this corpus says — measured, 2026-08-23

Eight queries against eleven triples, including Cantonese questions,
mixed-script (`Grant 婚禮喺邊度`), Cantonese slang, and **near-duplicate
triples differing only in the object** (`gourds_required | five` versus `six`).

| model | retrieval |
|---|---|
| **bge-m3** | **7 / 8** |
| qwen3-embedding:0.6b, raw | 2 / 8 |
| qwen3-embedding:0.6b, **with its documented instruction prefix** | **2 / 8** |

The instructed run matters: Qwen3 is instruction-tuned (+5.09 on instruction
retrieval) where bge-m3 is *negative* (−3.11), so running both raw would have
measured Qwen3 wrongly. It was re-run with `Instruct: {task}\nQuery: {q}` and
documents unprefixed, per its model card. **The result did not move.**

A model the literature puts **+10 ahead on retrieval finished 5 queries behind on
this data.** §7.5 predicted exactly this: the benchmarks measure neither
Cantonese, nor traditional script, nor six-token triples, and all three are what
this tier is made of.

**Decision: stay on bge-m3.** Not because it is modern — it is not — but because
the one axis it leads on is the one this corpus is built from, and the local
measurement agrees with that leg of the literature rather than the other.

### 7.6.3 The one failure, and it is not the model's

bge-m3's single miss was `打風` — Cantonese slang for *typhoon* — which returned
a `big walk` triple instead of `hong kong observatory | reports | typhoon signal
8`.

**Neither channel can close this.** Lexical cannot: `打風` shares no characters
with the English triple. Dense did not: the slang term is too far from the
formal English rendering.

This is **the alias problem, not the retrieval problem** — the same shape as
`Big Walk`/`大步行`, and it belongs to the entity registry
([21-substrate-contract.md](21-substrate-contract.md) §2), where a room's own
slang becomes an alias of a canonical entity. It is worth recording because it
is the clearest evidence in this bundle that **the entity graph is not an
optimisation of retrieval — it reaches cases retrieval structurally cannot.**

### 7.6.4 The honest limit of this reading

**n = 8.** This is a screening test, not a procurement decision. It is enough to
refuse a switch — a 7/8 versus 2/8 margin is not a close call — and **not**
enough to rank two models that finish near each other. A real comparison needs
100–200 gold pairs drawn from the room's actual traffic, which does not exist
until the tier does.

Two operational notes that survive whatever model is chosen:

- **Ollama has an open, unresolved ~5× throughput gap versus TEI** on this model
  family, and a history of `/api/embed` breaking across version bumps. **Pin the
  version and re-verify after any upgrade** — a silently changed embedding is
  §6's fingerprint case, arriving through the runtime rather than the config.
- **bge-m3 has no Matryoshka.** 1024 dimensions is fixed, so the per-kind
  dimension freedom §7.5 preserves is freedom for a *future* tier's model, not a
  knob on this one.

## 8. The last three, decided

- **The embedder's runtime — decided:** `bge-m3` on Ollama, out of process,
  reached **through a port with a fake**, per the convention that no port gains
  a second implementation without a shared contract suite. A direct HTTP call
  from the tier would make the contract suite need a live model.
- **Notes join the entity graph — decided: yes, by writing `note_entity`.** The
  junction table and its foreign key already exist and are empty because
  `note.anchor_entity_ids` is written verbatim while resolution has no owner.
  [21](21-substrate-contract.md) §5 now gives resolution an owner, so the note
  curator populates `note_entity` through the same sink that promotes fact
  entities. That is what gives the note tier a structural channel, and it costs
  one write in a pass that has to run anyway.
- **Thresholds — decided as defaults**, in [23](23-retrieval.md) §9's table:
  `word_similarity` at Postgres' own **0.3**, which §2's measurement shows
  clears the true Cantonese match (0.5) while an unrelated Chinese string scores
  0. A default with its reasoning is a decision; the first traffic window tunes
  it.

## Status / Checkpoint

2026-08-23 — written after the operator asked what the universal embedding
architecture is and whether the note and language tiers reuse it, then **revised
the same day** after he pointed out the first answer was sized against *today's*
row counts rather than the future the seam has to survive. §5.1 and §7.5 are that
revision; §7's cost argument was retracted outright once `bge-m3` was found
already running on this host. §7.6 records the model bake-off that followed —
the literature says bge-m3 is superseded, this corpus says otherwise, and the
corpus wins. It **corrects
[23-retrieval.md](23-retrieval.md) §6.1**: the CJK technique recommended there is
a workaround for an engine without trigram support, and Postgres solves the same
problem in one operator — which the note tier had already concluded and shipped
in migration `0026`.
