---
type: design
title: "Cognition — one session per unit per wake: orient, decide, act in one context"
description: "What the session is handed (the situation and the agenda), what it holds (every read tool, write verbs per tier, act verbs through the fire path, a closing verb), how code keeps every write honest, skills instead of one long prompt, the assess pass after, and what stays outside the loop."
tags: [nora, cognition, ooda, agent-loop, strands, write-verbs, proactive, skills]
timestamp: '2026-09-28'
edges:
  - {dst: 40-register, rel: depends_on, provenance: asserted}
  - {dst: 50-character-and-standing, rel: depends_on, provenance: asserted}
  - {dst: ../dreamer-agent-loop/39-proactive-ooda-loop.md, rel: supersedes, provenance: asserted}
---

# 30 · Cognition

One Strands agent session per unit per wake ([20](20-observe.md) §3). It
orients, decides and acts in one context, so no conclusion is lost between
steps and no state is handed from one model to another.

## 1. What code hands it

Built once per session, in code, before any model call:

- **The situation.** The Record items that raised the pending signals, each
  with its author, its room, **its age**, its cues and its perception (what a
  photo shows, whose faces, with confidence); the Rollup rows that raised
  signals (a co-location started, a deviation); the index lines of the
  Register rows those items touch; the unit's clock in HK time; how long
  since the last session. Index lines, not bodies: Character, Standing and
  Register bodies are pulled with read tools when the session needs them,
  never pushed ([15](15-map.md) §3–4).
- **The agenda.** One item per thing that needs attention, computed by code
  from the signals: *room R has 23 new messages*; *event E's place is named
  in 3 messages since it was set*; *a co-location of 3 members began at a place at 20:05*; *P's week deviates from their rhythm*;
  *note N's anchor passed*; *4 new story frames from 2 members*; *the unit's
  culture was last revised 6 days ago*. An item states **what happened**,
  never what to conclude from it, and never a menu of possible actions.

An agenda with no items is no session: zero model calls, the rule every
adaptor keeps today.

## 2. What it holds

| class | verbs | leaves the session how |
|---|---|---|
| **read** | every read tool the registry admits (`read_only_tools()`), plus the warehouse queries of [15](15-map.md) §4: `record_query`, `rollup_read`, `event_search`, `memo_read`, `character_read`, `standing_read`, `social_items`; presence, `route`, places, memory, web, `media_fetch` | returns into context |
| **write** | one family per tier: Register (`note_create`, `note_update`, `memo_write`, `memo_withdraw`, `event_propose` (status `staged` or `proposed`), `event_revise`, `event_set_status`), Character (`character_revise`), Standing (`standing_revise`) | code validates, then commits ([§3](#3-code-keeps-every-write-honest)) |
| **act** | `remind` (a reminder at a chosen time), `speak` (a message into a room of the unit now), `ask` (a question to a person, in a room of the unit, e.g. who is in a photo, [60](60-faces.md)) | a fire occasion through `FirePath.speak` in `nora/app/fire.py` — the fire path, one of the two senders (R-1b), with its egress screens, rate cap and attribution |
| **close** | `close(item, outcome)` | ends one agenda item; the session ends when every item is closed or the budget is spent |

Write verbs are shared with the tag path: `note_create`/`note_update` as
today, and the tag path's event tools (`create_event`, `update_event`,
`cancel_event`) are renamed to the event verbs above at step 2, so a note or
event written by cognition and one written on a tag turn pass the same
validator. The act step is the proactive adaptor's decide and act
turns made one: deciding *whether and when* to speak happens in the same
context that just revised the event it would speak about.

## 3. Code keeps every write honest

The session is one agent; judging its writes is not left to it. Every write
verb, before it commits:

1. **Citations resolve** to ids this session was shown or fetched: Record
   ids, plus lower-tier rows where [10](10-tiers.md) §2 rule 1 allows them. At
   least one is a Record id (rule 2).
2. **Citations point down** and **Nora's own rows ground nothing** they may
   not ground ([10](10-tiers.md) §2).
3. **The kind's schema holds** ([12](12-schemas.md)): fixed sections, cited
   lines, absolute dates only (a relative time word in a date field is
   refused), refused never repaired.
4. **No re-recording**: a Register or Character line that only restates one
   Record row is refused; the Record is already searchable
   ([80](80-prior-art.md) §4).
5. **Provenance is stamped**: session id, trace id, model, doctrine hash.
6. **History is kept**: every derived row is a revision chain; nothing is
   overwritten, so a wrong inference is one revision away from right.

A refused write is answered to the model with its reason (R-27
`unavailable`), counted, and never silently dropped.

## 4. Skills, not one long prompt

The base prompt is the Dreamer doctrine (what the runtime is), the situation
and the agenda. What each kind of work asks for — how to judge a note, when
a plan has moved, what a culture account is — lives in a **skill** the session
loads when its agenda holds that kind of item (ADR 0016's mechanism). The
adaptors' objectives (`note-candidates.md`, `note-curation.md`,
`note-merge.md`, `culture.md`, `proactive.md`) become those skills;
`standing-debrief.md` stays the debrief's. A session that only sees chatter
never loads the note skill's 280 lines.

## 5. Budgets

Per session: turns, total and output tokens, a wall clock, and a cap on tool
calls over every verb together, all in `DreamGuard`, all in code, none stated
in the prompt (a model told of a cap declines work, measured on the proactive
pass). A session that runs out closes nothing further; its open items stay
pending with their signals and head the next session's agenda.

## 6. The assess pass

Judging an act belongs outside it (design/dreamer-agent-loop/17-failure-classes.md
§2). After a session, off any live path:

- **The strong measure is people.** A Register row a person later corrects
  ("no, we went to C"), a message Nora sent that nobody answered, a question
  she asked twice: each is counted against the session that wrote it
  (`nora_cognition_corrected_total{tier}`).
- **A cheap reader** samples finished sessions for writes whose citations do
  not say what the write claims. It reports; it never edits.
- **Data-quality checks, nightly, in code** (the analogue of warehouse tests):
  citations that no longer resolve; one happening recorded as two events
  across rooms; a Rollup row a rebuild computes differently; a happened
  event whose place differs from its outing's; sections past their age
  horizon; an induced line citing no aggregate. Each a counter
  (`nora_dq_failures_total{check}`).
- **Rebuild, not only fold.** Every Character account is periodically
  rebuilt from the Rollup and a cited window of the Record, and the rebuild
  is diffed against the folded chain; the difference is the drift metric.
  Folding alone was measured to drop what it should keep (Standing dropped
  open plans, #565).

## 7. What stays outside the loop

- **The tag path** — the reflex. It answers when called, reads what cognition
  wrote, writes through the same verbs, and its debrief keeps revising the
  room's Standing inside the reply slot, through `standing_revise`.
- **Participation** — deciding within seconds whether a quiet window is hers
  is too fast for a unit session. It stays as it is (off by default); a later
  step may make it a small-agenda wake of the same loop once session latency
  is measured.
- **Delegated long work** — a backfill of a year of photos, a face index
  rebuild. A later `delegate` verb hands those to the compute path; out of
  scope here.

## 8. Why no Strands multi-agent primitive

The session is one Strands `Agent` from `create_dream_agent` in
`nora/agent/factory.py`, which gains a `skills_dir` for §4's skills (it has
none today, by design). `Swarm`, `Graph` and `Agent.as_tool()` all pass text
one model wrote into another model's prompt (read from strands-agents 1.50.2
and 1.57.1), which is the context boundary this design removes. Perception
models (vision, faces) are called as tools that return typed results, not as
agents. The SDK upgrade from 1.50.2 is independent of this bundle and ships
on its own measurement.
