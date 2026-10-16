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
  open; this bundle assumes they do, and [45](45-persona-adaptor.md) §3 is
  the one amended "allowed in" table for Persona).
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
| [35](35-persona.md) | the operator's rulings: Persona composes identity, character and hypotheses; disputes (the subject's hides a line at once, anyone else's is weighed); the unit as the read boundary; deleted messages; the one-off backfill |
| [45](45-persona-adaptor.md) | the Persona schema (typed lines around prose), the enforced reasoning modes, the adaptor's inputs and its one write verb, and the Honcho coverage measure; reverses DEC-184/290's no-schema rule for Persona |
| [50](50-learning-from-acts.md) | learning from every act: an outcome episode per social act, explicit feedback, `outcome_of`, what Lessons and Persona learn from them |
| [60](60-build-step-3.md) | the step 3 build plan: five slices, and where the code corrected the design |
| [40](40-prior-art.md) | October 2026 prior art: 12 memory frameworks, the shipped products and companions, the multi-party research, and what is taken and refused |

## The shape after Honcho (as ruled 2026-10-15)

```
 AUTHORED   doctrine, policy, identity.toml            (not indexed)
    │
 RECORD     messages · media readings · links · social items · disputes ·
    │       feedback      ingestion writes the row and project()s its node + edges
    ▼
 ROLLUP     outings · stays · trips · response episodes   rhythm · ties (by key only)
    │          each current build projects its episodes + `during` edges
    ▼
 NOTEBOOK   Register (notes, memos, events)   Persona (identity · character ·
            hypotheses)   Lessons   Standing      each written by its one writer;
            memos, events, lessons, Persona (and notes, once built) store their
            citations with the entry, projected as `cites` edges; Standing has none
    │
    │      ┄┄┄┄┄┄┄┄┄┄  MEMORY INDEX  (outside the layers: derived, rebuildable,
    │                  never cited; one per database)
    │                    memory_node: pointer + text + vector + time + scope
    │                    memory_edge: closed relation set, copied from the stores
    │                    ▲
    ▼                    │ one retrieval service: scope → seed → expand → rerank
 VIEWS      push (the speaker's and the room's Persona, open notes, Standing)
            · pre-fetch · ask_memory
```

Honcho no longer sits beside the flow: its every use has a fate in
[10](10-honcho-audit.md) §4 and §5. The memory index is drawn outside the
layers because it is not one (amended 2026-10-15): it is a derived index
rebuilt from the stores it reads, it is never cited, and nothing derived
rests on it (cognition-loop/05 rule 2: only the Record and the Rollup are
evidence). Losing it loses nothing but speed.

## The proposed shape, in three parts

**1. One index and graph, one verb** (detailed in [25](25-memory-graph.md),
which supersedes this paragraph's flat-index framing and the vector storage
below, [25](25-memory-graph.md) §3). A projection over every store: messages,
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
  `induced`-only. **Ruled 2026-10-15 ([35](35-persona.md)):** abduced lines
  are Persona's third part, Hypotheses, each with a prediction, and never
  rendered as fact.
- **A hypothesis that can be proved wrong.** An abduced row today goes out as
  a question. The proposal: it also carries a **prediction** that later Record
  rows confirm or refute ("if the evening explanation holds, a long RPG
  suggestion is declined"), and it is retired when refuted. The confirmation
  rate is then a grounded measure of the Dreamer's abduction, read from later
  evidence rather than from the model's opinion of itself. How the verdict is
  kept from being self-graded is [45](45-persona-adaptor.md) §3.

## Sequence

Amended 2026-10-15 (operator): step 0 has run, and the steps between the
index and Honcho's exit are now listed in their order.

0. **The journeys on existing tools first**, conversational-recall's own
   first step ([conversational-recall/20](../conversational-recall/20-orchestration.md)
   "Smallest viable choice"): its three journeys, with better assembly and no
   new index. Its instrument (0a) is the
   [`conversational_recall`](../../evals/corpora/conversational_recall.yaml)
   corpus, eight anchored cases judged on `recall_grounded`, each on a full
   restore of every store as of its instant.
   **Run at `ba17dcdd`, n=1: 7 of 8 PASS.** The one failure was a
   time-scoping misread (a week with no outings answered with an older
   outing); no case failed because a record could not be found. A rerun at
   `b34d34e1` with `rollup_read` on the tag path (PR #8, DEC-312) scored 7 of
   8 again, one case fixed and another broken, so that change is not shown to
   help. n=1 decides nothing about a rate, but it is the reading this step
   asked for.
   **Applying this step's own rule honestly:** recall correctness does not
   justify the index; the journeys pass on existing tools. The refactor
   proceeds on the operator's own grounds (operator, 2026-10-15): retire
   Honcho, one memory she owns, one verb, and Persona. The index is the means
   to those, not a fix for a measured recall failure.
1. **Audit** ([10](10-honcho-audit.md)): what Honcho supplies that no tier
   supplies. Done for code and call volume; whether the prefilled blocks
   change replies is step 7's replay.
2. **Bake-off** ([20](20-embedding-bakeoff.md)): choose the embedder on Nora's
   corpus. Runs beside step 3; the index's lexical channel does not wait for it.
3. **Index, `project()` and the one verb**, lexical first, over the stores
   that exist ([25](25-memory-graph.md) §7); vectors join when step 2 picks
   the embedder. The conversational-recall journeys are its acceptance cases.
3a. **Place containment** ([25](25-memory-graph.md) §6a, [60](60-build-step-3.md) §0
   item 2): place nodes and `located_in` edges (venue → district → city),
   from the geocoder's structured address components for event venues and
   outing places. Nothing holds a place hierarchy today, and saved places
   stay out (R-35). Until this lands, "near Mong Kok" reaches only what names
   Mong Kok.
4. **Note citations.** `note_create` / `note_update` gain citations like
   memos, lessons and events. `note_revision` has no citations column today
   (`nora/infra/sql/schema.py`, the `note_revision` table), so this is a schema
   and verb change, not a projection.
5. **Persona schema and `persona_write`** ([45](45-persona-adaptor.md) §1–§4),
   with the build prerequisites listed in 45 §4.
6. **The Persona adaptor in shadow, and the backfill** ([35](35-persona.md)
   §4, [45](45-persona-adaptor.md) §6): Persona is written under a shadow key,
   every prompt is unchanged, Honcho's feed keeps running.
7. **The gates**: the coverage measure and the reply replay
   ([45](45-persona-adaptor.md) §6).
8. **Switch the prompts**: `<persona>` replaces `peer_card`, `peer_history`
   and `<person_account>`.
9. **Honcho out**: each read replaced or dropped per the audit, the feed and
   `remember` rerouted, the routing deleted, and with it the workspace
   (DEC-214): the unit is the only boundary ([35](35-persona.md) row 6,
   ruled 2026-10-16); `policy.toml`'s `[units.<u>] workspace`, `identity.toml`'s
   `[workspaces]` and the routing go, and the portal's viewer scope moves to
   the unit. The Honcho client itself is
   deleted only after the switch has held for 14 days with the gates' readings
   unchanged, so the rollback until then is a config flip.
10. **Acts get outcomes** ([50](50-learning-from-acts.md)), before:
11. cognition-loop step 5 (social and play), writing into the index from its
    first row; step 6 (faces); then personalisation.

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
  smallest-choice rule). **Read 2026-10-15:** they did, 7 of 8 at n=1
  («Sequence» step 0). The operator proceeds on the grounds recorded there,
  so this reading no longer gates the index; it still forbids claiming the
  index improves recall until a journey shows it.
