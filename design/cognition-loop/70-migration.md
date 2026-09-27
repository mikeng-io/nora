---
type: design
title: "Migration — inside the Dreamer runtime, in shippable steps"
description: "What of the Dreamer runtime is kept and what is replaced, the build order with the reading that gates each step, what this supersedes, and the positions still open."
tags: [nora, migration, dreamer, refactor, shadow, eval]
timestamp: '2026-09-28'
edges:
  - {dst: 00-intent, rel: realizes, provenance: asserted}
---

# 70 · Migration

## 1. Inside the Dreamer, not beside it

The operator asked whether this is a great refactor or stays within the
Dreamer. It stays within: the runtime that makes a background agent safe is
kept, and what is replaced is how it is woken and how its work is divided.

| part | fate |
|---|---|
| `DreamerRuntime.run` (`nora/agent/dreamer.py`), the Strands dream agent, `DreamGuard`, `AdaptorGrant` and home-room resolution, `RegistryToolSource`, budgets, telemetry | **kept**: the cognition session runs on them |
| `dream_loop`, `_dream_adaptor_once`, `seed_watermarks` (`nora/app/runtime.py`), `AdaptorSchedule`, per-adaptor watermarks | **replaced** by signals and the unit session ([20](20-observe.md)) |
| the ingest-triggered Standing fold (`nora/ingest/stages/standing_event.py`) | **replaced** by signals and cognition's `standing_revise` (room case) |
| each adaptor's envelope, `accept` and `commit` | **moved behind write verbs**: the validation survives, the per-adaptor envelope goes |
| the four orient adaptors (`note_candidates`, `note_curator`, `note_merge`, `culture` in `nora/language/`) | **retired** after cut-over; their objectives become skills ([30](30-cognition.md) §4) |
| the proactive adaptor | **becomes the act step**; its writes to the shared decision log (DEC-179) become the act step's |
| the Standing debrief | **kept on the tag path**; it writes through `standing_revise` to the [12](12-schemas.md) §5 room schema |
| participation | **unchanged** ([30](30-cognition.md) §7) |

"Dreamer" stays the name of the runtime in code. What it runs is cognition:
the orient, decide and act steps of the ambient loop (DEC-176); observe is
ingestion ([20](20-observe.md)).

## 2. The steps

Each step ships alone, behind a flag that defaults off, and is read back live
(`nora-readback`) before the next starts.

| # | step | ships | the reading that gates the next |
|---|---|---|---|
| 0 | **Tiers, words, schemas** | a DEC for [10](10-tiers.md), [12](12-schemas.md), [15](15-map.md); GLOSSARY's Dreamer section rewritten to them, in their words only | — |
| 1 | **Signals, shadow** | the ingest signal stage, cues, the pending set, wake metrics; the clocks still run everything | a week of wake reasons; defaults sized from it |
| 2 | **Rollup and lineage** | co-location, outing, daybook, tie from existing presence and messages ([25](25-rollup.md)); stable citation keys; the `reasoning`, `lineage`, `exclude_persons`, `as_of` columns and the per-room lineage filter; `rollup_read`, `record_query` | each rollup recomputes identically from the Record; lineage refuses a presence-derived row in a room without the location flag |
| 2b | **Events unit-wide** | unit key, the lifecycle statuses, revision history with its reasoning mode, the event write verbs; the tag path's event tools on the new shape; `event_search` | existing gathering tests and the tag path's event cases green |
| 3 | **Cognition, shadow** | the session, agenda, the note, event and room-Character (`character_revise`, room case) verbs in dry-run (would-write rows recorded, nothing committed), skills from the objectives | the dinner case passes; over a shadow week every clocked-adaptor write is matched or deliberately closed |
| 4 | **Cut over** | cognition commits; clocks and the four orient adaptors retired; proactive is the act step; room culture is written by `character_revise` from here | the re-pointed adaptor eval suites green master vs branch; the latency falsifier ([00](00-intent.md) §6) |
| 5 | **Social and play ingestion** | the story poller for members only, and its perception; the story rule and its test rewritten; posts, reels and follows as the bridge gains each endpoint; Steam and Discord play samples, play session and play profile rollups | poll outcomes and item counts read back; the bridge session unharmed for a week |
| 6 | **Faces** | detector, embedder, templates from reference photos; labelling in the portal and in chat | precision on the held-out labelled set ([60](60-faces.md) §5) |
| 7 | **Memos** | the memo table, `memo_write`/`memo_withdraw`/`memo_read`, `<memos>`, the «記低» routing | `tool_selection` before and after |
| 8 | **Character and Standing** | person, unit and Nora Character; unit and person Standing; `character_read`, `standing_read`; the `self` block; the correction portal and `character_dispute` ([50](50-character-and-standing.md) §4); tension rows and their page ([55](55-self.md)); the nightly data-quality checks and the rebuild-drift metric ([30](30-cognition.md) §6) | a labelled set of accounts judged against the Record they cite |

Steps 5–6 are Observe and independent of 1–4; they may run in parallel.

## 3. What this supersedes

- **Design 39 and DEC-162** (the proactive adaptor as a separate Dreamer
  adaptor): its decide and act turns become cognition's act step.
- **The 2026-09-24 story rule** (design/instagram-adoption/18-instagram-read-and-search.md):
  stories are read by ingestion.
- **Design 43**: extended from three note passes to the whole loop, and its
  `WITHHELD_TOOLS` list (`nora/language/note_candidates.py`) retired: every
  read tool is granted and the budget is the control.
- **Design 27 §7.4** (the Dreamer never reads `where_been`) and its 90-day
  cap, for cognition only: cognition reads presence through `rollup_read`,
  under lineage ([25](25-rollup.md) §5).
- **Design 27 Tier 6** (co-location, speculative): built as a Rollup.
- **This session's first draft** (commit 0e060cf, a conductor over specialist
  agents).
- **Kept**: R-1b (two senders: the tag path and the fire path), the note
  brake (GLOSSARY **note**) (widened, [10](10-tiers.md) §2 rule 4), DEC-213
  (note body schema).

## 4. Open positions

| position | what would decide it |
|---|---|
| latitude in `core/identity.md`, `voice.md`, `belonging.md` ([55](55-self.md) §3) | the operator, before step 8 |
| one agent on the runtime model (a cheap model) holding a busy unit's day | step 3's shadow: agenda items closed per session, refusals per verb, context size |
| the unit as the read boundary for transcripts of all its rooms | the operator's 2026-09-09 ruling covers proactive; confirmed for cognition at step 0 |
| participation as a wake of the same loop | session latency measured after step 4 |
| a `delegate` verb for long work | the first backfill that needs it (faces, step 6) |
| posts, reels and follows endpoints on the bridge | each built and measured with the bridge |
| strands-agents 1.50.2 → 1.57.1 | its own PR, measured before and after |
| Honcho: one scope per unit inside the one workspace; told conclusions only in the prompt once Character ships ([80](80-prior-art.md) §6) | the Honcho version on norty (≥ 3.1.0), then the operator |
| retention of the Record (`[store] prune_enabled`) | prune only rows no live derived row cites, or leave a tombstone the citation resolves to |
| game-specific sources (CS2, Valorant, Apex) | after step 5; each a source feeding play samples |
| DEC numbers | taken at merge (`nora-decision`) |

## Status / Checkpoint

**2026-09-28 — proposed.** Operator confirmed: one agent for orient, decide and
act; events unit-wide and derived, cognition may revise, confirm and cancel;
no new statuses; stories, posts, reels and the social graph ingested; faces
now; passwords may be kept in memos; culture is the Character of a collective;
doctrine is law she never proposes to change, tension shown to the operator;
every derived row is schema'd Markdown.
Nothing is built.
