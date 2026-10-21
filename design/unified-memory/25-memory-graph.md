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
media attachment, a note, a memo, an event, a lesson, a Persona
line ([35](35-persona.md)), a Standing section, a rollup episode (an outing,
stay or trip, and the response and act episodes of
[50](50-learning-from-acts.md)), a social item, a link, a dispute or feedback
item, one of her own artifacts (a document she wrote, an image she
generated), a place, a person, a room, the unit, Nora. A node row holds a pointer and what is needed
to find it (its text for matching, its vector, its time, its scope). It
never holds the item's truth; a reader follows the pointer to the current head.

**An edge** is `(src_kind, src_id, rel, dst_kind, dst_id, provenance,
created_at)`.

**The whole graph is a projection and can be rebuilt.** Every edge is read
from something an owning store already holds, so the graph is never an owner
of truth (conversational-recall/20):

| edges | where the truth lives | projected by |
|---|---|---|
| `authored_by`, `in_room`, `attached_to`, `shared_by`, `during`, `mentions`, `located_in`, `outcome_of` | the stores' columns (a message's author and room, media in `messages.media_json`, an episode's interval and act, a place's containing place) | code, from the columns |
| `cites`, `about`, `supersedes`, and the entry → entry links (§2) | the Notebook entry's own citations, links and revision chain (cognition-loop/05 §3: "code resolves them and writes the edges"), stored with the entry | code, from the entry |
| `disputes`, `feedback_on`, `depicts` | a Record item (`persona_dispute`, `feedback`, a face label) | code, from the Record |

**`cites` edges exist only for stores that store citations** (operator,
2026-10-15): memos, lessons, events, Persona lines, and notes. A note
revision stores them in `note_revision.citations` since migration
`0116_note_citations` (00 «Sequence» step 4, built 2026-10-17): what its body
cites, in `memo_revision.citations`' form, and the index projects the head's
as it does a memo's. Standing stores no citations and has no `cites` edges.

**Nothing walks these edges for withdrawal** (operator, 2026-10-15). The
retraction cascade and its `citation` edge table were deleted on 2026-10-03
(migrations 0092 and 0093; cognition-loop/10 §2 rule 7, cognition-loop/05
§3, cognition-loop/70 §9), and the table was dropped because nothing read it.
`memory_edge` is not that table again: its named reader is the retrieval
service (§4), which expands along it on every search. A message deleted on
the platform (`messages.hidden_at`) is hidden: never quoted, never a hit, its
node dropped. Entries derived from it stay and cite it as "a message since
deleted"; nothing is purged and nothing is re-derived ([35](35-persona.md)
§5). A lost graph is rebuilt from the stores, and nothing is lost. Where each
projection is written is §7.

## 2. The relation vocabulary is closed

A new relation is a schema change, not something a model invents. The starting
set, each with its writer:

| rel | from → to | source of truth |
|---|---|---|
| `authored_by` | message, social item, her artifact → person (Nora for her own) | ingestion |
| `in_room` | message, link, entry → room | ingestion, write verb |
| `shared_by` | link, media, a social item's medium → person | ingestion; the social stream's projection |
| `attached_to` | message reading → message; media → message or link; link → message; a social item's medium, and its tagged place, → its social item | ingestion; the media lane for a reel read from a link (§6 step 3); the social stream's projection (§7a) |
| `during` | message, media → episode | Rollup builder |
| `about` | entry → entity (person, room, unit, Nora, place) | write verb |
| `cites` | an entry that stores citations (memo, event, lesson, Persona line, note; never Standing) → Record row, Rollup key or episode | write verb |
| `supersedes` | entry → entry | write verb |
| `tracks`, `closes_into`, `leaves_behind`, `draws_on`, `informed_by` | entry → entry: DEC-249's typed links (cognition-loop/05 §3). A note tracks an event, closes into a memo; an event leaves behind a memo; a collective's Persona, a lesson, or a `deduced` Persona line draws on a Persona line; a Standing line is informed by a Persona line or a lesson | write verb |
| `disputes` | `persona_dispute` item → Persona line | projected from the Record item |
| `feedback_on` | `feedback` item → act (a send, a comment reply, a summons answer) | projected from the Record item ([50](50-learning-from-acts.md) §3) |
| `depicts` | media → person | face label (step 6) |
| `mentions` | message, social item → person, place | ingestion, from resolved handles and resolved place names only |
| `located_in` | place → place (a restaurant → a district → a city); an outing → its district | code, from the place's resolved address components; never a model |
| `outcome_of` | episode → act | the Rollup builder ([50](50-learning-from-acts.md)) |

**A link is never evidence** (cognition-loop/05 §3, DEC-249). A Persona line
cites only Record rows, Rollup keys and episodes. A line that rests on other
lines (a `deduced` line's premises) links to them with `draws_on`; it does
not cite them ([45](45-persona-adaptor.md) §3).

**What is refused** (cognition-loop/80): a model-extracted triple such as
"Rin –likes→ roguelikes". That is a Character line with its citations, not an
edge. A memory graph records where things came from and how they are tied; a
knowledge graph records claims a model pulled out of text, with nothing
behind them. Mem0 removed graph memory from its open-source release, and the
multi-party benchmark GroupMemBench found speaker attribution is
what ingestion loses ([40](40-prior-art.md)).

## 3. In Postgres, without a graph database

Two tables, `memory_node` and `memory_edge`, beside the stores, in the same
database. Walking edges is a `WITH RECURSIVE` query. pgvector 0.8.6 and
pg_trgm 1.6 are already installed in Nora's database (read on norty
2026-10-14).

**Vectors sit on the row that carries the scope** (amended 2026-10-15). A
scoped search must filter inside the index scan, which pgvector 0.8 does with
iterative scans over the indexed table's own columns. A join to the scope
after the scan is a post-filter: the top k are found across everything and
then cut, and a room with few rows gets nothing back. So the HNSW index sits
on `memory_node` itself (or a vector table carrying `unit` and `origin_room`
on every row), and each read passes its permitted units and rooms, computed
per read, as arrays (`unit = ANY($1) AND origin_room = ANY($2)`). The unused
`embedding` table cannot do this as it is: it has no scope columns. It is
given them or replaced; a winning embedder whose dimension is not 1024 adds a
column by migration either way.

**Why not Neo4j or FalkorDB.** Hiding a message should drop its node and edges
in the same write, so a search never returns it. In a second database those
can disagree, and a search can return a hidden message until they converge.
The graph is small (hundreds of thousands of nodes), and the reads walk a few
hops under a hard ceiling (§4).

**Apache AGE** (Cypher inside Postgres; a PG18 build exists) keeps the one
database and makes multi-hop patterns readable. Its costs: a custom
database image (`pgvector/pgvector:pg18` plus the AGE package), `LOAD 'age'`
on every connection, its own vertex and edge tables kept in step with the
stores, and historically weak variable-length path performance.
**Recommendation: plain SQL first; adopt AGE when real queries need pattern
matching of three hops or more.** AGE buys readability, not capability, and
removing it later is a migration.

## 4. One retrieval service

Every reader calls one service: `ask_memory` in a turn, the pre-fetch into a
prompt, the Cognition session, the portal.

1. **Scope** first (operator, 2026-10-15). **The unit is the read boundary.**
   `[lineage] mode` is `off` and is not re-enabled ("do not enable", operator
   2026-10-03, cognition-loop/10 §3), and a Persona account renders only in
   the unit's rooms that list the person (`nora/language/character.py:40-47`).
   On top of that, **evidence about a person counts only if it was said in a
   room that person is listed in**: a `mentions` edge from a room Kit is not
   in is not followed to Kit, and is not evidence about Kit. The permitted
   units and rooms are computed per read and applied inside the index scan
   (§3), never after ranking
   ([conversational-recall/20](../conversational-recall/20-orchestration.md)).
2. **Seed** by hybrid search: lexical (trigram, or BM25 if [20](20-embedding-bakeoff.md)
   stage 1 wins) plus vectors, fused by reciprocal rank. Exact references
   (the attached image, the named person, the handle) are seeds without a
   search.
3. **Expand along edges, with adaptive depth** (operator, 2026-10-15: the hops
   are the point; §6a). From the seeds the service follows edges hop by hop,
   weighted by relation and age, and keeps going **while the next hop still
   yields nodes above a relevance floor**, within a node budget and a token
   budget, and never past a hard ceiling (proposed: 4 hops). `ask_memory`
   takes an optional `depth` so Nora can ask for more, up to the ceiling.
   Adaptive depth ships only if it beats a fixed two hops on the journey
   slices ([20](20-embedding-bakeoff.md)); until then two hops is the
   setting. Personalized PageRank from the seeds (HippoRAG) is the candidate
   for ranking the expanded set, measured against plain hop decay on
   recall@10 of labelled evidence over the journey slices; PPR ships only if
   it wins by at least 0.05.

   **Scope is checked on every node of the walk**, not only on the seeds and
   the results. A path is cut at its first out-of-scope node, and an
   out-of-scope intermediate is never returned, not even as the provenance of
   an in-scope node. **Hub nodes are not bridges:** a person, room or unit
   node is traversed only as a node's direct neighbour (the author of a
   message, the room it was in), never as a hop from which to fan out to
   everything pointing at it. Two hops through a person node reach everything
   ever said about that person, which is a search on the person, not an
   expansion of the question. **Test:** seed a search in room B, plant a
   target two hops away whose only path crosses a node in another unit, and
   expect it unreachable.
4. **Rerank**, only if [20](20-embedding-bakeoff.md) stage 4 earns it.
5. **Return** an evidence packet (conversational-recall/20): each item with
   its kind, age, source handle, reasoning mode for an entry, and **the path
   that reached it**. The path is the provenance: "this note, because it cites
   a message in which the reel Rin shared was discussed".

`ok`, `nothing_found` and `unavailable` stay distinct, and while a backfill or
rebuild is incomplete the answer is `partial`, never `nothing_found`. An
unreachable index is never "you never said that".

Rollup aggregates (rhythm, tie, play profile) have no node; `ask_memory`
reaches them by key, as `rollup_read` does. Fetched file content stays out by
design (external-files/00).

## 5. How a turn gets its context

Operator, 2026-10-14: tag and fire turns keep the same injection, and a
decision model could choose between a context block and a graph query. The
context-plan bundle measured that decision model on 2026-10-12/13 and parked
it ([context-plan/15](../context-plan/15-what-the-composer-decides.md)): Tev1
said yes to ~90% of parts (AUC 0.557), and two questions dropped ~40% of what
turns used. The graph allows a split that needs no decision model:

| path | what | decided by |
|---|---|---|
| **push** | the fixed core: the speaker's and the room's Persona, open notes, Standing (roughly today's blocks) | code, always |
| **pre-fetch** | the service run from the message's own seeds (speaker, room, referenced media and handles, the message text), rendered as one block under a token budget, through the one renderer (ADR 0012, ADR 0021) | code, from what the message names |
| **pull** | `ask_memory`, when Nora wants more | Nora, who is already the agent |

A separate query-planning agent before the turn would be a second model
reading the same message (CMS: interpret once) and seconds of latency.
conversational-recall/20 reached the same conclusion. **The reading that would
reopen it** (amended 2026-10-15): pre-fetch recall on the labelled journey
slices, the share of each case's labelled evidence the pre-fetch block
carries, below 0.7 (a starting value, fixed before the first run). The
earlier measure, Nora calling `ask_memory` right after a pre-fetch, cannot
see a miss: tag turns rarely call a memory tool at all (3 of the latest 100
`ask_memory` calls, 11 of 100 `about`, [10](10-honcho-audit.md) §2), so a
pre-fetch that missed reads the same as one that held everything.

## 6. Worked example: an Instagram link someone shares

1. **Record.** A message carries a reel link. Ingestion writes the message
   and a link node, with `attached_to`, `shared_by` and `in_room` edges, and the
   caption if the preview carries one.
2. **Wake.** The Cognition session sees it with its graph neighbourhood: who
   shared it, the notes and Persona lines near it.
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

## 6a. Worked example: "I need a restaurant in Mong Kok" (operator, 2026-10-15)

Kit asks in the friends room. The hops are what reach what no word in the
question names:

1. **Seed.** "Mong Kok" resolves to a place node; the message text seeds
   lexically and by vector.
2. **Expand.** `located_in` brings in the places inside Mong Kok (a
   restaurant → Mong Kok → Kowloon). From those, `during` and `mentions`
   reach the outing the group had at one of them last month, Rin's message
   about the area, and a note that cites Sam's message "avoid X, got sick
   there". Every node on each path is in the unit, and the evidence about
   Rin and Sam was said in rooms they are listed in (§4 step 1).
3. **Return** the outing, the note and the message, each with the path that
   reached it.
4. **What the group never said is not memory.** That a restaurant there
   closed last week, or a current hygiene notice nearby, is in no store. The
   recommendation path also runs a fresh `web_search`, recorded as a tool
   result: evidence a later note may cite, never a memory node by itself.

**As built (00 «Sequence» step 3a, 2026-10-17).** The areas are the
geocoder's structured address components, joined «中文 / English» and stored
beside each place: an event's venue takes its district and city from the
Geocoding API by the venue's place id when the venue resolves; an outing at
no saved place takes them from the geocode cache near its centre (R-35: an
outing at a saved place is never placed). `project()` writes a district and
a city hub keyed by level and name (`district:旺角 / Mong Kok`), the venue
`located_in` its district, the district in its city, and the outing
`located_in` its district directly (an outing has no venue node). An area
needs both languages, or it is none (its name is its hub's identity), and
every area name passes R-28's screen before it is stored (a name carrying an
identifier or a coordinate is dropped), venue and geocode cache alike. The
chain is derived from the stores, not from the order of writes: a venue lies
in every district its events were resolved in, a district in every city its
sources pair it with, recomputed for whatever a write touches; a place hub
nothing reaches any more goes with its `located_in` edges, so a rebuild
equals live. The read: a place hub whose name (either language) the question
holds as an exact reference **anchors** the walk, with the places inside it
two levels down, at an exact seed's relevance; the first hop leaves from
them inward only (never out to the city around a district), each step
weighted `located_in` 0.7, and an anchor is never returned. Anchors are not
selected before the scope reads what they reach: every one is a frontier
key up to a ceiling of 500, and a walk that meets it answers `partial`.
The hop spends its node budget strongest parent first, and the hits one step
from a named place are one tier for the answer's gap cut, so the event at a
venue is not cut behind the outings located straight in the district. The
outing is reached by its own `located_in` edge, not by `during`. A place a
hop reaches is still a hub: the anchor is the only exception to §4's hub
rule (DEC-313, amended). `nora memory backfill-places` (dry run unless
`--apply`, rate-limited, idempotent) gives the venues and cached geocodes
resolved before 0118 their areas; outings take them at the next Rollup tick.

## 7. When each node and edge is written

Ingestion stays what it is today: every store's own write path. Each one
gains a single call, `project(kind, id)`, which upserts that row's node and
its column-derived edges.

**Where `project()` runs** (amended 2026-10-15; this said "in the same
transaction", which assumed a shared unit of work Nora does not have). Each
store method opens its own `engine.begin()` (about 116 sites), so `project()`
runs inside the store method's own `begin()` block, beside the write it
projects. The Rollup is the exception: `SqlRollupStore.apply` diff-writes
rollup rows every tick in the separate analytics container, and
`presence_stays` rows are updated in place. So `project()` lives in `apply`,
in `switch_build` and in the mobility writes, and the analytics process links
the projector.

**A projection failure in a store that never raises** (media comprehension,
video readings): the write stands, the row is put on a projection queue and
counted, and the next pass retries it. It is never silently dropped; the
queue's length is part of the index's health ([10](10-honcho-audit.md) §5).

The only other new moving part is the embedding worker.

**What gets a node, and with what text:**

> 2026-10-21 (18 / ADR 0026): each [message reading](18-message-readings.md) is projected as a Record node with the existing `attached_to` relation to each message it reads (§2); no new relation. Its searchable text states about whom, the claim and how it was said; the source messages remain citable. Its visibility is scoped to all its source messages. When any source is hidden (forget/delete) or edited, the reading’s live projection is invalidated: its node is retired and it is re-read from eligible sources on the next build. A reading never outlives the eligibility of what it reads. Retained versions carry supersession provenance; the index remains derived and rebuildable. This is the design, not an implementation receipt.

| layer | item | searchable text | vector |
|---|---|---|---|
| Record | message | the body, re-projected on edit | yes |
| Record | media attachment `(message, sha256)` | the reading copied in from `media_objects.{title,description,content}` (keyed by `sha256`, no room, UPSERTed; there is no `media_descriptions` table); scoped by its message | yes: the reading's text, and the image or video itself if a multimodal embedder qualifies ([20](20-embedding-bakeoff.md)) |
| Record | link | URL, caption, preview text | yes |
| Record | social item, each of its media `(item, sha256)`, and its tagged place (§7a) | the item: what it is («Instagram story»), caption, and its media's readings; a medium: its reading; the tagged place: `location` as recorded, readable only where the author passes presence. Never `alt_text`, `tagged` or `mentioned` | yes: the item from its text once it holds more than its label; a medium from its bytes if an image, else from its reading; the tagged place from its text |
| Record | `persona_dispute`, `feedback` item | the speaker's words | lexical only |
| Record | her own artifacts (a document she wrote, an image she generated) | title, text or generation prompt | yes |
| Rollup | outing, stay, trip | a rendered line: who, the place label, when | lexical only |
| Rollup | response and act episodes ([50](50-learning-from-acts.md)) | a rendered line: the act, who answered, how | lexical only |
| Rollup | rhythm, tie, play profile | no node: read by key (`rollup_read`, `where_been`), and by `ask_memory` through the same key reads | no |
| Notebook | note, memo, event, lesson | title and body; an event's typed fields rendered | yes |
| Notebook | Persona lines and Standing sections ([35](35-persona.md)) | the line's or section's text | yes |
| entities | person, room, unit, Nora, place | names and handles; a place's name and its `located_in` chain | lexical only |

**Not indexed:** raw presence fixes, authored doctrine and policy, external
file content (only the link is kept, external-files/00), and Honcho.

**When:**

| moment | nodes | edges | written by |
|---|---|---|---|
| a message is ingested | message, its links, its attachments | `authored_by`, `in_room`, `attached_to`, `shared_by`, `mentions` (resolved handles and places only) | ingestion, inside the message write's `begin()` |
| a message is edited | the message node's text | `mentions` recomputed | the edit's own write |
| a message is deleted on the platform (`messages.hidden_at` set) | its node and its attachments' nodes dropped | its edges removed | the hide's own write; entries citing it stay and cite "a message since deleted" ([35](35-persona.md) §5) |
| a media reading lands or is re-read | every attachment node of that `sha256` takes the reading | `attached_to` | the media lane, in the reading's write; a failure is queued, never dropped |
| a Rollup tick or build switch | episode nodes diff-written with the rows; a switched-out build's removed | `during` (message, media → episode), `outcome_of` (episode → act) | `SqlRollupStore.apply` and `switch_build`, in the analytics process |
| a presence stay is updated in place | the stay's node re-projected | `during` | the mobility writes |
| a Notebook entry or revision is written | the entry | `cites` (stores with citations only), `about`, `supersedes`, the links | the tier's one writer, in its store method's `begin()` |
| a dispute or feedback item is recorded | the item | `disputes`, `feedback_on` | ingestion of the Record item |
| the subject disputes a line about themselves, or says "forget that" | the Persona line marked `hidden` at once; its node is dropped | its edges removed | ingestion of the Record item, before the adaptor's next session ([35](35-persona.md) §2) |
| an entry is retired or superseded | node marked so; a reader follows the pointer to the head | kept for the revision chain, not followed by a search | the writer |
| after any of the above | vectors | — | the embedding worker, asynchronously; lexical search finds the node before its vector exists |
| rebuild | all, from the stores | all | one command; also the first deployment's backfill ([35](35-persona.md) §4). It reports the count it could not project; until it finishes, a search answers `partial`. The media part runs under a throttle sized from the measured media count ([20](20-embedding-bakeoff.md) «Not decided here») |

A node is never written for its own sake and an edge is never inferred from
text by a model (§2).

## 7a. The social stream's items (built 2026-10-16)

Operator rulings of 2026-10-16, answering
[cognition-loop/22](../cognition-loop/22-social-stream.md) §9. The code is
`nora.infra.sql.memory_index` (`_project_social`, `story_frame_embedded`),
`nora.infra.sql.memory_read` and `nora.agent.memory_search`; migrations
`0110_memory_social` and, for the tagged place (item 4), `0113_memory_social_location`.

1. **The node mirrors an attachment.** One `social` node per `social_item`,
   keyed `network:item_id`, and one `social_media` node per medium of it,
   `(item, sha256)` in `media_ref`'s order, `attached_to` the item (a carousel
   is several). Two new kinds, not `media` with a social parent: an
   attachment's room, its type and its drop are all read off its message, and
   a social item has no message. The item's text is a label for what it is
   (`social_label`: «Instagram story», «Instagram post, co-authored»), the
   caption and its media's readings (`media_objects`, keyed by the bytes); a
   medium's text is its reading. The item is embedded from its text, but
   only once it holds more than its label: a label alone (no caption, no
   reading yet) would give every such item the same vector, so the worker
   records it `empty` (`nothing_to_embed`), lexical search still finds it by
   its label, and a reading landing changes its text and frees it. A medium
   is embedded from its bytes when custody recorded an image type, else from
   its reading (the attachment rule, `embeds_bytes`).
2. **Scope: the author's, like a stay.** `persons` is the item's person, no
   unit, no room (`PERSON_SCOPED_KINDS`): readable only in a room that lists
   them, in any unit. The item is not under the presence rule: it carries
   no lineage. Its tagged place is (item 4). Tagged and mentioned handles get no `persons` entry and no edge;
   the author is `authored_by` (the item) and `shared_by` (each medium).
3. **The off switch is applied at READ.** `MemoryScope.social_off` holds the
   listed persons whose `identity.toml` block sets `social = false`, computed
   per read by the `memory_search` tool from the identity it holds
   (`nora.infra` reads no identity). It is required with no default on
   `MemoryScope`, `scope_for` and `MemorySearch.search`, so a new caller
   cannot omit it and read everything. `in_scope` and `_in_scope` leave out every
   social node naming one of them, inside every read, so an item recorded
   before the switch is out too and switching it back needs no rebuild. The
   projector stays pure.
4. **Text: the caption, verbatim; never `alt_text`; the location in a gated
   node of its own.** Nor the tagged or mentioned handles. The caption is
   indexed like a message body: an @mention in it is the author's own text,
   and the answer's identifier screen (`scan_identifiers`: a phone number, a
   UUID, a key) withholds an item whole only for those, as for any other
   text. Alt text was ruled out the same day (operator): the network's
   generated alt text is typically phrased with the place and the tagged
   handles (0 of 32 live rows held one).

   **The location** was first left out with it, and is searchable since the
   operator's ruling of 2026-10-17 («should a post's location tag be
   searchable in Nora's memory? — yes»). A tagged place is one the author
   published, and still a person's whereabouts, so it is read under the
   rule a stay is: a `social_location` node, keyed `network:item_id` like
   its item, `attached_to` it, the author's (`persons`, no unit, no room),
   its text the `location` column as recorded, and embedded from it. It is in
   `PRESENCE_KINDS` beside the episodes, so `in_scope` and `_in_scope` admit
   it only where every person of it is in `presence_persons`: the room's
   `location` flag AND the live DisclosureGuard. A separate node, not a line
   of the item's text, because scope is decided per node: a read that could
   only take or leave the whole item would have to hide the post to hide its
   place. So where presence fails the item is found by its caption and its
   media, and its place is never seeded, never a neighbour and never in the
   rendered answer; where it passes, a search on the place finds the place
   node and reaches the item one `attached_to` hop away, and the answer's
   `kind` says «Instagram tagged place». It is in
   `KINDS_BY_CATEGORY["media"]` with its item and opted out with it
   (`social_off`). Alt text stays out: it was not ruled on, and it would
   carry the place past the gate. The poller records `location` for posts,
   reels and story frames, the place's name only
   ([cognition-loop/22](../cognition-loop/22-social-stream.md) §5). Items
   recorded with a location before
   `0113` are put on the projection queue by that migration and re-projected
   by the drain loop; no rebuild is needed.
5. **No new `kinds` category, no tool-text change.** All three kinds are in
   `KINDS_BY_CATEGORY["media"]`. The answer says what an item is and how old
   it is: its `kind` is the label («Instagram story»), a medium's is
   «Instagram picture or video», and `age` is from `taken_at`. A medium gets
   no media handle: `media_fetch` resolves only the chat's own media, so a
   handle would fail as if nothing were there.

**Known limit.** A collab post is scoped and opt-out-checked by the polled
author only: `collab` records that another account owns it, not who.

**Deletion and visibility.** An item with any `social_item_state` row (gone
or hidden) has no nodes, like a hidden message. Every write of `social_item`,
`social_item_state` and `social_perception` (`SqlSocialStreamStore.
insert_item`, `record_state`, `record_perception`) projects the item in its
own transaction, and a reading landing on the bytes (`reproject_media`)
re-projects every item holding them. `INDEXED_WRITES` lists the three
tables, so the architecture test holds every writer to it; no other process
writes them. `nora memory rebuild` walks `social_item` too.

**What `StoryMediaExpiry` reads.** `story_frame_embedded(conn, frames,
model=...)` answers, of `(network, item_id)` pairs, the frames whose every
medium node holds a vector of the current fingerprint or is terminal (given
up, or failed `empty`). A frame with no medium node (never projected, gone or
hidden, or its projection still queued) is not embedded, so its bytes wait for
the hard limit. The social stream's own follow-up replaces
`nora/app/social_expiry.py`'s `# ponytail:` read with it. If a store's write path has no `project()` call, its rows
are absent from search, which an architecture test catches by listing every
indexed kind against the write paths that project it.
