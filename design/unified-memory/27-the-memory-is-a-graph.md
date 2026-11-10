---
type: design
title: "The memory is a graph: entities, relations and evidence over every store"
description: "Detailed graph contract: entities, evidenced temporal relations, ownership, admission, resolution, enrichment and bounded subgraph reads. Current session, profiles and acceptance have named owners."
timestamp: '2026-10-28'
edges:
  - {dst: 00-intent, rel: extends, provenance: asserted}
  - {dst: 15-memory-map, rel: extends, provenance: asserted}
  - {dst: 18-extraction, rel: supersedes, provenance: asserted}
  - {dst: 25-memory-graph, rel: extends, provenance: asserted}
  - {dst: 40-prior-art, rel: depends_on, provenance: asserted}
  - {dst: 45-persona-adaptor, rel: extends, provenance: asserted}
  - {dst: 47-character-dimensions, rel: depends_on, provenance: asserted}
  - {dst: ../cognition-loop/session.md, rel: extends, provenance: asserted}
  - {dst: ../cognition-loop/data.md, rel: extends, provenance: asserted}
  - {dst: ../culture-tier/00-overview, rel: extends, provenance: asserted}
  - {dst: ../self-growth/40-lessons, rel: extends, provenance: asserted}
---

# The memory is a graph

This document owns the detailed graph model: stores, layer/evidence rules,
reviewed vocabularies, entity resolution, enrichment and bounded traversal.
[The memory map](15-memory-map.md) explains the current topology and profiles;
[one Cognition session](29-one-cognition-session.md) owns the current loop;
[build acceptance](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31)
owns delivery order and evidence. This contract does not establish live activation.

## What the operator asked (2026-10-28)

Connect every eligible signal and state, beyond text-only extraction. Persistent
entities and evidenced semantic relations let a new experience attach to prior
understanding. A query returns a subgraph. One time-ordered backfill uses the
same understanding path, with acting withheld.

## 1. What exists, measured 2026-10-28

The motivating audit found text-only extraction despite existing media readings,
and a graph dominated by authorship/attachment bookkeeping rather than meaning.
Its raw counts and superseded architecture comparison resolve in git history.
Current delivery evidence belongs to [31](31-build-one-session.md), not those
historical counts.

## 2. What this supersedes, and why the old reasons are answered

Source stores retain ownership; the graph additionally owns entities, semantic
relations and their evidence. The index remains derived. Relations are values in
a reviewed code vocabulary, not per-relation migrations. Model judgements are
admitted only with shown evidence, scope, validity and writer provenance.
Fetched pages are retained as citable Record items. The old separate-step
schedule is replaced by [29](29-one-cognition-session.md); scope, evidence and
source-deletion rules remain as specified below.

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
by the rules the data model already has ([cognition-loop/data.md](../cognition-loop/data.md)):

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
| `same_as` | entity → entity | notebook | the two are one; §7a |

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
| lessons | lesson | `cites` the episodes they learned from |
| Standing | existing scoped presentation | derived from completed Records and canonical state under [one Standing](../notebook-memory-types/20-standing.md), not an independent evidence source |
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

- **Private DM:** [private-dm](../private-dm/00-contract.md) owns one profile per
  person per unit, private Record and curated publication. An owner's DM view
  composes authorized unit reads at read time; no cross-unit profile/relation
  is stored. The DM build requires explicit owner/DM scope on relations and
  entity attributes: `persons` alone cannot protect private evidence. Only the
  owner's DM and the explicitly bound curation session may read it. The
  strictest-evidence rule prevents private evidence directly grounding a
  unit-visible claim; authorized curated publication follows that bundle's
  curation-decision contract. This remains a separate build, not permission
  granted by sharing a unit.
- **Standing:** [one Standing](../notebook-memory-types/20-standing.md) owns
  the automatic faithful updater and common presentation. Full turn Records
  and canonical execution state retain provenance. Typed references project
  their links; a summary does not acquire substantive judgement authority.
- **Personalisation:** [personalisation](../personalisation/00-intent.md)
  reads likes/dislikes with speech act and evidence, relevant traits/guesses,
  and plans/constraints with validity. Revealed preference reads stays,
  attendance and play. Its existing scoring/decay rules remain; a second
  extraction of the same conversation is not introduced. Its engine neither
  writes nor reads Lessons under its existing contract.
- **Room topology and computer work:** addressing reads room culture; retained
  worker turns and computer sessions join Record under their existing unit
  scope. No additional identity or truth writer is introduced.

## 6b. The Notebook by memory type (2026-10-29)

[Notebook kinds](../notebook-memory-types/00-design.md) owns the five target
entry kinds and their lifecycles; [the memory map](15-memory-map.md) owns their
place across layers. The following graph consequences remain:

- `topic` is an entity. `interested_in` connects a person to a recurring subject;
  `likes`/`dislikes` concern a particular work, place or event.
- Topic activity is counted from judged relations' evidence, with actual
  assertion times; it is not an independent Rollup judgement. A separate
  activity builder needs a measured consumer that the counts cannot serve.
- Legacy `memo`, `standing`, `thread`, `commitment` and `event_entry` kinds
  remain until explicit migrations preserve their semantics and links.
- The instruction's proposable vocabulary is rendered from the same named
  rules admission checks; a schema that the model was never shown is not
  sufficient guidance.

## 7. One Cognition loop: comprehend, measure, serialize

[One Cognition session](29-one-cognition-session.md) is the sole owner of the
session flow. One wake comprehends the agenda, connects admitted understanding,
maintains affected profiles and decides whether to act. There is no separate
per-item driver, reshape session or comprehend-on-arrival schedule.

Context assembly supplies structural graph anchors, resolution candidates,
source bodies, related profiles and open affairs before the model runs. Further
reads use the same scoped service. Only emitted or explicitly fetched evidence
is citable. Statement-shaped proposals, per-input completion, refusal repair,
propose-before-act, budgets and retry/parking ownership follow 29; their older
three-step versions are retired, not additional requirements.

## 7a. Who writes each relation, and when

Three owners, one per layer:

| Layer | Writer and timing |
|---|---|
| Record | The source store's code projector, in the source write transaction |
| Rollup | Code from the computed episode or aggregate during its update |
| Notebook | Cognition proposes understanding and selected Register associations; code admits it in the current wake session |

A typed source reference is projected from its columns. A semantic association
retains the session's shown selection and evidence. Each Register writer links
its own item after the authoritative row commits; failure retries the same row
and act key, never a duplicate. [29](29-one-cognition-session.md) owns completion
and parking. Profile curation draws on admitted relations rather than inventing
another independent relation writer.

**When an entity is created.** Only as the end of a relation the same write
admits, with evidence. A name mentioned once with nothing said about it is a
reading, not an entity. So the graph never holds an orphan entity, and "what
to create" reduces to "what relation does this item support".

**How an entity is resolved.** Code proposes candidates and the
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
Its session sees open relations in context or through reads, so a `plans`
plus a later stay at the venue yields a new `attended` with both as evidence, and a contradiction
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

[The memory map](15-memory-map.md#profiles-understanding-people-and-their-social-context)
owns the conceptual profile contract: facts, traits and guesses (unconfirmed),
subject/author attribution, individual/collective/Nora understanding, and its
relation to situation and practice. Facts are rendered from current graph
relations; judged traits and guesses retain relation premises and source evidence.
The entity remains the hub; its profile is a bounded synthesis.

[29](29-one-cognition-session.md) owns in-session profile maintenance and
[31](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31) owns the
surface handoff, writer retirement and acceptance. Room/unit culture and Nora's
practice use their existing stores and permissions; this consolidation does not
switch them live. [Revision and learning](50-learning-from-acts.md) connects new
evidence and outcomes to the affected understanding without requiring a lesson
for every change.

## 10. One backfill, built in time order

Process eligible sources oldest first across messages, media/social items,
Rollup episodes and Register writes. Use [the same Cognition session](29-one-cognition-session.md#3-completeness-latency-and-the-backfill)
in a separate lane with act tools withheld. Record/Rollup relations project
through code. Historical processing creates memory, not notes, reminders or
sends from past intentions.

The session's clock is the item's evidence instant, including relative-time
resolution and reads. Preserve as-of availability; current state or later
knowledge cannot silently enter historical input. The session uses the same
instructions as live understanding, not a rewritten historical prompt.

When history finishes, one catch-up wake per unit uses the real clock over
code-selected still-open commitments and relations whose event/due time is
still in the future. Older history remains memory only.

Hash the rendered input. Edits, late media and added source kinds reconsider
affected inputs; they do not require another full historical pass. A vocabulary
addition alone re-runs nothing; targeted reconsideration is an explicit operator
operation. Superseded readings remain historical versions, never a second live
truth source. Measure integrated-session cost and changed-profile work before
setting the authorized backfill budget; old separate-step estimates are not a
current spending allowance.

## 11. Gates, fixed before the spike

> **2026-10-31:** [31 §6](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31) owns current acceptance and
> qualifies this original two-step spike table. Quality targets below remain
> reported on observed populations; engineering guarantees still gate. Retired
> worker/step-cost rows follow 31 §3's session replacements. The later
> answerability/reply switch gates in §11, §14–§16 are historical, superseded
> by 31 §6's bounded decision, not waived quality findings.

A stratified sample (text-only, attachment-only, link, social item, and items
where an event spans several signals), run through the integrated Cognition session on a frozen
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
| cost per integrated session, median and p95, including profile maintenance | reported; the backfill budget is set from it |
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

Current switch acceptance is [31 §6](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31).
The historical fixed answerability/parity formula is not an additional gate.

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

[31 §6](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31) owns the
current sequence and bounded acceptance. [70](70-honcho-cutover.md) owns the
separate authorized switch, observation and removal. This graph contract adds
no fixed sample/parity quota or permission to spend, backfill or deploy.

## 16. Decisions this node needs

The settled model owns semantic entities/relations, retains bounded fetched
pages as evidence, excludes private-person egress in enrichment, and supersedes
legacy readings through historical processing. The story-opening restriction
and scope/evidence boundaries remain. The one-session design replaces the
original three-step/arrival schedule; historical recommendations and ratification
chronology resolve in git history.

## The reading that would reopen it

- The spike's judgement relation precision under 90% after one fix round:
  then judgements stay Character lines (25 §2's refusal stands) and the graph
  keeps only record and rollup relations plus entities.
- Wrong merges above 2%: entity resolution moves out of the loop into a
  separate, judged pass.
- The wake backlog grows across a day: measure session cost and capacity
  while retaining the existing ownership, concurrency and work-loss guarantees.
- Query p95 over 200 ms at depth 3 with precomputed neighbourhoods: a graph
  engine (AGE first) is measured.
- The answerability gate shows the subgraph preview answering no better than
  readings alone: the relations are not earning their cost, and the loop's
  enrichment and resolution steps are cut back to readings.

Loose ends noted, not blocking: no `play` stimulus arrived in the 7 days to
2026-10-28 (the producer may be idle), and `wake_at` was used once in those
7 days (172 sessions).


## 17. Implementation findings and longitudinal understanding (2026-10-30)

Operator-requested design amendment and investigation handoff. The findings below
are measured implementation gaps or explicit rollout boundaries, not a replacement
for the ratified graph architecture. [29](29-one-cognition-session.md) still owns
the unified session; [31](31-build-one-session.md) owns its build acceptance.
[32](32-platform-observations.md) specifies the missing platform observation path.
This section is the maintained home of the findings, independent of any issue.

**Evidence boundary:** source audited at
`a535a6960f98232455c80286c404cc181a19bf72`, checked again at
`8b3c0e6f4aa4e2f125c815ac8014ea3997c68950`; only release metadata changed between
those revisions. Seven generated local probes exercised production code using
existing contract fakes. Focused tests passed: 128 passed, 66 deselected, exit 0
(SQL cases excluded). No full gate, live-model evaluation or production error-rate
measurement is claimed by that investigation. The configured host alias did not
resolve before connection: live flags, rows and deployed inputs remain unverified.
Re-check each finding against the receiving session's exact head before repairing it.


### F1 — Wake graph context loses structural anchors

**Five Cs: Correctness / Completeness. Reproduced input regression.**

The existing `build_preview` seeds the source, author, mentions/faces, room, unit, reply parent and explicit references, and requests Record/Rollup/Notebook layers (`nora/domain/comprehend.py`, `build_preview`). The unified wake's `WakeMemory.prepare` reconstructs `ComprehendItem` without author, mentions, reply parent, URLs, accounts or place; its rebuilt source rows reset mentions and discard reply structure and metadata (`nora/app/cognition_memory.py`, `WakeMemory.prepare`).

It then seeds only source keys and aliases literally found in source text (the alias/seed construction in `WakeMemory.prepare`). Source persons are used as scope restrictions, not graph seeds. Its `Walk` does not specify layers, so the domain default is Rollup + Notebook (`nora/domain/memory_graph.py`, `Walk`). Knowing the sender's identity therefore does not ensure the sender's existing plans or the room's curated context are in the pre-model input.

Generated probe: an actor already has a valid `plans` relation. A new reply contains no actor/event name. The wake preview omits the relation; a direct actor seed on the same graph retrieves it. The previous `build_preview` also retrieves it. The rebuilt wake item has an empty author and no reply parent. This is a concrete mechanism for failing to interpret an implicit acknowledgement using established context; it does not prove every such utterance will be misread, since tools may recover the missing context.

Smallest direction: preserve structural input already available from the loader and reuse the established anchor construction under the existing scope/budget. Verify the actual assembled model input before changing instructions. Preserve intentional hub traversal limits; this finding does not call for unbounded traversal through people.

### F2 — Automatic recall bypasses new semantic relations

**Five Cs: Consistency / Completeness. Reproduced retrieval mismatch.**

`TranscriptInjection._prefetch` calls `.search`, which uses the legacy index walk. The model's explicit graph tool calls `.read`, which invokes `SqlMemoryGraph.walk`. A new Notebook relation can therefore be readable through a tool but absent from automatic recall, even with the graph collaborator wired.

Generated probe: a searchable breakfast reading has a valid new-graph `about` relation to an event whose name shares no query words. `.search` returns only the reading; `.read` reaches both the relation and the event. The tool result is correctly marked partial because this generated probe uses lexical retrieval; the event's presence is a positive control independent of that status.

Impact: social context requiring connected knowledge can remain invisible until Nora decides to search for something she may not know she is missing. Existing prefetch tests can pass because their fixtures exercise index adjacency rather than this new-graph-only connection.

Smallest direction: have automatic recall obtain a bounded view from the same scoped graph retrieval contract as the tools, retaining transcript deduplication and appropriate output rendering. Do not create a second graph or broaden read scope.

### F3 — Register links are lexical, not the session's semantic association

**Five Cs: Correctness / Completeness. Reproduced mechanism and ceiling.**

`RegisterGraphLinks.project` chooses targets by `alias.casefold() in body.casefold()` (`nora/app/register_graph.py`, `RegisterGraphLinks.project`). Notes use `concerns`, memos `settles`. Gatherings unconditionally obtain a Register-derived event entity (the `event_entry` branch in that method). This automatic linking cannot establish that a breakfast arrangement belongs to a differently named underlying activity merely from the session's understanding.

Generated probe: a new note about breakfast and an existing event produce zero new links. Keeping the evidence and graph fixed but adding the event's exact name to the note produces one link. No model was used, and the test does not claim that every natural note lacks an appropriate alias. It proves that semantic context alone is not represented by this linking mechanism.

Smallest direction: carry an evidence-grounded association selected in the existing Cognition session into the Register write/link step, with current admission and retry ownership. Alias matching can assist discovery, but should not be the only mechanism that serializes the understood association. Do not mandate a breakfast-specific rule or infer a new independent event from the literal poll topic.

**Disposition, recorded 2026-11-02.** The source-side repair merged in [#490](https://github.com/mikeng-io/nora/pull/490) (2026-11-01, `ee58149`): the six Register create/revise verbs persist the session-shown explicit associations, explicit `associations: []` clears selected links while an omitted `associations` on revise preserves them, and retry re-links the same Register row by its exact act key with the receipt in the same transaction. The probe text above remains the historical evidence that motivated the repair — it does not describe current source behaviour. Whether a later answer or correction actually uses a persisted association is a semantic property the source merge cannot prove; the unmeasured remainder is recorded in [31's dated snapshot](31-build-one-session.md#progress-evidence-and-next-bounded-action-2026-11-02).

### F4 — Unified profile curation does not yet feed the live profile surface

**Five Cs: Completeness. Intentional rollout boundary, not an accidental failing write.**

The unified session's `profile_revise` calls `WakeMemory.profile`; it validates admitted relations and shown evidence, then calls `records.shadow` (`nora/app/cognition_memory.py`, `WakeMemory.profile`). SQL saves a `reshape` receipt containing `would_writes` (`nora/infra/sql/comprehend.py`, `SqlComprehendRecords.shadow`). That path does not write live profile heads, room culture or Lessons.

Generated probe: a valid proposal returns `ok` with the explicit shadow note and creates one shadow receipt. The code itself establishes the destination; the probe is not a claim about live profile content.

Separate `ProfilePersonAdaptor` and collective adaptors still exist in composition and can write profile stores under their own configuration and evidence cycle (`nora/app/composition.py`, the `ProfilePersonAdaptor` construction). Consequently it would be wrong to report that Nora has no long-term profile system. The precise gap is that new unified-session reshape output is not automatically the profile read by later turns. The ratified session design explicitly stages this as a later surface switch.

Smallest direction: verify the intended phase/promotion and its acceptance before claiming a closed learning loop. No silent flag flip or deployment is authorized by this audit. A graph relation may remain useful even while a free-text behavioural account is still shadow-only; assess both separately.

**Disposition, recorded 2026-11-02.** The source handoff merged in [#495](https://github.com/mikeng-io/nora/pull/495) (2026-11-01, `78e9f4a`): the session's `profile_read`/`profile_revise` now read and write the existing typed Profile accounts through `SqlProfileStore`, with shown cited evidence, `relation:` premises, an atomic receipt, and shown-view invalidation on write; adaptor retirement and the session's replacement share one availability predicate covering Cognition and every unit's home room. The source slice's acceptance is closed and does not reopen without a reachable defect. What remains unproved is the operational activation — the authorized flag flip and a later live turn reading the persisted account — recorded in [31's dated snapshot](31-build-one-session.md#progress-evidence-and-next-bounded-action-2026-11-02). The probe text above stays historical evidence, and no flag change or deployment followed from it.

### F5 — Admission proves evidence custody, not the truth of social interpretation

**Five Cs: Correctness evidence boundary. Reproduced, not a request for deterministic semantic validation.**

`validate_relation` checks the closed vocabulary, endpoint kinds, layer, evidence availability, exact span presence and scope (`nora/domain/memory_graph.py`, `validate_relation`). It does not determine whether a source semantically entails a proposed relation.

Generated probe: an `attended` judgement supported only by a literal assent is structurally admitted with no refusals. This demonstrates that a saved relation/evidence count cannot certify that Nora distinguished intent, teasing and actual attendance. The session instruction already says a plan does not establish attendance. The semantic property must be measured using the model's real input and trajectory, not patched with keyword/time-of-day/person-specific rules.

The graph preserves source/judgement layers, attribution and corrections; these are valuable safeguards. They make a judgement auditable but do not by themselves make it correct. Prior Notebook conclusions can provide context, while original Record/Rollup evidence must remain the independent support.

### F6 — Checked-in semantic acceptance is still parked

**Five Cs: Correctness / Completeness of validation. Verified file census.**

`evals/corpora/cognition_session.yaml` contains 11 cases, all 11 parked and zero active cases in this checked-in corpus. It includes useful distinctions such as a lone acknowledgement versus agreement in context, third-visit profile change, ticket purchase versus attendance, and group-local interests. Its header states that `expected_output` explains the property and is not a separate semantic scorer.

The repository also contains private-anchor/replay and graph-spike tooling; unit tests exercise its fail-closed aggregation. Their existence is not a receipt that the current model passed longitudinal social-understanding evaluation. Private host-side artifacts could supply additional evidence, but were inaccessible here.

The most relevant missing measurement for this discussion is an anchored longitudinal trajectory where identical current words have different interpretations because of prior relationships, conventions and outcomes, followed by new evidence that changes the earlier account. Model evaluation follows the recorded-wake ruling below; generated fixtures only test code guarantees. Evaluate the account and subsequent answer, not only whether a tool named `memory_propose` was called. Cases test the business property; they are not a list of production scenario rules.


### Long-term social understanding is the acceptance property

An utterance occurred; Nora interprets its social force; an outcome later occurs
or remains unknown. These are separate claims with separate evidence. Assent is
not automatically commitment or attendance, teasing is not automatically a trait,
and a literal poll topic is not necessarily the underlying shared matter. Prior
relationships, group conventions and observed outcomes can change the reading of
identical current words. A quiet person's missing vote does not establish absence.

Notebook curation owns these revisable accounts. The graph connects them to people,
rooms, ongoing matters and independent Record/Rollup evidence; it is not itself the
curator. Curated understanding guides bounded retrieval and attention, while new
and contradictory observations remain discoverable. A repeated Notebook conclusion
cannot become independent evidence for itself. Do not add phrase/time/person rules,
a universal joke classifier or a global reliability score to ETL or admission.

An arrangement can belong to an existing activity without creating a second event.
The session must preserve that association through Register writes and later reads.
Likewise, a source poll may show three explicit voters while an evidence-backed
ongoing-matter account expects a larger party. Preserve both meanings and their
uncertainty instead of changing poll counts to match Nora's expectation.

### Status recorded 2026-10-31

An independent source audit at `3fe09ac7` re-derived each finding against
that head. **F1 and F2's mechanisms are repaired there** (source-confirmed,
not a live probe or a model-quality receipt): `WakeMemory.prepare` restores
the structural anchors and seeds via `preview_seeds`, and automatic prefetch
calls the same `MemorySearch.read`/`graph.walk` retrieval as the tools. One
limitation stands: the prefetch cache in `context.py` is per-turn local —
automatic and tool reads share the retrieval mechanism but a shared cache is
not proven at that head. **F3 remains open, smaller than first described:**
`RegisterGraphLinks.project` still links by literal alias, but `concerns`/
`settles`/`records` are now admitted proposal vocabulary on the notebook
layer, so a semantic association is serializable through `memory_propose`,
just not carried automatically. F4 stays intentionally staged; F5 is the
documented evidence boundary; F6 stays parked by ruling.

**2026-11-02:** F3's source repair merged as #490 and F4's source handoff
as #495 — the dated dispositions under each finding now carry current
state. F5 and F6 are unchanged, and no finding beyond F1–F4 is claimed
closed.

### Repair and measurement order

1. Preserve the available structural anchors and inspect the assembled input (F1).
2. Use the same scoped graph semantics in automatic recall and explicit tools (F2).
3. Carry understood associations through Register persistence/re-linking (F3).
4. Verify the authorized profile-surface handoff at its planned phase (F4).
5. Measure longitudinal interpretation, later answers and correction (F5/F6).

[31](31-build-one-session.md)'s recorded-wake anchoring ruling stands. Generated
code probes demonstrate wiring mechanisms, not model understanding; missing real
model anchors remain parked/ERROR, not replaced by synthetic model inputs. Use
held-out longitudinal evidence journeys and an independent semantic judgement,
with the active/parked/held-out census and actual run receipts. Compare histories
that change interpretation, then introduce counterevidence and inspect the revised
account and a later turn. These are evaluation properties, not production rules.

This order holds unless a same-head reproduction or an actual assembled/deployed
input overturns a finding. Passing structural tests, a shadow receipt or a tool-call
name alone cannot close the semantic requirement.

## Amendment 2026-11-06: statement-shaped `memory_propose`

Operator-approved simplification of the proposal contract (audit
items 1–5); the stores, layers, vocabulary and admission of this node stand.

- **Relation `persons` is derived, not authored.** §3's rule — `persons` copied
  from the strictest evidence — is now computed in admission as the union of
  the cited `Source.persons`; the model never writes the field. `validate_relation`'s
  subset check stays as the independent boundary.
- **One evidence custody.** The citable sources are exactly the rows the agenda
  actually rendered (after clipping/dropping), not everything loaded; tool-read
  rows are still shown by `fetch_watch`. A graph-preview label alone is not having
  read a source body.
- **Statements replace parallel lists.** `memory_propose` takes self-contained
  statements — a relation (verb, subject, object, validity, citations), a
  correction (a held relation's shown id, change, citations, a nested replacement
  on supersede), a reading (about, what, happened_at, where, how, citations).
  Endpoints are shown `@kind:id` handles or `{new: {kind, name, aliases,
  attributes}}`; identity pointers are only ever handles, never created. Code
  allocates ids, flattens to the existing `Entity`/`Relation`/`Evidence` rows and
  derives a reading's `source_ids` from its citations. The tool schema is
  generated from the same typed proposal models admission consumes.
- **Completion is per input, never per batch.** An agenda input is done when a
  saved statement cites one of its rendered rows or the model declares it
  `nothing_here`; a refused statement no longer withholds the rest. Each statement
  reports saved, or refused with one primary reason — no dependent-cascade
  diagnostics.
