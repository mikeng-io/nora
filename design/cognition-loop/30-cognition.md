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
  photo shows, whose faces, with confidence); the index lines of the Register
  rows those items touch; the unit's clock in HK time; how long since the
  last session. Index lines, not bodies: the session reads a body when it
  needs one.
- **The agenda.** One item per thing that needs attention, computed by code
  from the signals: *room R has 23 new messages*; *event E's place is named
  in 3 messages since it was set*; *person P arrived at a place at 19:52*;
  *note N's anchor passed*; *4 new story frames from 2 members*; *the unit's
  culture was last revised 6 days ago*. An item states **what happened**,
  never what to conclude from it, and never a menu of possible actions.

An agenda with no items is no session: zero model calls, the rule every
adaptor keeps today.

## 2. What it holds

| class | verbs | leaves the session how |
|---|---|---|
| **read** | every read tool the registry admits (`read_only_tools()`): transcripts, presence (`where_is`, `where_been`), `route`, places, memory (`about`, `ask_memory`), the Register reads, web, `media_fetch`, the social reads | returns into context |
| **write** | one family per tier: Register (`note_create`, `note_update`, `memo_write`, `memo_withdraw`, `event_propose`, `event_revise`, `event_set_status`), Character (`character_revise`), Standing (`standing_revise`) | code validates, then commits ([§3](#3-code-keeps-every-write-honest)) |
| **act** | `remind` (a reminder at a chosen time), `speak` (a message into a room of the unit now), `ask` (a question to a person, e.g. who is in a photo, [60](60-faces.md)) | a fire occasion through `FirePath.speak` in `nora/app/fire.py` — the one path that sends (R-1b), with its egress screens, rate cap and attribution |
| **close** | `close(item, outcome)` | ends one agenda item; the session ends when every item is closed or the budget is spent |

Write verbs reuse the tag path's where they exist (`note_create`,
`note_update`), so a note written by cognition and one written on a tag turn
pass the same validator. The act step is the proactive adaptor's decide and act
turns made one: deciding *whether and when* to speak happens in the same
context that just revised the event it would speak about.

## 3. Code keeps every write honest

The session is one agent; judging its writes is not left to it. Every write
verb, before it commits:

1. **Citations resolve** to Record ids this session was shown or fetched by a
   tool (design/dreamer-agent-loop/43-note-dreamers-are-research-loops.md §3's
   shown set).
2. **Citations point down** and **Nora's own rows ground nothing** they may
   not ground ([10](10-tiers.md) §2).
3. **The tier's own schema holds**: a note body in its sections (DEC-213), an
   event's fields, a Character account's form.
4. **Provenance is stamped**: session id, trace id, model, doctrine hash.
5. **History is kept**: every derived row is a revision chain; nothing is
   overwritten, so a wrong inference is one revision away from right.

A refused write is answered to the model with its reason (R-27
`unavailable`), counted, and never silently dropped.

## 4. Skills, not one long prompt

The base prompt is the Dreamer doctrine (what the runtime is), the situation
and the agenda. What each kind of work asks for — how to judge a note, when
a plan has moved, what a culture account is — lives in a **skill** the session
loads when its agenda holds that kind of item (ADR 0016's mechanism). The
current objectives under `nora/dreamer/objectives/` become those skills. A
session that only sees chatter never loads the note skill's 280 lines.

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

## 7. What stays outside the loop

- **The tag path** — the reflex. It answers when called, reads what cognition
  wrote, writes through the same verbs, and its debrief keeps revising the
  room's Standing inside the reply slot.
- **Participation** — deciding within seconds whether a quiet window is hers
  is too fast for a unit session. It stays as it is (off by default); a later
  step may make it a small-agenda wake of the same loop once session latency
  is measured.
- **Delegated long work** — a backfill of a year of photos, a face index
  rebuild. A later `delegate` verb hands those to the compute path; out of
  scope here.

## 8. Why no Strands multi-agent primitive

The session is one Strands `Agent` (`create_dream_agent` in
`nora/agent/factory.py`). `Swarm`, `Graph` and `Agent.as_tool()` all pass text
one model wrote into another model's prompt (read from strands-agents 1.50.2
and 1.57.1), which is the context boundary this design removes. Perception
models (vision, faces) are called as tools that return typed results, not as
agents. The SDK upgrade from 1.50.2 is independent of this bundle and ships
on its own measurement.
