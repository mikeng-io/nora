---
type: design
title: "Map — every store, every context block, and how they relate"
description: "The five kinds of state (authored, live, observed, derived by Nora, derived by memory), each store in each, what every prompt block reads from, who writes what, and the graph of how they feed each other."
tags: [nora, tiers, map, context, prompt-blocks, graph]
timestamp: '2026-09-28'
edges:
  - {dst: 10-tiers, rel: extends, provenance: asserted}
  - {dst: 55-self, rel: sequences, provenance: asserted}
---

# 15 · Map

## 1. Five kinds of state, and the tiers among them

| kind | what makes it this kind | stores | written by |
|---|---|---|---|
| **Authored** | a person decided it | doctrine (`nora/doctrine/`: constraint, identity/voice, conduct, instrument); the Dreamer doctrine and skills; `<room>` and `policy.toml`; `identity.toml` | the operator |
| **Live** | computed for this turn, never stored | the clock; where she is (which platforms are up); the tool catalogue | code, per turn |
| **Observed** — tier **Record** | it happened and was seen | messages; her own sends and decisions; presence; social items and edges; perceptions (what media shows, whose face) | code (ingestion) |
| **Derived by Nora** — tiers **Register**, **Character**, **Standing** | cognition concluded it from the Record | Register: note, memo, event · Character: person, room (culture), unit (culture), Nora · Standing: room, unit, person | cognition; the tag path for Register and room Standing |
| **Derived by a third party** — **memory** | Honcho concluded it | representation (Nora → person), self card (Nora → Nora), `remember` conclusions | Honcho; `remember` |

## 2. Every context block, and where it comes from

The tag turn's blocks (`_BLOCKS` in `nora/agent/prefill.py`), plus the ones
this bundle adds:

| block | kind / tier | subject | read by |
|---|---|---|---|
| doctrine (system prompt) | Authored | Nora | every turn |
| `<room>` | Authored | a room | tag, fire |
| `tool_catalogue` | Live | a room's tools | tag, fire |
| `presence` (where she is) | Live | Nora | tag, fire |
| now / today | Live | — | every turn |
| `group_transcript` | Record | a room | tag, fire, cognition |
| `people_in_window` | Record | a room | tag |
| `loop_decisions` | Record (her own decisions) | the unit | tag, cognition |
| `notes` | Register · note | the unit | tag, fire, cognition |
| **`memos`** (new) | Register · memo | the unit | tag, fire, cognition |
| **`events`** (new; today only via `list_events`) | Register · event | the unit | tag, fire, cognition |
| `culture` | Character · room | a room | tag, fire, cognition |
| **`character`** (new) | Character · person | the people in the window | tag, fire, cognition |
| **`self`** (new) | Character · Nora | Nora | every turn ([55](55-self.md)) |
| `standing`, `standing_working_set` | Standing · room | a room | tag, fire, cognition |
| **`standing_unit`**, **`standing_person`** (new) | Standing · unit, person | the unit; a person | tag, fire, cognition |
| `self_card` | memory (Nora → Nora) | Nora | tag, fire |
| `peer_card`, `peer_history` | memory (Nora → person) | a person | tag |

## 3. The graph

```mermaid
flowchart BT
  subgraph SRC[sources]
    MSG[messages] --- PRES[presence] --- SOC[social: stories, posts, follows] --- MED[media]
  end
  REC[(RECORD<br/>observed)]
  REG[(REGISTER<br/>note · memo · event)]
  CHR[(CHARACTER<br/>person · room/culture · unit/culture · Nora)]
  STD[(STANDING<br/>room · unit · person)]
  MEM[(memory<br/>Honcho)]
  DOC[[DOCTRINE<br/>authored]]
  LIVE[[live situation]]
  COG{{COGNITION<br/>one session per unit}}
  TAG{{tag turn<br/>reflex}}
  FIRE>fire path: speak, remind, ask]

  SRC -->|ingestion, code| REC
  REC -->|signals| COG
  REC -.feeds.-> MEM
  COG -->|write verbs| REG
  COG -->|write verbs| CHR
  COG -->|write verbs| STD
  COG -->|act verbs| FIRE
  TAG -->|write verbs| REG
  TAG -->|debrief| STD
  TAG --> FIRE
  FIRE -->|her sends are observed| REC
  REG -. cites .-> REC
  CHR -. cites .-> REC & REG
  STD -. cites .-> REC & REG & CHR
  DOC --> COG & TAG
  LIVE --> COG & TAG
  MEM --> COG & TAG
  REG & CHR & STD --> COG & TAG
```

Solid arrows are writes and reads; dotted arrows are citations, which only
ever point down. The loop closes through the fire path: what she says becomes
Record, which the next session observes like anything else.

## 4. Who may write what

| writer | Record | Register | Character | Standing | memory | doctrine |
|---|---|---|---|---|---|---|
| ingestion (code) | **yes** | — | — | — | feed | — |
| cognition | — | **yes** | **yes** | **yes** | — | proposes only ([55](55-self.md) §3) |
| tag turn | — | **yes** | — | room, via debrief | `remember` | — |
| operator | — | via Nora | — | — | — | **yes** |
