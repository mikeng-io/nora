# Unified memory: one memory of her own, searched in one way

Status: **proposed**, recorded 2026-10-14. Nothing here is built, and saving
this bundle changes no runtime behaviour. No decision number is allocated.

## What the operator asked for

Operator, 2026-10-14, after the public-release scrub closed: retire Honcho
and give Nora one memory system of her own, the intention already recorded
in [group-situation/20](../group-situation/20-context-awareness-target-shape.md)
§0. Three requirements:

1. **Coherent, not scattered.** The state stores become one whole: files,
   media, what she has concluded about people and groups, and their state.
2. **One way to search it.** A single verb (`ask_memory` or `search_memory`)
   reaches everything, instead of the model having to know which of a dozen
   tools holds the answer.
3. **Any subject, not only people.** Honcho models a person (a peer). Nora
   already reasons about rooms, units and herself (Character, Culture,
   Standing); the memory serves all of them.

He sees the storage and schema side as straightforward, and the **Dreamer**
side (what derives conclusions, in which reasoning mode) as the hard part.
He wants abduction alongside deduction and induction.

This bundle comes before two later phases he named: social engagement
(cognition-loop migration step 5, then faces at step 6), and personalisation
and recommendations. Both write new stores, and building them after the
retrieval layer exists means they are searchable from their first row.

## What is already decided, and this bundle does not reopen

- **The data model** is [cognition-loop/05](../cognition-loop/05-data-model.md)
  (DEC-249): Authored → Record → Rollup → Notebook → Views, with Memory
  (Honcho) *beside* the flow. This bundle's job is to remove "beside": what
  Honcho does moves into the flow or is dropped.
- **The reasoning modes** are [cognition-loop/10](../cognition-loop/10-tiers.md)
  §4: `computed`, `told`, `deduced`, `induced`, **`abduced`** and `said`.
  Abduction already exists as a mode, and its rule is already the right one:
  a value resting only on abduction "goes out as a question", and is never
  upgraded by assent. The operator's three reasoning layers are this table,
  not a new one.
- **The Honcho boundary** is [cognition-loop/80](../cognition-loop/80-prior-art.md)
  §6: Honcho keeps what a person said about themselves (`explicit`, the peer
  card, `remember`), Nora owns everything induced, and "measure before
  cutting". This bundle is the last step of that section.
- **Retrieval stays over existing stores**:
  [conversational-recall](../conversational-recall/00-intent.md) rules out a
  duplicate memory database and a new owner of truth. A derived index that
  can be rebuilt from the stores it reads is not an owner, and this bundle
  proposes exactly that and nothing more. Its evidence-packet contract
  ([20](../conversational-recall/20-orchestration.md)) is the result shape
  `ask_memory` returns.

## What this bundle adds

| node | adds |
|---|---|
| [10](10-honcho-audit.md) | the measured inventory of what Honcho does for Nora today, every call site, how often each read runs live, and what replaces each |
| [20](20-embedding-bakeoff.md) | the plan for choosing the embedder: EmbeddingGemma 2 (released 2026-10-06) and WeMM-Embedding against the incumbent bge-m3, on Nora's own mixed Cantonese and English corpus |

## The proposed shape, in three parts

**1. One derived index, one verb.** A projection over every store: messages,
notes, memos, events, media readings (and media vectors, if the bake-off
justifies them), Standing, Character, rollup episodes, presence episodes and
social items once step 5 writes them. One row per item: kind, owner store and
row id, subject, scope (workspace, unit, room), the item's own time, the model
and version that embedded it. It is rebuilt from the stores, never written
directly, so changing the embedder is a rebuild and not a migration. Search is
hybrid: the existing `pg_trgm` lexical channel (measured on Cantonese and
English, [dreamer-agent-loop/24](../dreamer-agent-loop/24-embedding-seam.md))
plus vectors. Names, handles and game titles match on words, and meaning
matches on vectors. The scope is chosen **before** searching
(conversational-recall/20: "a global search followed by a room filter is not
the boundary"). Every result carries its kind, its age and a handle to its
source. A judgement about *when* needs a time on its input, and an index row
without one is a defect.

**2. Conclusions for any subject, in Nora's tables.** Honcho's three reads in
the prompt (the peer card, the tagger's representation, `peer_history`) and its
one write (`remember` → `conclude`) are replaced by Nora's own tiers:
Character for the account of a person, a room, the unit or Nora; Register for
a person's told claims. What Honcho holds that no tier holds yet is measured in
[10](10-honcho-audit.md) before anything is cut.

**3. The Dreamer's modes, made complete.** The Cognition session already
writes with a mode. Two things are open:

- **Abduction where it is useful.** 10-tiers §4 allows `abduced` in Register
  and Standing only. The recommendation case ("they pick short co-op games
  *because* one of them only has evenings free") is an explanation of a
  unit's behaviour, which is Character's question, and Character is
  `induced`-only. Whether an abduced line may sit in Character, marked as a
  hypothesis, or must live in Standing, is the operator's decision.
- **A hypothesis that can be proved wrong.** An abduced row today goes out as
  a question. The proposal: it also carries a **prediction** that later Record
  rows confirm or refute ("if the evening explanation holds, a long RPG
  suggestion is declined"), and it is retired when refuted. The confirmation
  rate is then a grounded measure of the Dreamer's abduction, read from later
  evidence rather than from the model's opinion of itself.

## Sequence

1. **Audit** ([10](10-honcho-audit.md)): what Honcho supplies that no tier
   supplies. Done for code and call volume; the prompt-contribution read is open.
2. **Bake-off** ([20](20-embedding-bakeoff.md)): choose the embedder on Nora's
   corpus.
3. **Index and the one verb**, over the stores that exist, with the
   conversational-recall journeys as its acceptance cases.
4. **Honcho out**: each read replaced or dropped per the audit, the feed and
   `remember` rerouted, the client and its routing deleted.
5. Then cognition-loop step 5 (social and play), writing into the index from
   its first row; step 6 (faces); then personalisation.

## Not in this bundle

- **Interests** (the standing question Nora keeps asking the world for a
  group, with a watermark) are the personalisation phase's object, not
  memory's. They will read from this index.
- **Keeping fetched file content.** External files keep only the parsed link,
  by design ([external-files/00](../external-files/00-design.md)). "Search
  across files" means the links and what was said about them, unless the
  operator reverses that rule.

## The reading that would reopen it

- The audit finds a Honcho output that a tier cannot represent and that
  measurably changes replies: then this is a migration of that output, not a
  deletion.
- The conversational-recall journeys pass on existing tools with better
  assembly alone: then the index is not needed yet (that bundle's own
  smallest-choice rule).
