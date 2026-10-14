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

## What this bundle builds on, and what it reopens

- **The data model** is [cognition-loop/05](../cognition-loop/05-data-model.md)
  (DEC-249): Authored → Record → Rollup → Notebook → Views, with Memory
  (Honcho) *beside* the flow. **This bundle proposes amending DEC-249**, and
  says so rather than calling it a reading: §1 fixes the Notebook at four
  tiers, and §2 rules that "per-person facts live in Memory". With Memory
  gone, per-person facts need a lawful home: [35](35-persona.md) (ruled
  2026-10-15) widens Character into Persona, identity, character and abduced
  hypotheses in one account, so the Notebook keeps four tiers. Rules 3 (one writer per tier) and 4
  (speaking turns only read) are kept, not reopened.
- **The reasoning modes** are [cognition-loop/10](../cognition-loop/10-tiers.md)
  §4: `computed`, `told`, `deduced`, `induced`, **`abduced`** and `said`
  (whether they stay as stored detail is cognition-loop/05 §10 Q2, still
  open; this bundle assumes they do, and node 35 amends their "allowed in"
  column for Persona).
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
| [15](15-memory-map.md) | every store as part of the one memory: notes, memos, media, presence episodes, social items, with what each remembers and how it is indexed and corrected |
| [20](20-embedding-bakeoff.md) | the retrieval-stack bake-off: trigram vs BM25, bge-m3 vs EmbeddingGemma 2 (released 2026-10-06) vs WeMM-Embedding, fused, then reranked, on Nora's own mixed Cantonese and English corpus |
| [25](25-memory-graph.md) | the memory graph (one node per item in any store, a closed set of edge relations, citations written with their entries) and the one retrieval service over it: scope, seed, expand, rerank, with push / pre-fetch / pull injection |
| [30](30-identity-and-character.md) | (superseded in part by 35) Identity as its own tier beside Character, for actors (human, Nora, another agent) and collectives (room, unit), with "remember this" and "that's wrong" |
| [35](35-persona.md) | the operator's rulings: Persona composes identity, character and hypotheses; anyone may dispute; visibility; the one-off backfill |
| [40](40-prior-art.md) | October 2026 prior art: 12 memory frameworks, the shipped products and companions, the multi-party research, and what is taken and refused |

## The shape after Honcho (as ruled 2026-10-15)

```
 AUTHORED   doctrine, policy, identity.toml            (not indexed)
    │
 RECORD     messages · media readings · links · social items · disputes
    │          ingestion writes the row and project()s its node + edges
    ▼
 ROLLUP     outings · stays · trips (episodes)   rhythm · ties (by key only)
    │          each current build projects its episodes + `during` edges
    ▼
 NOTEBOOK   Register (notes, memos, events)   Persona (identity · character ·
            hypotheses)   Lessons   Standing      each written by its one writer,
            citations stored with the entry and projected as `cites`/`about` edges
    │
    ├──────────►  MEMORY INDEX  (derived, rebuildable, one per database)
    │               memory_node: pointer + text + vector + time + scope
    │               memory_edge: closed relation set, copied from the stores
    │                    ▲
    ▼                    │ one retrieval service: scope → seed → expand → rerank
 VIEWS      push (Persona, Culture, open notes, Standing) · pre-fetch · ask_memory
```

Nothing sits beside the flow any more: Honcho's every use has a fate in
[10](10-honcho-audit.md) §4 and §5.

## The proposed shape, in three parts

**1. One index and graph, one verb** (detailed in [25](25-memory-graph.md),
which supersedes this paragraph's flat-index framing). A projection over every store: messages,
notes, memos, events, media readings (and media vectors, if the bake-off
justifies them), Standing, Character, rollup episodes, presence episodes and
social items once step 5 writes them. One row per item: kind, owner store and
row id, subject, scope (workspace, unit, room), the item's own time, the model
and version that embedded it. It is rebuilt from the stores, never written
directly. Changing the embedder re-embeds every row; if the new model's
dimension differs from 1024, it is also a migration, because the `embedding`
table holds one vector column per dimension (`v1024`, migration 0044). Search is
hybrid: the existing `pg_trgm` lexical channel (measured on Cantonese and
English, [dreamer-agent-loop/24](../dreamer-agent-loop/24-embedding-seam.md))
plus vectors. Names, handles and game titles match on words, and meaning
matches on vectors. The scope is chosen **before** searching
(conversational-recall/20: "a global search followed by a room filter is not
the boundary"). Every result carries its kind, its age and a handle to its
source. A judgement about *when* needs a time on its input, and an index row
without one is a defect.

**2. Conclusions for any subject, in Nora's tables.** Honcho's two prefilled
reads (the peer card, and the tagger's representation, rendered as
`peer_history`) and its one write (`remember` → `conclude`) are replaced by
Nora's own tiers: Persona for who an entity is, what it is like and why
([35](35-persona.md)), Register for the unit's affairs. What Honcho holds that no tier holds yet is measured in
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

0. **The journeys on existing tools first**, conversational-recall's own
   first step ([conversational-recall/20](../conversational-recall/20-orchestration.md)
   "Smallest viable choice"): its three journeys, with better assembly and no
   new index. If they pass, steps 2–3 wait.
   Its instrument (0a) is built, 2026-10-14: the
   [`conversational_recall`](../../evals/corpora/conversational_recall.yaml)
   corpus, eight anchored cases judged on `recall_grounded`, each on a full
   restore of every store as of its instant. Not yet run.
1. **Audit** ([10](10-honcho-audit.md)): what Honcho supplies that no tier
   supplies. Done for code and call volume; whether the prefilled blocks
   change replies is open.
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
