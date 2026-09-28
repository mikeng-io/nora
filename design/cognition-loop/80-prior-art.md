---
type: design
title: "Prior art — Honcho, Graphiti/Zep, Letta, Generative Agents, Mem0; what Nora adopts, what it refuses, and the boundary with Honcho"
description: "What each memory system does from raw input to derived memory, read from source on 2026-09-28; the mapping to Nora's tiers; the word collisions and how this bundle avoids them; the adopted mechanisms (retraction cascade, decay and reinforcement, valid time only where intrinsic, hybrid search, contradiction as a row); the refused ones (model-extracted triples, rewriting old rows, agent-written procedure); and where Honcho stops and Nora's tiers start."
tags: [nora, prior-art, honcho, graphiti, zep, letta, generative-agents, mem0, memory, terminology]
timestamp: '2026-09-28'
edges:
  - {dst: 10-tiers, rel: informs, provenance: asserted}
  - {dst: 25-rollup, rel: informs, provenance: asserted}
---

# 80 · Prior art

Read from source on 2026-09-28: Honcho `2eb27b6` (v3.2.1), Graphiti `6b4b56f`,
letta-code `e14d5e5`, mem0 `94c3fe9`; papers by URL. The operator asked
(2026-09-28) whether Honcho's deduction, induction and abduction, and
Graphiti's model, should shape this design.

## 1. What each does

| system | raw → derived | who writes, when | time | contradiction | retrieval |
|---|---|---|---|---|---|
| **Honcho** (github.com/plastic-labs/honcho) | messages → **conclusions** keyed by (observer, observed), levels `explicit`, `deductive`, `inductive`, `contradiction`; a **peer card** of identity, attribute, relationship, instruction only ("no behavioral content") | the *deriver* (per message batch, explicit only, from the peer's own messages) and a scheduled *dream* pass (≥ 50 new conclusions, 60 min idle, ≤ 1 per 8 h) with a deduction and an induction specialist | `created_at` and absolute dates in text; no valid time | stored as a `contradiction` row (≥ 2 sources) or resolved by soft delete | representation (a pair's conclusions as text), dialectic agent, semantic search; **scopes** (v3.1.0) bound recall and cascade removal to derived rows |
| **Graphiti / Zep** (github.com/getzep/graphiti; arXiv 2501.13956) | **episodes** (raw input, non-lossy) → LLM-extracted entities and edges → clustered communities | synchronous on `add_episode` (Zep: background) | **bi-temporal**: `valid_at`/`invalid_at` (world) and `created_at`/`expired_at` (system) on every edge | the old edge is invalidated, never deleted | cosine + BM25 + graph BFS, reranked (RRF, MMR, cross-encoder) |
| **Letta** (MemGPT; letta.com/blog/sleep-time-compute) | core blocks (always in context), recall (history), archival (vectors) | a **sleep-time agent** owns memory edits; the primary agent may not edit core memory; now called "dreaming", triggered by compaction or step count | — | "resolve in favour of the latest evidence" | tools over recall and archival |
| **Generative Agents** (arXiv 2304.03442) | memory stream of observations → reflections citing their sources | reflection fires when summed importance passes a threshold | recency decay | — | recency + importance + relevance |
| **Mem0** (arXiv 2504.19413) | messages → memories | the OSS release reversed its own LLM UPDATE/DELETE to **ADD-only**, and removed graph memory | — | — | semantic + BM25 + entity |

Honcho's code has **no abductive level** (`DocumentLevel`, src/utils/types.py);
its docs mention one, its code calls it hypothetical. Abduction and computation
are Nora's additions.

## 2. Mapping to Nora

| Nora | Honcho | Graphiti | Letta | Generative Agents |
|---|---|---|---|---|
| Record | messages | episode | recall | observation |
| Rollup | — | — (everything is LLM-extracted) | — | — |
| Register | — | entity edges | core blocks (task state) | plans |
| Character · person | inductive conclusions | entity summary | `human` block | reflections about others |
| Character · room, unit | none | community summary | — | — |
| Standing | — | — | `persona` block, in part | reflections about self |
| cognition session | dream pass | — | sleep-time run | reflection |
| reasoning `told` / `deduced` / `induced` | `explicit` / `deductive` / `inductive` | — | — | — |
| reasoning `abduced`, `computed` | none | none | — | — |
| tension row | `contradiction` | invalidated edge | — | — |
| lineage | `source_ids` | `episodes[]` | git history | cited indices |

**The pattern is confirmed.** Letta's sleep-time agent and Honcho's dream pass
both keep consolidation off the live path and give the background agent sole
ownership of long-lived memory. Nora's cognition is that pattern, woken by
signals rather than a timer.

## 3. Words that collide, and the rule this bundle keeps

| their word | means there | in Nora it is | rule |
|---|---|---|---|
| Honcho *observation* | a derived conclusion | the opposite of observed | always "Honcho conclusion" |
| Honcho *session* | a conversation thread | a room | always "Honcho session"; bare "session" is cognition's |
| Honcho *dream*, *dreamer* | its consolidation pass | the Dreamer is Nora's runtime | always "Honcho's dream pass" |
| Honcho *representation* | a pair's conclusions as text | not Character ([10](10-tiers.md) §6) | — |
| Graphiti *episode* | a raw ingested item | Record | Nora's episode is Tulving's: a bounded happening ([25](25-rollup.md)) |
| Graphiti *fact* | an edge | retired word (#115) | never imported |
| Graphiti *community* | a clustered group | rooms and units are configured | culture is never "community" |
| Letta *core memory*, *block* | always-on context | "memory" is Honcho; "block" is a `_BLOCKS` row | say "context block" |
| *reflection* (several) | a background pass | cognition | descriptive only |

## 4. Adopted

1. **Retraction cascade** (Honcho scopes). When a Record item is withdrawn — a
   message deleted, the PII scrub, a face label removed, a person leaving a
   unit — every derived revision whose citation chain reaches it is marked
   `unsupported`, stops rendering, and goes on the next agenda for
   re-derivation. Citations already exist; this is what makes them pay.
2. **Transaction time everywhere, valid time only where intrinsic.** Revision
   chains already give when a row was written. `valid_from`/`valid_to` belong
   to Rollup episodes (code computes them) and Register rows (dated by nature),
   **not** to Character or Standing: the knowledge tier measured `valid_to`
   at 0 writes out of 5, because a friends' circle has no authority that
   retracts (design/dreamer-agent-loop/20-knowledge-tier.md §0).
3. **Decay and reinforcement** (ACT-R activation, Generative Agents recency,
   Honcho `times_derived`). Every Character and Standing line carries
   `last_supported_at` (its newest cited Record time) and `support_count`;
   the prompt shows the age, and code puts "account stale" on the agenda.
4. **Absolute dates, and no re-recording** (Honcho's deriver, Graphiti, Letta's
   reflection rules). Write verbs refuse relative time words in date fields,
   and a Register or Character line that only restates one Record row is
   refused: the Record is already searchable.
5. **Hybrid search for `record_query`**: lexical (pg_trgm: Postgres's default
   text search does not segment Cantonese) plus vector plus filters (person,
   room, span), fused by reciprocal rank, recency as the tiebreak.
6. **Contradiction as a row** (Honcho `contradiction`, ≥ 2 sources). Two cited
   Character claims that conflict are kept as a contradiction until a later
   revision resolves one; never silently overwritten.
7. **Agenda priority from code-computed surprise** (Honcho's surprisal
   sampling, Generative Agents' importance): a new place, a first
   co-location, a message burst order the agenda. Never an importance model
   call per Record item.

## 5. Refused

- **Model-extracted triples or an entity graph** (Graphiti, Mem0's graph,
  Cognee): the knowledge tier, #115 — 5 facts in 11 days, predicates that held
  their own answer, every read `nothing_found`. Mem0 removed its own graph
  memory.
- **Rewriting or deleting old rows on the write path** (Mem0's old
  UPDATE/DELETE, A-MEM's memory evolution, Honcho's dream deletes): breaks
  "nothing overwritten" and lineage. Mem0 measured ADD-only as better.
- **Clustered communities** for rooms or units: they are configured.
- **Agent-written procedure or prompts** (Letta skill learning, LangMem prompt
  optimisation): doctrine is authored and never hers to write
  ([55](55-self.md) §3).
- **Precomputing answers to predicted questions** (sleep-time compute's
  headline): it pays only when questions are predictable; Nora's value is the
  unasked.

## 6. The boundary with Honcho

Both Honcho's dream pass and Nora's cognition would induce a behavioural
account of each person from the same messages, and put both in one prompt
(`peer_history` beside `character`). Honcho builds her self card from her own
messages, which rule 4 of [10](10-tiers.md) §2 forbids as evidence. And Honcho
cannot read presence, social items or perceptions, or model a room.

| owner | holds |
|---|---|
| **Honcho** | what a person said about themselves: `explicit` conclusions, the peer card, `remember`. In Nora's terms, *told* evidence about one person |
| **Nora** | everything induced, multi-source, collective or about Nora: Character (all four entities), Standing, Register, Rollup |

1. **Scoped per unit — done, DEC-214 (#603, #605, live 2026-09-28).** Nora's
   hierarchy is workspace > unit > room: each Nora workspace binds one Honcho
   workspace (the person-model boundary), each unit is one Honcho named scope
   inside it, and room and unit reads pass that scope. Live: workspace 67
   (friends, wedding) on Honcho `nora`; ridgeline on `nora-ridgeline`. The peer
   card stays unscoped inside its workspace (Honcho v3.1.2's card endpoint
   takes no scope), so it can mix the units of one workspace, never two
   workspaces.
2. **Push only told conclusions and the peer card**, filtered client-side to
   `explicit`. Once Character · person and Character · Nora ship, Honcho's
   deductive and inductive conclusions and the self card leave the prompt.
   Honcho's dream pass keeps running: the peer card depends on it.
3. **Memory is a hint, never a citation.** A Honcho conclusion may prompt a
   write; the write cites Record ids the session fetched itself.
4. **Measure before cutting.** Compare Honcho's inductive conclusions per
   person with the Character accounts once both exist, then decide whether
   Honcho's induction is worth its spend.
