---
type: design
title: "Map — every store, every block, every read tool, and how they relate"
description: "The kinds of state (authored, live, observed, computed, derived by Nora, derived by memory, operator-only), each store in each; which context is pushed into every prompt as a small index and which is pulled by a read tool; who writes what; and the graph, laid out as the operator's lake, transform, warehouse, dashboard, action."
tags: [nora, tiers, map, context, prompt-blocks, read-tools, graph, rollup]
timestamp: '2026-09-28'
edges:
  - {dst: 10-tiers, rel: extends, provenance: asserted}
  - {dst: 25-rollup, rel: depends_on, provenance: asserted}
  - {dst: 55-self, rel: sequences, provenance: asserted}
---

# 15 · Map

## 1. Every store

| kind | what makes it this kind | stores | written by |
|---|---|---|---|
| **Authored** | a person decided it | doctrine (`nora/doctrine/`); the Dreamer doctrine and skills; `<room>` and `policy.toml`; `identity.toml`; reference photos for faces ([60](60-faces.md) §2) | the operator |
| **Live** | computed for this turn, never stored | the clock; her platform status (which platforms are up); the tool catalogue | code, per turn |
| **Record** | it happened and was seen | messages with edits, deletes and reactions; her own sends and decisions; presence fixes and collector polls; social items; play samples; perceptions (what media shows, whose face); face labels and templates; `character_dispute` items | code (ingestion; the decision log and the fire path for her own acts; the tag path's `face_label` and `character_dispute`, authored by the person) |
| **Rollup** | code computed it from the Record | episodes: stay, trip, co-location, face co-appearance, outing, play session, daybook · aggregates: place, occupancy, rhythm, corridor and trip speeds, tie, play profile ([25](25-rollup.md)); anomalies are computed on read, not stored | code only |
| **Register**, **Character**, **Lessons**, **Standing** | an adaptor concluded it | Register: note, memo, event · Character: person, room (culture), unit (culture), Nora · Lessons: the unit's lessons · Standing: room, unit, person | Register: the Cognition adaptor and the tag path · Character: the Character adaptor only · Lessons: the Lessons adaptor only · Standing: the Cognition adaptor and the debrief |
| **memory** | Honcho concluded it | representation (Nora → person), self card (Nora → Nora), peer card, `remember` conclusions | Honcho; `remember` |
| **Operator-only** | for the operator, never for her | tension rows ([55](55-self.md) §4); assess reports | the Character and Lessons adaptors; the Assess adaptor |

## 2. The operator's analogy, laid out

| data stack | Nora |
|---|---|
| lake | **Record** |
| extract / enrich | ingestion and perception ([20](20-observe.md), [60](60-faces.md)) |
| transform, deterministic | **Rollup** ([25](25-rollup.md)) |
| transform, judged | the Dreamer's adaptors ([00](00-intent.md) §3) writing Register, Character, Lessons, Standing |
| warehouse | Rollup + Register + Character + Lessons + Standing |
| row-level security | **lineage** and `exclude_persons`, applied at assembly ([10](10-tiers.md) §3) |
| dashboard | small indices pushed into every prompt (§3) |
| queries | read tools (§4) |
| action | the act verb and the fire path |

## 3. Context pushed into the prompt: small indices only

Pushed blocks are few and short, because every per-turn line costs every turn
and a row that varies per turn ends the cached prefix's hit rate (the comment
on `culture` in `_BLOCKS`, `nora/agent/prefill.py`). Everything else is pulled
(§4). Every pushed block carries its age, and renders each row, field or
section only where its lineage passes for the room ([10](10-tiers.md) §3).

| block | tier | subject | pushed into | size |
|---|---|---|---|---|
| doctrine (system prompt) | Authored | Nora | every turn | — |
| `<room>` | Authored | a room | proactive only today (`this_room_is` on `<presence>`, `nora/language/proactive.py`); tag and fire turns do not get it (`prefill.py` calls `render_presence` without `room_is`); note-candidates and culture get `description_for`. Cognition gets it for every bound room | one line |
| `tool_catalogue`, `presence` (her platform status, not presence fixes), now | Live | — | tag, fire | — |
| `group_transcript` | Record | a room | tag, fire | window |
| `loop_decisions` | Record | the unit | fire (participation, proactive), cognition | recent |
| `notes` | Register · note | the unit | tag, fire, cognition | titles |
| **`memos`** (new) | Register · memo | the unit | tag, fire, cognition | titles |
| **`events`** (new) | Register · event | the unit | tag, fire, cognition | next 14 days and last 48 hours, one line each, with each field's mode and `as_of` |
| `culture` | Character · room | a room | tag, fire, cognition | capped |
| **`self`** (new, step 8) | Character · Nora | Nora | every turn, after the doctrine and below it; her whole account ([55](55-self.md) §3) | capped in lines |
| **`lessons`** (new, step 8) | Lessons | the unit | tag, fire, cognition, after the doctrine and below it | the unit's lessons, under a hard size cap ([50](50-character-and-standing.md) §6) |
| `standing`, `standing_working_set` | Standing · room | a room | tag, fire | as today |
| `self_card` | memory | Nora | tag, fire; until step 8 | as today |
| `peer_card`, `peer_history`, `people_in_window` | memory | a person; the people in the window | tag, fire | as today |

## 4. Context pulled by a read tool

| tool | reads | returns | new? |
|---|---|---|---|
| `record_query(scope, from, to, author?, text?)` | the Record | messages and items in an interval; lexical search by pg_trgm, measured on Cantonese (design/dreamer-agent-loop/24-embedding-seam.md §2) | **new**: Postgres messages have no text index today; Honcho's search (`search_memory`, `search_memory_workspace`) is memory |
| `rollup_read(kind, subject, interval)` | the Rollup | episodes and aggregates | **new** |
| `where_been`, `routine_read` | the Rollup (presence) | stays, places, rhythm; a forecast from the rhythm (DEC-215) | exist; tag path |
| `note_read`, `note_search` | Register · note | a note body; matching notes | exist |
| `memo_read` | Register · memo | a memo body | **new** |
| `event_search(place?, person?, interval?)` | Register · event | events | **new**; `list_events` is per room, filtered by status only |
| `character_read(entity)` | Character | an account | **new**; instead of an always-on person block |
| `standing_read(subject)` | Standing | a room's, the unit's or a person's Standing | **new** |
| `social_items(person, interval)` | Record · social | ingested stories, posts, reels with their perceptions | **new**; the live `instagram_*`, `x_*`, `linkedin_*` reads stay on the tag path and are withheld from cognition ([20](20-observe.md) §2) |
| `about`, `ask_memory`, `search_memory`, `search_memory_workspace` | memory | Honcho | exist |

Every read tool applies lineage for the room its turn or agenda item is bound
to. Cognition has no room of its own: each agenda item binds one, and that
item's room-scoped reads resolve through it ([30](30-cognition.md) §1).

## 5. The graph

```mermaid
flowchart BT
  subgraph SRC[sources]
    MSG[messages] --- PRES[presence] --- SOC[social] --- PLAY[play] --- MED[media]
  end
  REC[(RECORD · lake)]
  ROL[(ROLLUP · computed<br/>episodes · aggregates)]
  SIG[(signals · per unit)]
  REG[(REGISTER<br/>note · memo · event)]
  CHR[(CHARACTER<br/>person · room · unit · Nora)]
  LES[(LESSONS<br/>per unit)]
  STD[(STANDING<br/>room · unit · person)]
  MEM[(memory · Honcho)]
  TEN[(tension · operator only)]
  DOC[[DOCTRINE · authored]]
  subgraph SUB[subconscious · the Dreamer's adaptors]
    COG{{Cognition<br/>fast, per unit}}
    CAD{{Character adaptor<br/>slow, per unit and entity}}
    LAD{{Lessons adaptor<br/>slow, per unit}}
    ASA{{Assess adaptor<br/>reports only}}
  end
  subgraph CON[conscious · the speaking turns]
    TAG{{tag turn · reflex}}
    FIRE>fire turn: a due reminder]
  end

  SRC -->|ingestion + perception, code| REC
  REC -->|Rollup builder, code| ROL
  REC & ROL -->|signals| SIG
  SIG -->|wakes| COG
  COG -->|wake_at| SIG
  REC -->|feeds| MEM
  COG -->|write verbs| REG & STD
  CAD -->|character_revise| CHR
  LAD -->|lesson_write, lesson_retire| LES
  CAD & LAD -->|tension| TEN
  COG -->|remind: the one bridge| FIRE
  TAG -->|write verbs| REG
  TAG -->|debrief: standing_revise| STD
  TAG -->|face_label, character_dispute| REC
  TAG -->|remind| FIRE
  FIRE -->|her sends are observed| REC
  ROL -. cites .-> REC
  REG -. cites .-> REC & ROL
  CHR -. cites .-> REC & ROL & REG & CHR
  LES -. cites .-> REC & ROL & REG & CHR
  STD -. cites .-> REC & ROL & REG & CHR & LES
  DOC --> SUB & CON
  MEM --> COG & TAG
```

Solid arrows are writes, reads and signals; dotted arrows are citations,
which only point down. What she says becomes Record and raises no signal
([20](20-observe.md) §3). Every adaptor reads the tiers and blocks it needs;
the read arrows are left out. The two layers are the operator's model
([55](55-self.md) §6): the subconscious drives the conscious, through the
pushed blocks and `remind`; the conscious reaches the subconscious only as
Record and as ordinary Register and Standing writes.

## 6. Who may write what

| writer | Record | Rollup | Register | Character | Lessons | Standing | tension | memory | doctrine |
|---|---|---|---|---|---|---|---|---|---|
| ingestion (code) | **yes** | — | — | — | — | — | — | feed | — |
| Rollup builder (code) | — | **yes** | — | — | — | — | — | — | — |
| Cognition adaptor | — | — | **yes** | — | — | **yes** | — | — | **never** ([55](55-self.md) §3) |
| Character adaptor | — | — | — | **only writer** | — | — | via `character_revise` | — | **never** |
| Lessons adaptor | — | — | — | — | **only writer** | — | via `lesson_write` | — | **never** |
| Assess adaptor | — | — | — | — | — | — | — (reports and metrics only) | — | **never** |
| tag turn | `face_label`, `character_dispute` | — | **yes** | — | — | room, via debrief | — | `remember` | — |
| operator, or the subject | face labels (portal, [60](60-faces.md) §2) | — | via Nora | — | the operator's rollback by id, through `lesson_retire` | — | — | — | operator only; changes when the humans all agree |

Tension rows are read only by the operator ([55](55-self.md) §4).

## 7. The as-of dimension

Every store above except memory is append-only in system time
([10](10-tiers.md) §8), so each has a second axis: *as of T*. The blocks and
read tools read the current view (as of now); `nora snapshot --at T`
([70](70-migration.md) §2) reads every store as of T, with the doctrine,
skills, config and model route in force at T, and the Rollup rebuilt to the
watermark T. Memory has no such axis.
