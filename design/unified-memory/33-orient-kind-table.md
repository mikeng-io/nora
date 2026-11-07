---
type: design
title: "Orient: one kind table drives what the Cognition session writes"
description: "The one Cognition session (29) is an OODA loop with CMS inside each phase. Orient writes memory from one kind table — per kind its meaning, when it is created, its evidence, its relations, its writer and its correction — that admission enforces; the instruction's memory part states each kind with its create-when and an example, and renders the kind and relation names from the same constants. Resolution candidates are part of Observe. Measured by recorded-wake replay, profile lines included."
edges:
  - {dst: 29-one-cognition-session, rel: amends, provenance: operator-requested}
  - {dst: 27-the-memory-is-a-graph, rel: depends_on, provenance: asserted}
  - {dst: ../notebook-memory-types/00-design.md, rel: extends, provenance: asserted}
  - {dst: ../ambient-loop/01-stages-and-seams.md, rel: relates_to, provenance: asserted}
---

# 33 · Orient: one kind table

Status: **shape ruled** by the operator, 2026-11-03 (the kind table; profile
lines measured in the replay). The cell values below are proposed from 27 §5,
27 §7 and [notebook-memory-types/00](../notebook-memory-types/00-design.md)
§2–§3, and are ruled with this node's review. Docs only: nothing is built until
the build is briefed.

## 1. What was measured

Production, 2026-11-02 13:00 – 2026-11-03 04:52 UTC, 26 live Cognition
sessions (generated report; session rows, `comprehend_result`, SigNoz traces):

- One reading and no graph relation were written. Every `graph` receipt read
  was empty; 31 of 34 linked extractions held no claim.
- In 3 sessions (4 with one borderline) the batch stated something durable
  about a person or thing and nothing was written.
- 64 model calls cost 1,539,063 tokens; median 52,028 per session.

Two causes, both read in the session's own input and output:

1. **No rule said what to write, so the notebook's rule was borrowed.** The
   instruction carries the notebook's test («somebody has decided to do it,
   and some corner of it is still unsettled», `nora/dreamer/skills/cognition-register.md`)
   and the memory field shapes (`nora/language/comprehend_render.py`
   `COMPREHEND`), but no criterion for any other kind. The model's reasoning
   applied the open-corner test to memory («no plan, no arrangement. Nothing
   to write»), calling itself the notebook keeper.
2. **Orient had nothing to connect to.** 27 §7 step 1 built resolution
   candidates for every name in an item (exact name or alias, trigram
   similarity, a place near its anchor, an event in its time window, each with
   its strongest relations: `nora/domain/comprehend.py` `build_preview`). When
   29 retired the per-item runner, `build_preview` lost its only caller. The
   session's preview passes none: `nora/app/cognition_memory.py`
   `Preview(seeds, graph, (), (at, at))`.

Neither is a schema gap. 18's rule that a reading is written for every batch
is **not** restored: raw messages are already the Record, and the session is
the one interpreter (29 §1); a second derivation pass would interpret twice.

## 2. The session is an OODA loop; CMS is inside each phase

The ambient loop's own staging ([ambient-loop/01](../ambient-loop/01-stages-and-seams.md),
[cognition-loop/20](../cognition-loop/20-observe.md)) is kept, and 29's one
session is placed on it:

| OODA | what happens | who | CMS |
|---|---|---|---|
| **Observe** | stimuli accumulate; a wake rule fires (ambient: passive, clocked, or `wake_at`); code seeds from the batch, walks the graph and finds resolution candidates; context blocks | code | comprehend (input) |
| **Orient** | for each thing in the batch: **match** a candidate, **create**, **relate**, **correct** an older relation, or **enrich** a public entity; one `memory_propose`; code admits each part against its kind's row (§3) and answers admitted / refused with reason / connected subgraph | model proposes, code decides | comprehend → **measure** |
| **Decide** | reshape the entities whose relations changed (profile lines, culture, lessons); then act, or stay silent, on committed state | model | measure |
| **Act** | act tools; close: receipts, stimuli consumed or retried, the next `wake_at` | model, code | **serialize** |

`serialize(N)` is `Observe(N+1)`: the next wake reads the graph this one
wrote. The order is enforced as 29 §2 already enforces it: act tools refuse
until `memory_propose` is admitted. **Resolve comes before create**: a thing
is matched against its candidates first, or every mention mints a new entity.

## 3. The kind table

One named constant in code, one row per kind. Admission **checks against
it**. The instruction's memory part states each writable kind with its
create-when and an example, and renders the entity kinds and admitted
relation names from the same constants (`comprehend_render.py`
`_VOCABULARY`, «the model is shown exactly the list code admits against»),
so the two cannot drift. A new kind is a row
and a review (27 §5's rule, extended from relations to kinds). The Notebook
rows extend notebook-memory-types/00 §3, where each kind is already «a spec
on the one entry».

| kind | memory type | create when | evidence | relates by | writer | corrected by |
|---|---|---|---|---|---|---|
| **event** | episodic | an item states something that happens at a time: a plan, an RSVP, an occurrence | Record (message, social item, stay) | `plans`, `attended`, `went_with`, `at`, `features`, `records` | Orient | supersede; `attended` needs evidence beyond the person's own plan (27 §5) |
| **place** | semantic | a named place is mentioned or tagged and no candidate matches | Record; a fetched page for public detail | `at`, `located_in`, `lives_in`, `works_at`, `studies_at`, `stayed_at` | Orient; Rollup for stays | supersede; `same_as` |
| **work** | semantic | a specific game, show, artist, product or song is named | Record; a fetched page | `likes`, `dislikes`, `features` | Orient | `same_as` |
| **organisation** | semantic | an employer, school, venue operator or team is named | Record; a fetched page | `works_at`, `studies_at`, `member_of`, `features` | Orient | supersede; `same_as` |
| **topic** | interests | a subject recurs across conversations and nothing would complete it | Record; weight counted by code from `interested_in` evidence | `interested_in`, `about` | Orient (anchor); Decide (account) | active ↔ dormant; retired |
| **person relation** | semantic | an item states or repeatedly shows a person's preference for one specific thing, a workplace, residence or membership | Record, never a Notebook entry | `likes`, `dislikes`, `interested_in`, `works_at`, `lives_in`, `studies_at`, `member_of` | Orient | close / supersede / dispute; never deleted |
| **reading** | semantic | an item states something about a person or thing that no relation above can hold | Record; exact span | `attached_to` its sources, `about` entities | Orient | superseded on re-read |
| **profile line** | semantic | an entity's relations changed in this session | the relations it `draws_on`, and their evidence | `about`, `draws_on` | Decide (reshape) | supersede; `profile_dispute` |
| **note** | prospective | somebody has decided to do something and a corner is still open; you can say what would close it | Record | `concerns`, `cites` | Act | open → pending → ended / kept |
| **lesson** | procedural | a closed response episode teaches how Nora should act | the episodes | `cites` | Decide | retired |
| **decision** | working | Nora's own reasoning on one question the Record cannot give back | Record, links to entries | `about`, links | Act | closed, with `Reopen if` |

**Silence is correct for an item that fits no row**: a reaction, banter that
states nothing about anyone, a question nobody took up. The open-corner test
is the `note` row's «create when», and binds nothing else.

**The vocabulary has known gaps**, measured in the misses: no possession or
lending relation, and `likes` does not admit an organisation (a brand). Such a
claim is a `reading` until a relation is added by the existing route (27 §5: a
model proposes, a human adds). `# ponytail: readings carry what the vocabulary
cannot; add a relation when retrieval needs to query it as structure`.

## 4. What the build changes

1. **The kind table** as one constant; the instruction's memory part states
   each kind with its create-when and an example; admission reads the same
   rows and the instruction renders the kind and relation names from the
   same constants. The notebook skill's
   open-corner rule becomes the `note` row, scoped to notes only
   (`cognition-register.md` is operator-only; the edit is ruled with this node).
2. **Resolution candidates restored** in Observe: the session runs the existing
   candidate lookup over its seeds and passes them to `Preview`, within the
   preview's token budget.
3. **Admission keeps valid parts.** One reading with a bad citation refuses
   only that reading, not the proposal (`nora/app/cognition_memory.py`, the
   `partition_claims` drop branch), matching 27 §7 step 6 («valid parts commit
   and refused parts are recorded with their reason»).
4. **Correction is exercised**: a later item that contradicts a current
   relation closes or supersedes it.

## 5. How it is measured

Recorded-wake replay (`evals/rebuilt/entry.py`), before and after, on the same
wakes: the three measured misses and the borderline one, about ten sessions
that correctly wrote nothing, and one generated contradiction case.

- **Orient:** each stated event, place, work, organisation, topic or person
  relation is written with the right ends, persons and an exact span; a new
  mention of a known thing attaches to its candidate instead of creating a
  duplicate; a tentative statement keeps its speech act.
- **Negative:** the empty sessions stay empty; nothing is promoted to a
  settled fact; notes are unchanged.
- **Decide:** profile lines (shadow) move only for entities whose relations
  changed, each `draws_on` an admitted relation.
- **Cost:** tokens per admitted write, against today's 1.54M for one reading.

`unavailable` or a timeout is ERROR, never PASS. One run decides nothing:
read rates over the whole replay set.

## The reading that would reopen it

- The model still applies one kind's rule to another with the kinds stated:
  then Orient's writes move to a session of their own.
- A kind cannot state its «create when» as evidence: it needs its own stage,
  not a row.
- Readings written only where no relation fits make retrieval measurably
  worse in replies: then the `reading` row narrows.

## Amendment 2026-11-06

§4.3's part-wise keep is extended to the statement contract: `memory_propose`
carries self-contained statements (relation, correction, reading) with
`@kind:id` handles or `{new: …}` endpoints and rendered-row citations;
`persons` is derived from cited sources, the schema is generated from the typed
proposal models, and an item completes when a saved statement cites it or it is
declared `nothing_here`. The kind table, its «create when» rows and the
silence-is-correct rule are unchanged.
