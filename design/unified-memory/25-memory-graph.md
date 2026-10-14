# The memory graph and the one retrieval service

Status: **proposed**, 2026-10-14.

## What the operator asked

Operator, 2026-10-14: memory is not only relational rows but how things
connect, a **memory graph** rather than a knowledge graph, with edges to the
note, the memo, the lesson, the rollup, the Instagram story. Is that possible
in SQL? Is there an embedding-plus-graph search? And how does it relate to the
unified hybrid search?

His summary, confirmed: the memory graph is the **indexer and the
relations**. Every store keeps its own table and does its own job (notes in
`note`, media in custody, events with their typed columns); the graph indexes
all of them and holds how they connect. It is not the only storage, and not a
second owner of truth ([conversational-recall/20](../conversational-recall/20-orchestration.md)).

## 1. Nodes, edges and the two kinds of content

**A node** is one item in any store, addressed as `(kind, id)`: a message, a
media object, a note, a memo, an event, a lesson, a Character or Identity
section, a Standing section, a rollup episode, a social item, a link, a
person, a room, the unit, Nora. A node row holds a pointer and what is needed
to find it (its text for matching, its vector, its time, its lineage). It
never holds the item's truth; a reader follows the pointer to the current head.

**An edge** is `(src_kind, src_id, rel, dst_kind, dst_id, provenance,
created_at)`. The graph has two kinds of content, and they are kept apart
because they fail differently:

| content | examples | written by | if lost |
|---|---|---|---|
| **index** | node text, vectors, `authored_by`, `in_room`, `during` | code, derived from the stores' columns | rebuilt from the stores; nothing is lost |
| **asserted relations** | `cites` (an entry and the Record rows it rests on), `supersedes`, `disputes`, `depicts` (a face label) | the owning store's write verb, **in the same transaction as the entry** | not rebuildable; they are part of the truth |

`cites` is the citation-edge table cognition-loop/80 §4.1 already designed for
the retraction cascade, made general. Without it a correction cannot find what
rested on the corrected row.

## 2. The relation vocabulary is closed

A new relation is a schema change, not something a model invents. The starting
set, each with its writer:

| rel | from → to | writer |
|---|---|---|
| `authored_by` | message, social item → person | ingestion |
| `in_room` | message, link, entry → room | ingestion, write verb |
| `shared_by` | link, media → person | ingestion |
| `attached_to` | media, link → message | ingestion |
| `during` | message, media → episode | Rollup builder |
| `about` | entry → entity (person, room, unit, Nora) | write verb |
| `cites` | entry → Record row or Rollup key | write verb |
| `supersedes` | entry → entry | write verb |
| `disputes` | dispute item → entry | tag path (`character_dispute`) |
| `depicts` | media → person | face label (step 6) |
| `mentions` | message → person, place | ingestion, from resolved handles only |

**What is refused** (cognition-loop/80): a model-extracted triple such as
"Alex –likes→ roguelikes". That is a Character line with its citations, not an
edge. A memory graph records where things came from and how they are tied; a
knowledge graph records claims a model pulled out of text, with nothing
behind them. Mem0 removed graph memory from its open-source release, and the
multi-party benchmark GroupMemBench found speaker attribution is
what ingestion loses ([40](40-prior-art.md)).

## 3. In Postgres, without a graph database

Two tables, `memory_node` and `memory_edge`, beside the stores, in the same
database. Walking edges is a `WITH RECURSIVE` query. pgvector 0.8.6 and
pg_trgm 1.6 are already installed in Nora's database (read on norty
2026-10-14). The unused `embedding` table and its HNSW index are where the
vectors go.

**Why not Neo4j or FalkorDB.** A correction must withdraw the entry, its
edges and its index rows together. In a second database those can disagree,
and the retraction cascade becomes eventually consistent. The graph is small
(hundreds of thousands of nodes), and the reads walk one or two hops.

**Apache AGE** (Cypher inside Postgres; a PG18 build exists) keeps the one
transaction and makes multi-hop patterns readable. Its costs: a custom
database image (`pgvector/pgvector:pg18` plus the AGE package), `LOAD 'age'`
on every connection, its own vertex and edge tables kept in step with the
stores, and historically weak variable-length path performance.
**Recommendation: plain SQL first; adopt AGE when real queries need pattern
matching of three hops or more.** AGE buys readability, not capability, and
removing it later is a migration.

## 4. One retrieval service

Every reader calls one service: `ask_memory` in a turn, the pre-fetch into a
prompt, the Cognition session, the portal.

1. **Scope** first: the reader's room or unit, from lineage. Filtering is
   applied in the index scan (pgvector 0.8 iterative scans), never after
   ranking ([conversational-recall/20](../conversational-recall/20-orchestration.md)).
2. **Seed** by hybrid search: lexical (trigram, or BM25 if [20](20-embedding-bakeoff.md)
   stage 1 wins) plus vectors, fused by reciprocal rank. Exact references
   (the attached image, the named person, the handle) are seeds without a
   search.
3. **Expand** one or two hops along edges, weighted by relation and age.
   Personalized PageRank from the seeds (HippoRAG) is the candidate for
   ranking the expanded set, measured against plain hop decay.
4. **Rerank**, only if [20](20-embedding-bakeoff.md) stage 4 earns it.
5. **Return** an evidence packet (conversational-recall/20): each item with
   its kind, age, source handle, reasoning mode for an entry, and **the path
   that reached it**. The path is the provenance: "this note, because it cites
   a message in which the reel Alex shared was discussed".

`ok`, `nothing_found` and `unavailable` stay distinct. An unreachable index is
never "you never said that".

## 5. How a turn gets its context

Operator, 2026-10-14: tag and fire turns keep the same injection, and a
decision model could choose between a context block and a graph query. The
context-plan bundle measured that decision model on 2026-10-12/13 and parked
it ([context-plan/15](../context-plan/15-what-the-composer-decides.md)): Tev1
said yes to ~90% of parts (AUC 0.557), and two questions dropped ~40% of what
turns used. The graph allows a split that needs no decision model:

| path | what | decided by |
|---|---|---|
| **push** | the fixed core: the speaker's Identity, the room's Culture, open notes, Standing (roughly today's blocks) | code, always |
| **pre-fetch** | the service run from the message's own seeds (speaker, room, referenced media and handles, the message text), rendered as one block under a token budget, through the one renderer (ADR 0012, ADR 0021) | code, from what the message names |
| **pull** | `ask_memory`, when Nora wants more | Nora, who is already the agent |

A separate query-planning agent before the turn would be a second model
reading the same message (CMS: interpret once) and seconds of latency.
conversational-recall/20 reached the same conclusion. **The reading that would
reopen it:** traces showing Nora calling `ask_memory` right after a pre-fetch
for what the pre-fetch should have held. That count is the measure of the
pre-fetch's misses.

## 6. Worked example: an Instagram link someone shares

1. **Record.** A message carries a reel link. Ingestion writes the message
   and a link node, with `attached_to`, `shared_by` and `in_room` edges, and the
   caption if the preview carries one.
2. **Wake.** The Cognition session sees it with its graph neighbourhood: who
   shared it, the notes and Character lines near it.
3. **Escalate if relevant.** `instagram_read` fetches the reel. Media custody
   keeps the bytes; comprehension reads the video (MiMo). That adds a media
   node, with `attached_to` the link.
4. **Enrich.** A `web_search` on what the reel shows. The result is a recorded
   tool result: evidence to cite, not fact.
5. **Settle.** Cognition writes a note ("want to try this place") or updates
   an interest, `cites` the reel's reading and the search result, `about` the
   room. The citations are the edges.

Three constraints hold today:

- **A story is never opened in a turn nobody asked for** (operator safety
  rule, [instagram-adoption/18](../instagram-adoption/18-instagram-read-and-search.md),
  pinned by `test_a_story_is_never_opened_in_a_turn_nobody_asked_for`).
  Step 3 for a story in a Cognition pass needs the operator to change that
  rule. A post or reel shared into the room is not that rule's case, but the
  same node records the open decision on unprompted lookups.
- **Stories expire** in 24 hours and view-once media cannot be kept; capture
  has a deadline.
- **Cost.** An escalation is a fetch and a video read. The Cognition pass
  needs a per-pass budget for escalations.
