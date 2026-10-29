---
type: design
title: "One Cognition session: what it means, what it connects to, whether to act"
description: "The operator's 2026-10-29 ruling. Cognition is one stimulus-response session per wake that comprehends, connects and decides as one continuous act, replacing the three separate step sessions of 27 §7 and the per-item comprehend runner. The model proposes its understanding first; code admits it and answers with what it connects to; only then do the act tools work. Profiles are proposed inside the same session. Memory updates at wake time. One verb set across every kind (create, revise, set_status, read, search) and two cross-kind graph tools (memory_search, memory_read) that return a subgraph over the one new graph. Docs only, not built."
tags: [nora, cognition, memory-graph, cms, stimulus, tools, vocabulary]
timestamp: '2026-10-29'
edges:
  - {dst: 27-the-memory-is-a-graph, rel: supersedes, provenance: asserted}
  - {dst: ../notebook-memory-types/00-design.md, rel: amends, provenance: asserted}
  - {dst: ../cognition-loop/30-cognition.md, rel: amends, provenance: asserted}
  - {dst: 28-provenance, rel: depends_on, provenance: asserted}
---

# 29 · One Cognition session

Status: **ruled** by the operator, 2026-10-29. Docs only: no code, tool or
schema changes until this node is read and the build is briefed.

This supersedes [27](27-the-memory-is-a-graph.md) §7 (three separate step
sessions) and its "comprehend on arrival" ruling of 2026-10-28. It also
supersedes §14's tool names and the verbs [notebook-memory-types/00](../notebook-memory-types/00-design.md)
§7 left open. The graph stores, layers, vocabulary, admission rules and
evidence of 27 stand.

## 1. What the operator ruled, and why

> "The overall cognition runner answers questions that are already clear.
> For this batch of stimuli: what does this mean, what does it connect to,
> should I do something? This is an integrated whole." (operator, 2026-10-29)

The worked example: someone arrives downstairs. *What it means:* they are
nearby. *What it connects to:* the gifts bought for them on a trip, an open
thing. *Whether to act:* message them; get the gifts ready. If it connects to
nothing open, do nothing.

27 §7 split this into three bounded sessions ("never one prompt asked to read,
profile and plan at once"), and the code built two drivers: a per-item
comprehend runner (steps 1 and 2) and the Cognition runner (step 3). That
split rested on an argument, never a measurement. Read back in the code on
2026-10-29, it had three costs:

1. **The same words were interpreted twice.** Step 3's agenda block renders
   the stimulus messages' own words (`nora/language/cognition_render.py`), and
   step 1 read the same words separately. Step 3's input had no block for what
   step 1 understood.
2. **Not every stimulus was comprehended.** Comprehend work items were created
   only for message batches and Register writes
   (`nora/infra/sql/graph_projection.py`). Social items, stays and plays raise
   stimuli (`nora/domain/stimuli.py`) but no work item.
3. **Comprehension without a reason.** A lone "k" became work for a model on
   its own, out of the context that gives it meaning.

## 2. The session

```
stimuli accumulate ─► wake rules (settled, strong cue, batch, max_age, time, self)
                          │
                          ▼
        ONE Cognition session over the unit's pending stimuli
        ┌──────────────────────────────────────────────────────────┐
        │ context: the batch, what is open, the graph around it     │
        │ 1. understand + connect   → memory_propose (+ profile_*)  │
        │      code admits, answers: admitted, refused (why),       │
        │      and the subgraph the admitted items connect to       │
        │ 2. decide                 → act tools, now unlocked       │
        │      or nothing, when it connects to nothing open         │
        └──────────────────────────────────────────────────────────┘
                          │
                          ▼
        one session record: what was understood, what was decided
```

**The trigger is stimulus and response.** The existing stimulus table and
wake rules decide when a session starts. No per-item queue drives a model.

**Context, assembled by code** (one `_BLOCKS` table, as every occasion):

| block | carries | exists today in |
|---|---|---|
| self / profile, clock | who Nora is in this unit; the time | Cognition |
| agenda | the batch: the stimuli and their words, each with its time | Cognition |
| note_index, events, memos | what is open | Cognition |
| **graph** | the subgraph around the batch's people, names, places and topics, in 27 §14's format: layer word, evidence counts, state and time on every row | step 1's `graph_preview`, moved in |

**Tools**, under §4's vocabulary:

| group | tools | when |
|---|---|---|
| read | `memory_search`, `memory_read`, `<kind>_read` / `<kind>_search`, `media_fetch`, the social reads, `web_search` / `web_fetch` (egress-screened, capped) | any time |
| propose | `memory_propose` (readings, entities, relations, evidence, corrections), profile changes through `profile_*` | first |
| act | `note_*`, `event_*`, `lesson_*`, `topic_*`, and the rest of §4's kind verbs; `wake_at` / `wake_move` / `wake_cancel` | only after a `memory_propose` was admitted in this session |

**The order is enforced, not requested.** Act tools refuse until
`memory_propose` has been admitted in the session. "Should I act" is decided
on committed understanding, within the same continuous session.

**`memory_propose` answers back.** It runs admission on the spot and returns:
- what was admitted;
- what was refused, each with its reason in the schema's own words, as
  #381 does for shape;
- the subgraph the admitted items connect to (`memory_read` of them, depth 1).

That answer is the step from "connect" to "decide". It also ends the silent
refusals that spike runs 5–6 and the 2026-10-29 smoke measured: there,
admission ran after the session closed and the model never learned why.

**Relevance is not a model-made score.** A number the model invents for "how
relevant" is a proxy it grades itself on. The decision rests on what code
shows it: each connection's evidence count and age; whether it reaches
something **open** (an open note, a planned event, an unkept commitment); time
proximity (clock, event times); topic weight per person (counted evidence,
#379). The model decides and cites what the decision rests on.

**Profiles are proposed in the same session** (operator, 2026-10-29). Noticing
"this changes what I think of them" is part of the same act. They are
recorded in shadow until phase 7 switches the profile surface.

## 3. Completeness, latency and the backfill

- **Every stimulus is comprehended, never only decided on.** A wake's agenda
  cap may defer a stimulus to a later wake (carry); it may never skip its
  comprehension. A stimulus is consumed only by a session that admitted a
  proposal covering it, or explicitly recorded nothing to understand ("k").
  Social items, stays and plays are covered because they are stimuli.
- **Memory updates at wake time** (operator, 2026-10-29), not within seconds
  of arrival. Strong cues already wake quickly; a quiet room updates when it
  settles.
- **The backfill is the same session** over historical stimuli in time order,
  in its own lane, with the act tools withheld (27 §10: no notes, reminders or
  sends from history).

## 4. One vocabulary

Every Notebook kind (note, topic, lesson, profile, decision; memo as a note's
kept phase) sits on the one entry machinery (#379), so every kind gets the
same five verbs, all already in ADR 0015's verb list:

| verb | means | replaces |
|---|---|---|
| `<kind>_create` | a new entry | `note_create`, `memo_write`, `lesson_write` |
| `<kind>_revise` | a new revision of the latest version (`stale_base`) | `note_update`, `memo_revise`, `event_revise`, `standing_revise` |
| `<kind>_set_status` | any lifecycle move: end, keep, retire, withdraw, dormant | `note_retire`, `memo_withdraw`, `lesson_retire`, `event_set_status` |
| `<kind>_read` | one entry by id | `note_read`, `memo_read`, `standing_read`; `profile_read` replaces `about` |
| `<kind>_search` | find entries of that kind | `note_search`, `event_search` |

Three distinct acts the verb list already defines stay: `event_propose`,
`event_rsvp`, `profile_dispute`. `note_merge` stays until the routing
measurement shows `set_status` can carry it without losing merge's behaviour.

**Cross-kind graph tools** read the one new graph (27's entities, relations
and evidence), using the old index's hybrid search only to find where to start:

| tool | answers | returns |
|---|---|---|
| `memory_search(query, depth=2, kinds?, person?, since?, until?)` | what is relevant to this question, of any kind | a subgraph in 27 §14's format |
| `memory_read(kind, id, depth=1)` | what does this one thing connect to | the same format, seeded from one node; with depth 0, the full fields and evidence text |
| `memory_propose(...)` | here is what I understood | admitted, refused (why), connected subgraph |

Depth is the model's to choose: default 2, ceiling 4 (27 §12), with node and
per-relation fan-out caps. A capped walk answers `partial`, never silently
short. The scope rules of 25 §4 apply inside the walk. The memory tools
discover and connect across kinds; the kind tools act on one kind's entries.

**Retired names:** `memory_stage` and `profile_stage` (become
`memory_propose` and `profile_*`); step 1's local `memory_search` (the
registered one moves onto the new graph); the phantom `memory_read` listing,
until the real one exists. The Honcho-era `about`, `ask_memory`,
`search_memory`, `search_memory_workspace`, `who_said`, `lookup_person` and
`recall_lookups` retire with Honcho (phase 8); until then `profile_read` is
the profile read.

**The naming gate covers session-built tools.**
`tests/architecture/test_tool_names.py` checked only `NATIVE_TOOLS`, so every
tool a session built for itself escaped it. That is how `stage`, `write`,
`withdraw`, `retire` and `merge` drifted in. It extends to them.

## 5. What is retired, and what stays

| retired | why |
|---|---|
| the per-item comprehend runner as a driver | the wake is the driver (§2) |
| `memory_work_item` as the intake of understanding | stimuli are the intake; a Register write links itself in its own session (27 §7a) |
| the separate reshape session | profiles are proposed in the session (§2) |
| "comprehend on arrival" (2026-10-28) | memory updates at wake time (§3) |

**Stays:** the graph stores, projection and Rollup; admission and the
relation vocabulary (#375); shape errors returned to the model (#381);
person-pointer mapping and evidence targets (#383); the topic kind and the
entry store (#379); the PROV reference (28).

## 6. The spike, re-scoped

The spike measured per-item step 1 on its own, the unit this node retires.
It now measures **the integrated session**, replayed over the snapshot's
stimulus batches with the act tools withheld. The 27 §11 rows and their marks
carry over (reading and relation precision, wrong merges, egress, acts, lost
and failed work, query p95), and so do the judges (`cx/gpt-6.1-sol`,
`kmc/k3-256k`). A smoke with fixed marks (#383) runs first, from the
projected dump, before any full run.

## 7. What this node does not decide

| open | decided by |
|---|---|
| the instruction text | the build PR, measured by the smoke and the spike |
| the order the renames ship in | ADR 0015's `tool_selection` before and after, per rename |
| how a struck stimulus is marked "nothing to understand" | the build PR, with a named test that a struck stimulus is never consumed uncomprehended |

**This holds unless** the integrated session, measured on the same snapshot
sample, is clearly worse on reading or relation precision than the separate
step 1 was. Then a separate memory step returns, with that measurement behind
it.
