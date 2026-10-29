---
type: design
title: "The memory is a graph: entities, relations and evidence over every store"
description: "Make the memory itself a graph: persistent entities, layered relations with evidence and validity, every store as a node, one Cognition loop that comprehends every signal on each stimulus, enriches from the web, reshapes profiles and decides, a subgraph-returning query with code routing and a preview presentation, and one time-ordered backfill."
timestamp: '2026-10-28'
edges:
  - {dst: 00-intent, rel: extends, provenance: asserted}
  - {dst: 15-memory-map, rel: extends, provenance: asserted}
  - {dst: 18-extraction, rel: supersedes, provenance: asserted}
  - {dst: 25-memory-graph, rel: extends, provenance: asserted}
  - {dst: 40-prior-art, rel: depends_on, provenance: asserted}
  - {dst: 45-persona-adaptor, rel: extends, provenance: asserted}
  - {dst: 47-character-dimensions, rel: depends_on, provenance: asserted}
  - {dst: ../cognition-loop/30-cognition, rel: extends, provenance: asserted}
  - {dst: ../cognition-loop/10-tiers, rel: extends, provenance: asserted}
  - {dst: ../culture-tier/00-overview, rel: extends, provenance: asserted}
  - {dst: ../self-growth/40-lessons, rel: extends, provenance: asserted}
---

# The memory is a graph

Status: **ratified** by the operator, 2026-10-28 (all seven decisions in §16). Docs only: no schema, verb, tool or
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
| extraction is one model call per batch over text | 18 | **step 1 of the Cognition loop** per stimulus, over every signal (§7) | measured above: 27% of messages carry content the single pass never saw |
| the Character tier has its own writers (Character / profile adaptors), each on its own clock | [cognition-loop/10](../cognition-loop/10-tiers.md) §1, [45](45-persona-adaptor.md) | **step 2 of the Cognition loop** reshapes traits and guesses on every stimulus that changed the entity; facts are rendered by code from the graph; the separate adaptors retire | operator, 2026-10-28: a profile must move when the evidence moves, not on a nightly clock; three loops waking on one item read it three times, against *interpret once* |
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
  (`identity.toml`, `policy.toml`); their `entity` rows are pointers keyed by `person_id` / room / unit, so the graph never owns who someone is. A pointer is **one entity per unit** (`<unit>:<kind>:<id>`, 2026-10-28): an entity's unit never changes, so a person in two units is two entities and neither unit's graph reaches the other's (DEC-231).
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
| **notebook** | the Cognition loop (§7), with evidence | `attended`, `went_with`, `at`, `features`, `likes`, `plans`, `works_at`, `lives_in`, `same_as` | superseded by a later revision; disputed by the subject; never deleted |

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
`organisation` (an employer, a school, a venue's operator, a team). Amended 2026-10-29 (§6b): `topic` (a subject people keep returning to:
racing, not one race).

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
| `likes`, `dislikes` | person → work, place, event | notebook | a stated or repeatedly shown preference for one specific thing; the claim's speech act is kept (stated / said by another / shown). Amended 2026-10-29 (§6b): never a subject; that is `interested_in` |
| `interested_in` | person → topic | notebook | added 2026-10-29 (§6b): the person keeps returning to the subject; its weight is its evidence, counted by code |
| `works_at`, `studies_at`, `lives_in`, `member_of` | person → organisation, place | notebook | 47's location-bearing claims (workplace, residence, regular places) as relations; the code-set `location` flag carries over (47, decided 2026-10-22) |
| `concerns`, `settles`, `serves`, `fulfils`, `cancels` | Notebook entry → entity, entry, ledger item | notebook | §6 |
| `same_as` | entity → entity | notebook | the two are one; §7 step 1.4 |

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

## 6a. What this meets in the other bundles (2026-10-28)

Four bundles written in the last ten days meet this graph. None of them is
built, so each conflict is settled here, in design, before any of them ships.

**Private DM ([private-dm](../private-dm/00-contract.md), proposed, not built).**
The operator's rulings stand unchanged: one profile per person per unit, with
no private profile (00 §1, 2026-10-21); a DM's raw history is private Record
anchored to the person and their default unit (02 §1); only *curated*
understanding is published into the unit (00, 03 §5); a DM may read every unit
that lists its owner, and the laundering rule holds (06, 2026-10-25). In the
graph that means:

- **Per-unit pointers already fit:** a person is one entity per unit
  (`pointer_entity`, §3), the shape 00 §1 rules. A DM's view of its owner is
  **composed at read time**, one walk per unit that lists them, each through
  06 §3's per-room predicate. No cross-unit profile or relation is ever
  written: a stored one would be read back inside some unit, carrying one unit's understanding into another, which the laundering rule forbids.
- **A private scope is needed, and is missing.** Step 1 comprehends DM items
  like any item, but what it derives only from private DM evidence must be readable only by the owner's own DM and by the bound curation session that 02 §4 allows to read the private scope (an explicit internal unit-curation capability), under 03 §3's limits on what that session may write. `persons` cannot carry
  that: it admits a relation wherever every person it names is listed, so a
  fact about Rin from her DM would surface in any group room that lists her.
  The DM build adds an explicit private scope to `relation` and `entity`
  attributes (owner and DM scope id), filtered inside the scan like `unit`, and
  the strictest-evidence rule (§3) makes anything citing private evidence
  private. No DM data exists yet, so nothing migrates.
- **Curated publication is step 2 or step 3, with 03 §5's check.** Curating DM understanding into the unit's profile (00, 02 §2) is now step 2
  (profile lines) or step 3 (Notebook) writing a unit-scoped item whose
  evidence is the curation decision, never the private item itself; private
  evidence cannot make a unit-scoped relation, by §3's rule.

**Standing ledger ([standing-ledger](../standing-ledger/00-intent.md), proposal).**
Decisions, commitments and threads are already §6's nodes. Two points meet:

- **Ledger links are code, not a writer's judgement.** Ledger entries carry
  typed references: a commitment's `owed_to` and the citation its `Done as`
  line must carry (20 §3, under 12-schemas' cited-line grammar), a thread's
  `note_ids`, `decision_ids` and code-computed spans (20 §4). Their `about`, `fulfils` and `concerns` links are built by code from those fields and staged through `stage_and_admit` in the **notebook** layer, as §5 requires, with the rows the entry cites as their evidence: the judgement is the ledger writer's, already made when it cited them, and code only converts it. This keeps the tag-path op tool (the ledger's migration step 3, a reply-turn tool) from calling the graph itself.
- **A turn record is a Record item** ([30](../standing-ledger/30-turn-records.md)):
  `turn` joins the Record node kinds with the ledger's build, so a ledger line
  citing `turn(...)` is walkable and a turn can be step 1 evidence like any
  tool result she cited.

**Personalisation ([personalisation](../personalisation/00-intent.md), proposal).**
Its likes are "a read view of profile facts, traits and guesses", plus a new
constraint reader with its own interval extraction over messages (10 §1). Both
now read the graph:

- **Likes** read the `likes` / `dislikes` relations step 1 writes, with their
  evidence and speech act (stated / said by another / shown), and step 2's
  traits and guesses; revealed preference reads `stayed_at`, `attended` and
  play. The α/β scoring of 10 §1 and the decay of 10 §3 apply to those rows unchanged.
- **Constraints and dated plans** read `plans`, `at`, and relations with
  validity intervals. The separate constraint reader and its interval
  extraction (10 §1, 60) are superseded by step 1: building them would read
  every message a second time, against *interpret once*.
- **Lessons** (40) are step 2's now, not the Lessons adaptor's; the rule that
  the engine neither writes nor reads them is unchanged.

**Room topology ([room-topology](../room-topology/00-intent.md)).** Addressing
reads a room's culture; culture is now written by step 2 (§9) into the same
store, so the reader is unchanged. No conflict.

**Agent worker and computer unit scope:** no conflict. An agent-worker session becomes a turn-record occasion (its 50, step 6), so it reaches the graph as a `turn` node like any turn; computer-unit-scope already scopes the `session` nodes of §6 to the unit. No change to either.

## 6b. The Notebook by memory type (2026-10-29)

[notebook-memory-types](../notebook-memory-types/00-design.md) (#371)
reorganised the Notebook into five kinds and made a person's interest a graph
edge. Three consequences for this graph, settled here:

- **A topic is an entity, and an interest is one relation.** `topic` joins the
  entity kinds and `interested_in` (person → topic) the relations (§5).
  `likes`/`dislikes` narrow to one specific work, place or event, so "likes
  racing" has one home and never two that drift
  ([10](../notebook-memory-types/10-relations.md) §3). Operator, 2026-10-29.
- **Topic activity is counted from judged relations, so it is not Rollup.**
  Which messages are about racing is a step-1 judgement (`interested_in`, and
  readings `about` the topic), and the Rollup is rebuilt from Record by code
  with no model. The interest's weight is therefore its own evidence rows,
  counted by code at read (the walk's evidence count and the asserted range:
  who, how often, last moved). This amends the bundle's "activity is Rollup"
  (00 §3, 10 §3) in where it lives, not in what it is; a separate topic-activity
  builder is not needed until a read is measured needing more than the counts.
- **The old Notebook kinds stay in the vocabulary until the migration node.**
  `memo`, `standing`, `thread`, `commitment` and `event_entry` remain entry
  kinds (§6) and keep their relations (`settles`, `fulfils`, `cancels`,
  `records`) until each store moves onto the one entry. Step 1 writes no
  entries, so the backfill is unaffected; it does need `topic`, which is why
  that lands first.

**Measured 2026-10-29, spike run 5:** step 1 admitted no notebook relation and
no entity from 78 items: 231 relations were refused as outside the vocabulary
(`shared`, `asked_about`, `bought`, …), and their entities and evidence fell
with them as orphans. The instruction never named the vocabulary. It now lists
the proposable kinds and relations, rendered from the same rules admission
checks (`comprehend_render._VOCABULARY`), so the two cannot drift.

## 7. One Cognition loop: comprehend, measure, serialize

> **Superseded 2026-10-29 by [29](29-one-cognition-session.md)** (operator):
> one Cognition session per wake comprehends, connects and decides as one act;
> the three separate step sessions below, the per-item runner and "comprehend
> on arrival" are retired. §14's tool names are superseded by 29 §4. The
> stores, layers, vocabulary and admission of this node stand.

**Operator, 2026-10-28:** everything said and everything done keeps shaping
the memory and the profiles, so the Cognition loop is the one driver. Every stimulus (a message, a story, a stay, a state change) drives steps 1
and 2 on arrival; step 3 follows on the wake rules. Each step is a bounded model session of its own, with its own
focused input: one owner and one order, never one prompt asked to read,
profile and plan at once. The steps are CMS one level up:

| step | runs | CMS | job | writes |
|---|---|---|---|---|
| 1. comprehend | **on each arrival**, per work item | comprehend | read every new item once, resolve, relate, enrich | readings, entities, notebook relations |
| 2. reshape | right after a step 1 that changed relations | measure | for each entity step 1 changed: what changed in what it is like, including a room's or unit's culture and Nora's lessons (§9) | traits and guesses ([ADR 0029](../../docs/adr/0029-a-prompt-block-names-its-subject-and-author-by-id.md)'s parts), culture baselines, lessons; facts are code (§9) |
| 3. decide and act | on the existing wake reasons (`settled`, `strong_cue`, `batch`, `max_age`, `time`, and `self`, raised by `wake_at`) | measure, serialize | what needs doing: notes, memos, events, reminders, Standing, or a re-read of a social item that looks wrong | Register items with their own links (§7a), Standing |

Every step ends in a staged write that code admits (serialize), and
`serialize(N)` is `comprehend(N+1)`: the next wake reads the graph this one
wrote. The three steps read one another only through committed state, never
by waiting on each other.

**Operator, 2026-10-28: comprehend on arrival.** Steps 1 and 2 do not wait
for a wake, so the graph and the profiles are current within one work item of
an arrival, not after 30 minutes of quiet. Step 3 keeps the wake rules,
because deciding whether to act benefits from a settled room; when it runs,
the graph it reads is already up to date.

**Seven rules the merge must keep:**

1. **Step 1 is complete; step 3 is selective.** Every item is comprehended
   exactly once, never capped or deferred by surprise; a skipped item is a
   hole in the graph. Only step 3 keeps Cognition's agenda cap
   (`max_items`, ordered by surprise, the rest deferred,
   `nora/domain/cognition_session.py`). Step 3's agenda carries each item's
   readings and preview subgraph (§14), so it decides on step 1's reading,
   never a second reading of the raw text.
2. **Acting is a switch.** The backfill (§10) runs steps 1 and 2 with step 3
   off: two months of history never creates a note or a reminder.
3. **A profile moves when its graph moved.** Step 2 runs only for entities
   whose relations step 1 changed in this work item; an "ok 👍" rewrites no card.
4. **No step grades itself.** A guess is graded by a judge other than its
   writer ([45](45-persona-adaptor.md)), outside the loop.
5. **A step 1 work item is never dropped.** Each work item has its own state
   and input hash (§10), independent of wakes: an agenda-failed wake's stimuli may be struck for step 3 after
   `max_consecutive_failures` (infrastructure failures never strike, DEC-244), but a work item that
   fails is retried and, past 5 attempts, parked `failed` for the operator's
   re-queue, never discarded. A missed item is a hole in the graph.
6. **Each step commits on its own.** A step 3 failure never rolls back step 1
   or 2; step 3 re-reads committed state on its next wake.
7. **The backfill has its own lane.** It runs steps 1 and 2 in a separate
   worker with its own concurrency limit, so it never takes a slot from live
   arrivals or live wakes (`max_concurrent_sessions`).

A re-read in step 3 uses the existing read lanes and the story rule of §16.5:
it re-reads what the social stream already perceived, never opens a story
nobody asked for.

It is built on the Cognition session's machinery, not a new runtime: the
`cognition` adaptor with read-only tools from the catalogue
(`RegistryToolSource.build`), the session row as provenance, `fetch_watch`'s
rule that a write may cite only what the session was shown or fetched, a
staged write validated by code, and one settle or an explicit no-change per
step. Each step's rendered input is a declared occasion in context assembly,
a `_BLOCKS` table walked by `assemble` ([ADR 0021](../../docs/adr/0021-every-prompt-is-assembled-and-the-enforcer-finds-it.md), joined as
[context-assembly/04](../context-assembly/04-every-occasion.md) «How a new occasion joins»).

**What step 1 works on:** a **work item** per arrival, keyed by its source:
a message batch (as today's extraction queue, 20 messages), a social item
(every item the social poller records, as today's `social` stimulus: a
member's post, story or reel; ingestion itself stays outside the loop), a
media reading landing late on an old message, a Rollup episode closing (an
outing, a co-location), a Register write. Each work item records the
**input hash** of everything it rendered (§10).

**What the graph already knows is pushed, not searched for** (operator,
2026-10-28). Before the model runs, code builds the work item's **graph
preview**, the same push / pre-fetch / pull split the reply turn has (§13):

- **Seeds, by code, from the item alone:** its author, room and reply parent;
  its mentions, and known names or aliases found in its text (a scan against
  the unit's entity aliases); its link's URL or account; faces already
  labelled in its media and a social item's tagged place; and its time window
  ("Saturday" resolved against the item's own time, §10).
- **Expansion:** the retrieval service's walk from those seeds (§12, the same
  scope, caps and hub rule): each seed's entity with its key attributes, its
  open relations (plans, commitments, upcoming events), the last readings in
  the same thread, and the resolution candidates of §7a.
- **Rendered** as the first block of step 1's input, in §14's preview format,
  within a fixed token budget (a starting value, measured in the spike), with
  `more=available` when a cap was hit.

The model's own `memory_search` / `memory_read` calls are then for what code
cannot seed: a name the graph has never held, a vague reference ("that place
last time"), or one more hop. The same preview is carried to step 3's agenda
(§7 rule 1), so the graph context is built once per item.
`# ponytail: the alias scan finds only names the graph already holds; new names fall to the model's tools, and the spike's recall row (§11) says whether that gap matters`

**Step 1, in order:**

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
3. **Read the graph preview, and pull more if needed.** The preview (above)
   already holds the entities and nearby evidence the item names: the venue
   already known, the event already planned, the person's open commitment,
   last week's outing. `memory_search` / `memory_read` (§12) go further.
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

**Step 1's budgets** (starting values, fixed before the spike, measured in it):
rounds, tokens and time only, as every Cognition session (DEC-247): 8 turns,
120,000 total tokens, 12,000 output tokens, 240 s. The one per-tool cap is on the paid, outbound
lookups: at most 3 web searches and 3 page fetches per work item, held by the
search tools themselves, because each is a third-party call that leaves the
host (§8), a consequence DEC-247's reasoning did not cover. A spent budget settles what is valid; nothing is retried
past 5 attempts (`failed`, re-queued by an operator command, as now).

**Model:** the production runtime model (DeepSeek V4.1 Flash via OmniRoute).
V4 Pro remains a judge only.

## 7a. Who writes each relation, and when

Three writers, one per layer, and no relation has two:

| layer | writer | when | decides by |
|---|---|---|---|
| record | code: the store's own `project(kind, id)` (25 §7) | in the same `begin()` as the row's write | columns: a message's room and author, a link's message, a reply's parent. No judgement. |
| rollup | code: the Rollup's `apply` and the mobility writes | each Rollup tick, as an episode opens or closes | the episode row: a stay's place and interval, a co-location's two people |
| notebook | the Cognition loop: step 1 for what it reads, step 3 for the Register items it writes | step 1 per work item, on arrival and when a work item's input hash changes (§10); step 3 right after its note, memo or event commits | the model proposes, code admits (§7 step 1.6) |

Register rows with an explicit link column (a reminder's event entry, an
RSVP's event) are record-layer and projected by code. **Whoever writes an
item writes its own links, right after it, in the same session:** Cognition writes a note
together with its `concerns`, a memo with its `settles`, an event entry with
its `records`, each with evidence, admitted by the same code checks. No item is linked later by a second writer. The Register row commits first and is the source of truth; its links follow through `stage_and_admit` (2026-10-28: the Register stores commit in their own transactions and the graph port has none to share). A link write that fails is never dropped: every Register write is also a step 1 work item (§7), which retries it and parks it `failed` past 5 attempts, as [25](25-memory-graph.md) §7 already rules for a projection that fails. Step 2 reads relations and writes
profile lines (§9); it never writes a relation.

**When an entity is created.** Only as the end of a relation the same write
admits, with evidence. A name mentioned once with nothing said about it is a
reading, not an entity. So the graph never holds an orphan entity, and "what
to create" reduces to "what relation does this item support".

**How an entity is resolved (§7 step 1.4).** Code proposes candidates and the
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

## 9. Profiles are reshaped in the loop, from the graph

A profile card keeps its three parts ([ADR 0029](../../docs/adr/0029-a-prompt-block-names-its-subject-and-author-by-id.md)):

- **facts** are **rendered by code** from the entity's current relations
  (works at, lives in, likes, upcoming plans), ranked by recency and evidence
  count within the card's bound. No model writes them, so the card can never
  disagree with the graph; a poor choice is fixed by the ranking rule, not
  by a second copy.
- **traits** and **guesses (unconfirmed)** are written by step 2 (§7) when
  the entity's relations changed, each line `draws_on` the relations it
  generalises and `cites` their evidence. A guess is graded by a judge other
  than its writer.

The card stays the bounded synthesis in every prompt ([18](18-extraction.md)
«Character is the card»); the graph is the memory behind it. The separate
`character-*` and `profile-*` adaptors retire when step 2 is live; their
write verbs move to the Cognition loop.

**Culture and lessons are step 2 too** (operator, 2026-10-28). The graph is
cross-connected: a person's change can change the room they talk in, and a
change in how people answer Nora changes what she has learned. So one step 2
pass reshapes, in this order, the **persons** step 1 changed, then the
**rooms and units** they belong to (their culture: the room's baselines, what
it normally picks up, [culture-tier](../culture-tier/00-overview.md)), then
**Nora** (her lessons, from closed `response` episodes, which arrive as Rollup
work items, [self-growth/40](../self-growth/40-lessons.md)). A room or unit
is reshaped only when a member's or the room's relations changed in this pass;
Nora only when a response episode closed. The `culture` and `lessons`
adaptors retire when step 2 carries them; `lesson_write` / `lesson_retire`
and the culture writes move to step 2, and their readers (the
Character sessions' room-culture block, the tag path's `<culture>` and
`<unit_culture>` prefill, the lessons block) read the same stores as now.

## 10. One backfill, built in time order

**Order:** oldest first, by evidence time, across every kind (messages,
social items, Rollup episodes, Register writes), so each session sees the
graph as it stood at its moment, as a live arrival would. It runs steps 1
and 2 with step 3 off (§7 rule 2), in its own lane (§7 rule 7). Record and Rollup
relations are projected by code first, in full, because they are free and the
loop reads them.

**The clock is the item's.** Steps 1 and 2 use the same instructions live
and in the backfill; what differs is the input: each work item renders "now"
as its own evidence time, the graph as of that time, and enrichment as of
that time. "Going to the concert on Saturday", read in the backfill, resolves
against the week it was said, not against today. The model's input states
its time; no instruction is rewritten for the backfill.

**A catch-up wake closes it.** History that is still live (a future event, an
unfulfilled commitment, an open plan) would never reach step 3 with acting
off. When the backfill finishes, one step 3 wake per unit runs on the real
clock over what code selects as still open in the graph: relations with no
`valid_to` whose event or due time is in the future, and open commitments.
Everything older stays memory only.

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
few hundred searches and fetches. That counts step 1 only: step 2's reshape
sessions come on top, one per changed entity per wake, and the spike measures
how many and at what cost before the backfill budget is set.

## 11. Gates, fixed before the spike

A stratified sample (text-only, attachment-only, link, social item, and items
where an event spans several signals), run through steps 1 and 2 of the loop on a frozen
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
| notes, reminders or sends created during the backfill | **0** (any is a FAIL) |
| a work item lost (neither settled nor parked `failed`) | **0** (any is a FAIL) |
| cost per work item, median and p95, step 1 and step 2 separately; step 2 sessions per work item | reported; the backfill budget is set from it |
| query p95 at depth 3 on the built graph | < 200 ms |

**The AGE comparison (operator, 2026-10-28).** The spike also loads its built
graph into Apache AGE (1.8.0 supports the production Postgres 18) and runs one
frozen question set both ways: code-templated walks over `relation`, and
Cypher. It measures p95 latency at depth 3, and how often the production model
writes a correct query (right answer, unit filter held) in Cypher versus a
structured path tool compiled to SQL. AGE is adopted before the backfill if it
is within the latency gate and clearly more often correct; otherwise
`relation` stays and AGE stays the option. Either way the unit filter is code,
never the model's query. Cost recorded with the result: a custom database
image, AGE's own schema for links (Cypher cannot read `relation`), and
Postgres upgrades waiting on AGE.

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
3. **The Cognition loop's three steps** (§7, §8, §9): steps 1 and 2 on new
   arrivals in shadow (they write, nothing reads), with code-rendered facts;
   step 3 writes its Register items' own links (§7a). Steps 1 and 2 exist
   before the spike because the backfill runs both (§10).
4. **The spike** on a frozen snapshot, against §11, with the AGE comparison;
   AGE is decided here, before the backfill.
5. **One backfill** in time order (§10).
6. **Query, presentation and tools** (§12–§14), with the routing measurement;
   step 3's agenda reads step 1's output (§7 rule 1).
7. **Cards read from step 2 and the graph** (§9; [00](00-intent.md) phase 4);
   the separate profile adaptors retire.
8. **Answerability and reply-replay gates**, then the switch, the operator's
   call ([70](70-honcho-cutover.md)).

Work items 2 and 3 are one integrated change measured once by the spike
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

6. **One Cognition loop** (operator, 2026-10-28, ruled): comprehension,
   profiles and decisions are three ordered steps of one loop per stimulus
   (§7); the Character tier's separate writers retire, and its facts are
   rendered by code from the graph (§9).
7. **Comprehend on arrival; culture and lessons in step 2** (operator,
   2026-10-28, ruled): steps 1 and 2 run per arrival, step 3 on the wake
   rules (§7); the `culture` and `lessons` adaptors retire into step 2 (§9);
   social ingestion stays outside the loop and every recorded item is a step 1
   work item (§7).

## The reading that would reopen it

- The spike's judgement relation precision under 90% after one fix round:
  then judgements stay Character lines (25 §2's refusal stands) and the graph
  keeps only record and rollup relations plus entities.
- Wrong merges above 2%: entity resolution moves out of the loop into a
  separate, judged pass.
- Step 1 falls behind arrivals (a backlog that grows across a day): step 1
  gets more workers, still owned and ordered by the loop.
- Query p95 over 200 ms at depth 3 with precomputed neighbourhoods: a graph
  engine (AGE first) is measured.
- The answerability gate shows the subgraph preview answering no better than
  readings alone: the relations are not earning their cost, and the loop's
  enrichment and resolution steps are cut back to readings.

Loose ends noted, not blocking: no `play` stimulus arrived in the 7 days to
2026-10-28 (the producer may be idle), and `wake_at` was used once in those
7 days (172 sessions).
