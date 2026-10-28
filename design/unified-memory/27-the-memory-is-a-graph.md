---
type: design
title: "The memory is a graph: entities, relations and evidence over every store"
description: "Make the memory itself a graph: persistent entities, layered relations with evidence and validity, every store as a node, one research loop that perceives every signal and enriches from the web, a subgraph-returning query with code routing and a preview presentation, and one time-ordered backfill."
timestamp: '2026-10-28'
edges:
  - {dst: 00-intent, rel: extends, provenance: asserted}
  - {dst: 15-memory-map, rel: extends, provenance: asserted}
  - {dst: 18-extraction, rel: supersedes, provenance: asserted}
  - {dst: 25-memory-graph, rel: extends, provenance: asserted}
  - {dst: 40-prior-art, rel: depends_on, provenance: asserted}
  - {dst: 45-persona-adaptor, rel: extends, provenance: asserted}
  - {dst: 47-character-dimensions, rel: depends_on, provenance: asserted}
---

# The memory is a graph

Status: **ratified** by the operator, 2026-10-28 (all five decisions in §16). Docs only: no schema, verb, tool or
migration lands with this node. It supersedes [18](18-extraction.md)'s
single-pass text reading, and amends [25](25-memory-graph.md) §1, §2 and §5,
[15](15-memory-map.md) and [40](40-prior-art.md) §5, which carry dated
pointers here.

## What the operator asked (2026-10-28)

After history extraction finished (5,525 of 5,525 messages, 4,226 claims,
precision 155/156 on a re-queued sample), the operator asked whether this now
matched Honcho, and then ruled on what the memory has to be:

1. **Every signal, not text.** Images, video, links, social engagement
   (Instagram and the rest) and every state Nora holds, not message bodies.
2. **An agent loop, not a single pass**, that reads the image, the video and
   the link, and a **research step** that looks things up: a message says "going
   to a concert", another names a place; the loop can find what event took
   place there at that date and time. "Not only curated memory: an enrichment
   layer to discover more." The value should multiply, not add: each item can
   search and use past context.
3. **A graph.** "Every state, every item and every piece of data about that
   person should be connected within a graph, instead of 'he said this, then
   he said that'. That's not memory; it's a transcript."
4. **Abstract enough that the schema does not change** every time a relation
   or a new kind of item is added.
5. **Queried as a graph:** a query returns a subgraph, not one layer of
   relations.
6. Routing, cost and presentation answered: does every message query the
   graph; does constant querying load the database; is the whole content
   shown or a preview with tools behind it.
7. **One backfill.** Not a backfill now, a second when a signal is added and
   a third after that.

## 1. What exists, measured 2026-10-28

**The extractor reads text only.** A batch's snapshot holds each message's
body (or latest edit), author, time, mentions and reply link
(`nora/infra/sql/extraction.py`, the snapshot builder). The media join that
every other transcript gets ([`nora/domain/media_join.py`](../../nora/domain/media_join.py):
"for EVERY transcript a model is about to read") is not applied, so a photo
reaches the model as `[image a3f2c1]`. Of the extracted messages:

| carries | messages |
|---|---|
| an attachment | 799 (880 images, 53 videos, 6 files, 4 audio) |
| a link | 734 (Instagram 200, YouTube 101, Threads 65, Douyin 50, Steam 30, Twitch 29, …) |
| an attachment and no other text | 696 |

`media_objects` holds a reading for 1,352 of its 1,454 objects: Nora had
read the photos the extractor never saw. The quality spike's recall misses
included 3 photo-only messages of 7.

**The graph is bookkeeping.** `memory_node` 15,607 rows, `memory_edge` 26,148:

| edges | count |
|---|---|
| message `in_room` / `authored_by` | 16,171 |
| stay, trip, outing `about` person | 5,178 |
| media, social media `attached_to` / `shared_by` | 4,085 |
| Character, note, memo `cites` message | 391 |
| events, places, lessons, the rest | ~40 |

Every edge says who, where or attached to what. There are 4 place nodes
beyond the area hubs, no event, work or organisation nodes, and no edge
between two items by meaning. The 4,226 claims are not projected at all.
The graph answers "what did Rin say", not "what happened".

## 2. What this supersedes, and why the old reasons are answered

| earlier position | where | what changes | why the reason no longer holds |
|---|---|---|---|
| the whole graph is a projection and never an owner of truth | 25 §1 | the **entity, relation and evidence** stores below own their rows; `memory_node`/`memory_edge` stay the derived search index over them | the reason was "no second owner of truth": each store still owns its own rows, and the new stores own only what no store held (entities and the relations between items) |
| a new relation is a schema change | 25 §2 | a relation is a **value** in a closed list in code; no migration | a closed list kept in code, with a test on every write, is as closed as a schema, and costs a review instead of a migration |
| model-extracted triples are refused as edges ("the edge says nothing about its evidence") | 25 §2, 40 §5, cognition-loop/80 | judgement relations exist, **each with evidence rows** and marked as judgement | the refused thing was a triple with nothing behind it. Here every judgement relation stores the Record/Rollup rows that support it, its writer and session, and its validity; it can be audited, disputed and superseded like a Character line. Mem0's removal and GroupMemBench's attribution loss are answered by the evidence rows keeping the speaker and the source |
| extraction is one model call per batch over text | 18 | a **research loop** per arrival over every signal | measured above: 27% of messages carry content the single pass never saw |
| a web result is "evidence a later note may cite, never a memory node by itself" | 25 §6, §6a | a fetched page is a **Record item** (`web_page`), a node, citable | the rule's point holds: a page is evidence, never a fact by itself. It becomes a node so a judgement can cite it and a later item can reach it |

Kept unchanged: the unit is the read boundary; scope is checked on every node
of a walk; hidden items leave the index and nothing derived is withdrawn by
cascade ([35](35-persona.md) §5); Tension is never readable; the R-27 three
states; one retrieval service ([25](25-memory-graph.md) §4).

## 3. Three generic stores

Every node is addressed `(kind, id)`, as `memory_edge` already addresses
them. A node is either a row in its **own store** (a message, a stay, a note:
its fields stay in its table, which is the key-value part) or an **entity**.
The graph adds three tables and no per-kind table:

| table | one row | columns |
|---|---|---|
| `entity` | a thing that persists across items: a person, place, event, work, organisation | `id`, `kind`, `unit`, `name`, `aliases`, `attributes` (JSONB), `state`, `created_at`, `merged_into` |
| `relation` | one statement linking two nodes | `id`, `src_kind`, `src_id`, `rel`, `dst_kind`, `dst_id`, `layer`, `valid_from`, `valid_to`, `asserted_at`, `status` (current / superseded / disputed / retired), `written_by` (code projector, or adaptor + session id), `superseded_by`, `unit`, `persons`, `created_at` |
| `relation_evidence` | one link from a relation, or from one attribute of an entity, to what supports it | `relation_id` or (`entity_id`, `attribute`), `source_kind`, `source_id`, `role` (supports / contradicts), `span` (the exact text for a quote, when there is one) |

- **`kind` and `rel` are values.** A new entity kind, relation or attribute
  is a code change to the vocabulary (§5), never a migration. A migration is
  needed only for a new **lifecycle** (a new way state is created or
  corrected), which should be rare.
- **Attributes are JSONB, checked by a per-kind schema in code** (a place:
  address, category, area; an event: start, end, venue, performer). The
  database stops enforcing their shape; the write verb does, and an
  architecture test holds every writer to the schema.
  `# ponytail: JSONB attributes; promote one to a column only when a query plan needs it`.
- **Persons, rooms, units and Nora** keep their authored identity
  (`identity.toml`, `policy.toml`); their `entity` rows are pointers keyed by
  `person_id` / room / unit, so the graph never owns who someone is.
- **Scope travels on the row.** `relation.unit` and `persons` are copied from
  the evidence (the strictest of it), so a scoped read filters inside the
  scan, exactly as `memory_node` does (25 §3). An entity with no unit-scoped
  evidence is unreachable.

**Postgres, no graph database.** Walks are `WITH RECURSIVE` over
`relation (src_kind, src_id, rel)` and `(dst_kind, dst_id, rel)`. 25 §3's
reasons against a second database stand: a hide must drop a node and its
edges in one write. Apache AGE remains the option for patterns of three hops
or more, if a measured query needs one.

## 4. The layer is a column

Record, Rollup and Notebook are not separate graphs. They are the `layer` of
each relation, and the layer decides who writes it and how it is corrected,
by the rules the data model already has ([cognition-loop/05](../cognition-loop/05-data-model.md)):

| layer | written by | example relations | corrected by |
|---|---|---|---|
| **record** | code, from the source's own columns | `authored_by`, `in_room`, `attached_to`, `reply_to`, `reacted_to`, `shared_by`, `mentions` | never edited; a hide drops its node |
| **rollup** | code, computed from Record | `stayed_at` (person → place, interval), `co_located_with` (person → person, interval), `during` (item → outing) | rebuilt from Record, never by a model |
| **notebook** | a model writer (the research loop §7, a Character writer), with evidence | `attended`, `went_with`, `at`, `features`, `likes`, `plans`, `works_at`, `lives_in`, `same_as` | superseded by a later revision; disputed by the subject; never deleted |

A fetched web page is a **record** item, and what is learned from it is a
**notebook** relation or attribute citing it. Nothing in the notebook layer
is evidence for anything: its evidence rows may point only at record or
rollup sources (the `relation_evidence.source_kind` check). That is the rule
that stops a guess from citing itself and growing stronger: conclusions are
context for the next judgement, never its proof
([45](45-persona-adaptor.md) §4a: "her own earlier conclusion adds no
independent evidence").

So "Rin went with Kit" can exist twice: as a **rollup** relation
(`co_located_with`, 19:40–23:10, code) and as a **notebook** relation
(`went_with` the event, citing the co-location and Rin's message). Same table,
different layer, different trust, and the reader is told which (§12).

**Validity is bi-temporal.** `valid_from`/`valid_to` is when the statement is
true in the world (resolved against the message's time, unknown stays
unknown, [47](47-character-dimensions.md)); `asserted_at` is the evidence's
time; `created_at` is the build. A later contradicting statement closes
`valid_to` or supersedes; it never deletes (Graphiti's invalidation, [40](40-prior-art.md)).

## 5. The vocabularies

One named constant per list, each entry with its meaning and its allowed
ends; a write outside the list, or with ends of the wrong kinds, is refused by
code. A model may **propose** a missing relation in its session's output; a
human adds it to the list.

**Entity kinds:** `person`, `room`, `unit`, `nora` (pointers, §3), `place`
(the existing place and area hubs become entities), `event` (something that
happens at a time: a concert, a dinner, a trip, a launch), `work` (a thing
with an identity: a game, a show, an artist, a product, a song),
`organisation` (an employer, a school, a venue's operator, a team).

**The starting relations:**

| rel | ends | layer | means |
|---|---|---|---|
| `authored_by`, `in_room`, `attached_to`, `shared_by`, `reply_to`, `reacted_to`, `mentions`, `located_in`, `during`, `outcome_of` | as 25 §2 | record / rollup | as 25 §2 (moved into this store from `memory_edge`'s projection, which then reads them) |
| `stayed_at` | person → place | rollup | a stay at a known place, with its interval |
| `co_located_with` | person → person | rollup | the co-location episode, interval |
| `about` | Notebook entry, reading → entity | notebook | what the entry or reading is about |
| `records` | event entry (Register) → event entity | notebook | the Register entry that plans this event; says nothing about whether it happened |
| `plans` | person → event | notebook | intends to take part; from a plan statement or an RSVP |
| `attended` | person → event | notebook | took part. Needs evidence beyond the person's own plan: a statement that it happened, a stay or post at the venue in the window, another participant's account (§8) |
| `went_with` | person → person | notebook | took part in the same event together; cites the event and the co-presence evidence |
| `at` | event → place | notebook | where it happened |
| `features` | event → work, person, organisation | notebook | who or what the event was of (the artist of a concert, the game of a tournament) |
| `likes`, `dislikes` | person → work, place, event kind | notebook | a stated or repeatedly shown preference; the claim's speech act is kept (stated / said by another / shown) |
| `works_at`, `studies_at`, `lives_in`, `member_of` | person → organisation, place | notebook | 47's location-bearing claims (workplace, residence, regular places) as relations; the code-set `location` flag carries over (47, decided 2026-10-22) |
| `concerns`, `settles`, `serves`, `fulfils`, `cancels` | Notebook entry → entity, entry, ledger item | notebook | §6 |
| `same_as` | entity → entity | notebook | the two are one; §7 step 4 |

Adding a row here is a code change and a review; it is the only way the
graph grows a new kind of meaning.

## 6. Every store is a node

| store | kind | its relations |
|---|---|---|
| messages, edits, deletes, reactions | message, reaction | record edges (§5) |
| media attachments and their readings | media | `attached_to`, `shared_by`; a judgement may cite the attachment (the reading is its text) |
| links | link | `attached_to`, `shared_by`; the page the loop fetched is a `web_page` `attached_to` the link |
| social items, their media, tagged place, engagement counts | social, social_media, social_location | record edges; the loop reads them as items of their own (§7) |
| fetched web pages (new) | web_page | `about` entity; cited by judgements |
| presence stays, trips, outings, co-locations, daybook, rhythm, ties | stay, trip, outing (nodes); rhythm and ties read by key (25 §4) | rollup relations |
| response and act episodes | episode | `outcome_of` |
| Nora's sends, documents, generated images, tool results | send, document, image, tool_result | `authored_by` Nora; a tool result she cited is evidence like any Record item |
| computer sessions, music plays | session, play | record edges, `about` person |
| claims (the current readings) | reading | `attached_to` its messages, `about` entities; evidence for judgements, as Record items |
| event entries | event_entry | `records` the event entity; `in_room`; RSVPs as `plans` |
| reminders | reminder | `serves` the event entry or commitment; `about` the person reminded |
| notes | note | `concerns` an entity, event or thread; `cites` its evidence; `supersedes` |
| memos | memo | `settles` a ledger thread or decision; `cites` |
| ledger decisions, commitments, threads | decision, commitment, thread | `about` who decided or committed; `fulfils` / `cancels` from the message or event that resolved it |
| Character lines (profile, traits, hypotheses) | profile_line | `about` the entity; `draws_on` the relations it summarises |
| lessons, Standing | lesson, standing | `cites` the episodes they learned from |
| disputes, feedback | dispute, feedback | `disputes`, `feedback_on` |

**A Register item's state lives on its row** (open, settled, cancelled,
superseded); the graph reads its head. `records` from a cancelled event
entry stays, and reads as cancelled; whether the event happened is the
separate `attended` judgement with its own evidence ([45](45-persona-adaptor.md)
§4a: "a cancellation is not evidence that an event happened").

**Never in the graph:** Tension (code guarantees she cannot read it), doctrine
and config (Authored, served whole), raw presence fixes (read through their
episodes), and external file content (only the link,
[external-files/00](../external-files/00-design.md)).

## 7. The research loop

One loop perceives every item on arrival and writes the graph. It replaces
the single extraction call (18) and is built on the Cognition session's
machinery, not a new runtime: a Dreamer adaptor (`adaptor = "research"`) with
read-only tools from the catalogue (`RegistryToolSource.build`), the session
row as provenance, `fetch_watch`'s rule that a write may cite only what the
session was shown or fetched, a staged write validated by code, and one
settle or an explicit no-change.
Its rendered input is a declared occasion in context assembly, a `_BLOCKS`
table walked by `assemble` ([ADR 0021](../../docs/adr/0021-every-prompt-is-assembled-and-the-enforcer-finds-it.md), joined as
[context-assembly/04](../context-assembly/04-every-occasion.md) «How a new occasion joins»).

**What triggers it:** a **work item** per arrival, keyed by its source:
a message batch (as today's extraction queue, 20 messages), a social item, a
media reading landing late on an old message, a Rollup episode closing (an
outing, a co-location), a Register write. Each work item records the
**input hash** of everything it rendered (§10).

**Steps of one session:**

1. **Read every item fully.** The rendered input carries each message with
   its attachments' readings (the media join), its link with the page the
   preview or a fetch holds, its reply parent, and for a social item its
   caption, media readings, tagged place (under its presence gate, 25 §7a)
   and counts at read time. A video or a reel is read through the existing
   lanes (`media_fetch`, `instagram_read`, `instagram_search`, `x_read`,
   `x_search`, the video desk), never a second describer.
2. **Say what was said.** Readings, as today's claims: about whom, what, when,
   where, how (stated / said by another / planned / asked / reported), with the
   exact span. The span checks stay code; a span may now come from an
   attachment's reading or a social caption, cited as that item.
3. **Look up the graph.** `memory_search` / `memory_read` (§12) for the
   entities and nearby evidence the item names: the venue already known, the
   event already planned, the person's open commitment, last week's outing.
   This is the multiplier: a new item attaches to what exists.
4. **Resolve.** Attach to an existing entity, create one, or propose
   `same_as`. Merging is never destructive: `same_as` is a notebook relation
   with evidence, and a reader follows `merged_into` to the survivor; a wrong
   merge is undone by superseding it.
5. **Enrich** (§8), when an entity is under-specified and the item makes it
   worth knowing: what was on at a venue on a date, what a game or show is,
   where a named place is.
6. **Write.** One staged write: readings, entities, relations with layer
   `notebook`, attributes, each with evidence rows. Code refuses a relation
   outside the vocabulary, wrong ends, evidence the session never saw,
   evidence in the notebook layer, a span not in its source, or a scope wider
   than its evidence. Valid parts commit and refused parts are recorded with
   their reason (the drop rule of #318, kept).

**Budgets** (starting values, fixed before the spike, measured in it): 8 turns,
12 tool calls of which at most 3 web searches and 3 page fetches, 120,000
total tokens, 240 s. A spent budget settles what is valid; nothing is retried
past 5 attempts (`failed`, re-queued by an operator command, as now).

**Model:** the production runtime model (DeepSeek V4.1 Flash via OmniRoute).
V4 Pro remains a judge only.

## 7a. Who writes each relation, and when

Three writers, one per layer, and no relation has two:

| layer | writer | when | decides by |
|---|---|---|---|
| record | code: the store's own `project(kind, id)` (25 §7) | in the same `begin()` as the row's write | columns: a message's room and author, a link's message, a reply's parent. No judgement. |
| rollup | code: the Rollup's `apply` and the mobility writes | each Rollup tick, as an episode opens or closes | the episode row: a stay's place and interval, a co-location's two people |
| notebook | the research loop (§7) | per work item, on arrival; and when a work item's input hash changes (§10) | the model proposes, code admits (§7 step 6) |

Register rows with an explicit link column (a reminder's event entry, an
RSVP's event) are record-layer and projected by code. What a note or memo is
*about* is a judgement and goes through the loop. Character writers read
relations and write lines (§9); they never write a relation.

**When an entity is created.** Only as the end of a relation the same write
admits, with evidence. A name mentioned once with nothing said about it is a
reading, not an entity. So the graph never holds an orphan entity, and "what
to create" reduces to "what relation does this item support".

**How an entity is resolved (§7 step 4).** Code proposes candidates and the
model picks:

1. **Candidates are code.** Same kind, same unit scope, matched by exact
   alias, then trigram on name and aliases, then place proximity for places
   (the existing place hubs), then an event's date window and venue for
   events. At most 5 candidates are rendered, each with its attributes and its
   three strongest relations.
2. **The model returns** one of: a candidate's id, `new`, or `same_as` between
   two candidates, with the evidence for the choice.
3. **Code admits it:** a `new` whose name exactly matches a candidate's alias
   is refused as a duplicate. A person is never `new`; a person entity is a
   pointer to `identity.toml` (§3), and an unmapped name stays a reading.

**When a relation changes.** A later item never edits an earlier relation.
Its session sees the open relations in step 3, so a `plans` plus a later stay
at the venue yields a new `attended` with both as evidence, and a contradiction
closes `valid_to` or supersedes (§4). One change has no new item: an event
past its window with only `plans` becomes `unconfirmed`. That is code, a sweep
on the Rollup tick, not a model call.

`# ponytail: candidate matching is alias + trigram + place/date; an embedding match joins only if the spike's wrong-merge rate (§11) misses its gate`

## 8. Enrichment and its limits

**A fetched page is a Record item.** `web_page`: URL, fetch time, content
hash, the extracted text the session read (bounded), and the tool call that
fetched it. It is kept because a judgement that cites it must be
re-derivable; this is a page the loop read, not a file someone shared, so
external-files/00's rule (keep only the link of a shared file) is untouched.

**What it can establish:** attributes of a place, work, event or organisation
(the venue's address and category, the lineup on a date, the genre of a
game), and the `features` / `at` relations of a public event. **What it can
never establish:** anything about a private person. A page is never evidence
that someone attended; the person's own evidence is (§5, `attended`).

**Plan versus happened.** A plan statement makes `plans`. `attended` needs a
second kind of evidence in the event's window: a statement that it happened,
a stay or a post at the venue, or another participant's account. A plan with
nothing else stays a plan, and its event entity stays `planned` until the
window passes, then `unconfirmed`.

**Egress.** A query leaves the host for a third-party search provider
(`nora/infra/search`: Exa, Tavily), a real trust boundary. Queries name
events, places, works and organisations, never a private person's name or
handle, and pass the R-18/R-28 screen (no identifier, no coordinate) before
they leave, whatever `[content_checks]` is set to: a query is outbound, and
those two rules are never simplified away. "Who played <venue> on 24
October" goes out; "where was Rin on 24 October" never does. The screen is
code at the tool, refusing the call with a recorded reason, and the spike
measures it (§11).

## 9. Character cards read the graph

Phase 4's `character-*` writers ([00](00-intent.md) «Forward phases») read
the person's, room's or unit's **subgraph** instead of message pages: their
relations with evidence counts, open Register state and recent readings. A
profile line `draws_on` the relations it summarises and `cites` their
evidence. The card stays the bounded synthesis in every prompt
([18](18-extraction.md) «Character is the card»); the graph is the memory
behind it. One writer per tier still holds: the research loop writes entities
and relations, Character writers write lines, nobody writes both.

## 10. One backfill, built in time order

**Order:** oldest first, by evidence time, across every kind (messages,
social items, Rollup episodes, Register writes), so each session sees the
graph as it stood at its moment, as a live arrival would. Record and Rollup
relations are projected by code first, in full, because they are free and the
loop reads them.

**Incremental after it:** every work item stores the hash of its rendered
input. A later change that alters an item's input (a media reading landing,
an edit, a new signal kind joined into the render) re-queues **only the items
whose hash changes**; their new relations supersede the old ones. Adding a
signal kind therefore costs a run over the items it touches, never a second
full backfill. A vocabulary addition re-runs nothing by itself; a targeted
re-run over items that mention the new kind is an explicit operator command.

**The current 4,226 claims** are superseded by the loop's readings in the
backfill: 27% of their inputs change (§1), and one version of readings is
simpler than two. They stay as history; nothing reads them after the switch.

**Size, estimated** (the spike measures it): about 280 message batches, 490
social items, a few hundred Rollup episodes and Register writes; at the
budget ceiling about 40M tokens, at the expected median a third of that; a
few hundred searches and fetches.

## 11. Gates, fixed before the spike

A stratified sample (text-only, attachment-only, link, social item, and items
where an event spans several signals), run through the loop on a frozen
snapshot, judged by the precision judge used in 2026-10-27's spike and a
second judge from another vendor:

| measure | pass |
|---|---|
| reading precision (supported by the cited source) | ≥ 95% |
| reading recall, attachments and links included | ≥ 80% |
| judgement relation precision (supported by its evidence rows) | ≥ 90% |
| wrong merges (`same_as` joining two different things) | ≤ 2% of merges |
| enrichment: attribute supported by its page, and the page about the right entity | ≥ 95%, ≥ 90% |
| egress: a query naming a private person or carrying an identifier or coordinate | **0** (any is a FAIL) |
| work items failed after retries | < 5% |
| cost per work item, median and p95 | reported; the backfill budget is set from it |
| query p95 at depth 3 on the built graph | < 200 ms |

The switch gates are unchanged and come after: answerability and reply
replay ([45](45-persona-adaptor.md) §6, amended 2026-10-21), now over the
graph.

## 12. Querying: the answer is a subgraph

One retrieval service ([25](25-memory-graph.md) §4), extended to walk the
`relation` store as well as `memory_edge`:

1. **Scope**, computed per read, inside every scan (25 §4 step 1).
2. **Seed:** hybrid search over the index; exact references (the named
   person, the attached image, a place the question names); a time window
   ("last weekend" seeds the items and relations valid then); the speaker
   and the room.
3. **Expand:** hop by hop along **meaning** relations (the notebook and
   rollup layers, `located_in`), adaptive depth (25 §4 step 3: two hops until
   adaptive depth is measured better, ceiling 4). **Record relations are
   evidence pointers, not paths:** a walk never fans out from a person to
   every message they wrote (25 §4's hub rule, extended from hub nodes to
   structural relations). Filters: layer, validity at a date, relation.
4. **Return** nodes with their key fields and the relations among them, each
   with its layer, validity and evidence handles, ranked.

**Cost.** About 130 messages a day, some of them turns. A depth-3 walk over
indexed relations at tens of thousands of rows is milliseconds. Every walk
has a node cap, a per-relation fan-out cap, a depth cap and a
`statement_timeout`; one that hits a cap answers `partial`, one that times
out `unavailable`, never `nothing_found`. One walk per turn, reused by her
tool calls in that turn. Walk duration, node count and depth are metrics
(instruments measure the cost, not only the answer), and an architecture
test reads the walk's query plan for an index scan.
`# ponytail: live walks; precomputed neighbourhoods per entity if p95 passes 200 ms`.

## 13. Routing

The split of [25](25-memory-graph.md) §5 stands: **push** the fixed core
(the speaker's and room's cards, open notes, Standing), **pre-fetch** from
the message's own seeds by code (built, `PREFETCH_TOKENS` = 1,500), **pull**
by Nora's tools. A decision model that chose context parts was measured and
parked on 2026-10-12/13 ([context-plan/15](../context-plan/15-what-the-composer-decides.md):
yes to about 90% of parts, AUC 0.557).

The decision model the relevance gate is moving into may carry one more
output, **memory need** (`none` / `light` / `query`), replacing the
pre-fetch's unconditional run, **only if** it beats code routing on the
labelled journey slices: pre-fetch recall no lower, and fewer walks.
Until then code routes. When it routes, an unsure answer is `light`, never
`none`, because a wrong `none` is silent and a wrong `light` costs an indexed
read. This holds unless the decision model's memory-need output is measured
better than code on those slices.

## 14. Presentation: a preview, with depth behind tools

The pre-fetch block carries **structure and labels, not content**: every
node with its id, kind, short label, time and state; every relation with its
layer in plain words, its validity and its **evidence counts**; and whether
more is available. A model's input must explain itself (AGENTS.md): kind,
age, state and layer are what let her tell a cancelled plan from a fact.

```text
<memory query="Rin, concert, last Saturday" depth=2 more=available>
P1 person Rin
P2 person Kit
E1 event  "<artist> live" · 24 Oct 20:00
L1 place  <venue> · concert hall
N1 note   "Saturday plans" · open
P1 ─attended──▶ E1   judgement · 1 message, 1 stay, 1 story
P1 ─went_with─▶ P2   judgement · co-location
E1 ─at────────▶ L1   judgement · 1 web page
P1 ─stayed_at─▶ L1   computed  · 19:40–23:10
N1 ─concerns──▶ P1, P2
</memory>
```

Layer words: `said` (record reading), `computed` (rollup), `judgement`
(notebook), `found` (from a web page). Under the same 1,500-token budget.

**Two tools on the graph surface** ([DEC-316](../09-decision-index.md)'s one
surface):

| tool | does |
|---|---|
| `memory_search` (exists; its result changes) | seed and walk; returns the preview subgraph |
| `memory_read` (new) | given node ids: full fields; the evidence text behind a relation; or one more hop from a node |

Per-store verbs (`note_read`, `media_fetch`, `room_read`, `load_transcript`)
stay for what only they do. A tool change goes through [ADR 0015](../../docs/adr/0015-tool-surface-policy-v2.md)'s
routing measurement before and after; it ships only if tool selection does
not get worse.

**The risk:** a label read as a fact ("she attended") without checking. The
layer word and evidence count are on every relation, and the answerability
gate scores whether an answer is grounded, not only whether it is right.

## 15. Order of work

1. This node ratified, and the amendments it points from.
2. **Stores and projections:** `entity`, `relation`, `relation_evidence`;
   the vocabularies with their tests; code projection of record and rollup
   relations; the Register mapping (§6).
3. **The research loop and enrichment** (§7, §8), on new arrivals in shadow:
   it writes, nothing reads.
4. **The spike** on a frozen snapshot, against §11.
5. **One backfill** in time order (§10).
6. **Query, presentation and tools** (§12–§14), with the routing measurement.
7. **Character writers read the graph** (§9; [00](00-intent.md) phase 4).
8. **Answerability and reply-replay gates**, then the switch, the operator's
   call ([70](70-honcho-cutover.md)).

Steps 2 and 3 are one integrated change measured once by the spike
(AGENTS.md «Build the least that works»), not piecewise PRs with nothing
measured between them.

## 16. Decisions this node needs

1. **The graph owns entities and relations** (§2, first row). Recommended:
   yes; it is the premise of everything here. Operator, 2026-10-28: the
   direction is confirmed; ratification is this node's.
2. **A fetched page's extracted text is kept** (§8). Recommended: yes,
   bounded, because a judgement citing it must be re-derivable.
3. **Queries never name a private person** (§8). Recommended: yes, enforced
   by code at the tool.
4. **The current claims are superseded in the backfill** (§10). Recommended:
   yes.
5. **Stories.** A story is never opened in a turn nobody asked for
   ([instagram-adoption/18](../instagram-adoption/18-instagram-read-and-search.md)).
   The loop reads only what the social stream already polled and perceived;
   opening a story from a shared link in the loop is that rule's case and
   stays refused unless the operator changes it.

**Ratified (operator, 2026-10-28):** yes to all five, as recommended.

## The reading that would reopen it

- The spike's judgement relation precision under 90% after one fix round:
  then judgements stay Character lines (25 §2's refusal stands) and the graph
  keeps only record and rollup relations plus entities.
- Wrong merges above 2%: entity resolution moves out of the loop into a
  separate, judged pass.
- Query p95 over 200 ms at depth 3 with precomputed neighbourhoods: a graph
  engine (AGE first) is measured.
- The answerability gate shows the subgraph preview answering no better than
  readings alone: the relations are not earning their cost, and the loop's
  enrichment and resolution steps are cut back to readings.
