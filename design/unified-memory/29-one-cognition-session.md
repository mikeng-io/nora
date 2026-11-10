---
type: design
title: "One Cognition session: what it means, what it connects to, whether to act"
description: "Cognition is one stimulus-response session per wake that observes, orients, decides and acts. Proposals and acts are independently available. Profiles are proposed inside the same session. Memory updates at wake time. One verb set across every kind (create, revise, set_status, read, search) and two cross-kind graph tools (memory_search, memory_read) return a subgraph over the one new graph."
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
2. **Two intakes for one arrival.** Projection queues comprehend work for
   message batches, media, social items, Register entries, profile lines,
   co-locations, closed outings, closed response episodes and reminders
   (`nora/infra/sql/graph_projection.py`); ingest separately raises the
   stimulus Cognition wakes on (`nora/domain/stimuli.py`). Nothing ties the
   two, so step 3 can act on an item step 1 has not read. Individual stays and
   plays raise stimuli but no work item.
3. **Comprehension without a reason.** A lone "k" became work for a model on
   its own, out of the context that gives it meaning.

## 2. The session

One wake opens one session: observe what arrived and what remains open;
orient by searching memory and the notebook; decide whether anything is worth
doing, for whom and when; act through the offered tools, or remain silent.
Memory, profiles, notebook layers, Standing, reminders and self-wakes are
outputs of the same continuing judgment.

All offered tools are available at the start. `memory_search` and `memory_read`
connect the new input to held state before `memory_propose` saves statements.
Relations come first; a reading is the fallback when no relation can hold the
fact, and a correction addresses a contradicted shown relation. Chatter that
adds nothing beyond the recorded chat act needs no statement. Proposals contain
only statements, with per-statement saved/refused results and connected memory.
They do not declare input coverage or unlock acts. The relation and entity
vocabularies are derived from admission constants in the tool schema.

Act verbs are available independently of proposals. Their validators, disclosure
checks, provenance, idempotency and budgets still apply. Profile reads and
revisions connect changed relations to held accounts in the same session.
The historical lane offers evidence reads and memory proposals, with no act
verbs or current-profile access.

## 3. Completion, latency and the backfill

A session that ends on its own (`end_turn`) consumes the stimuli it was handed,
including when it writes nothing. There is no per-item close, coverage claim,
strike or write requirement. `max_items` is an input budget: unseen stimuli
are deferred, never dropped. A later look is scheduled with `wake_at`.

Failed, timed-out and bounded sessions leave their stimuli pending and apply
failure backoff. Saved statements and acts survive; retries use the existing
idempotency receipts. After five failed attempts a stimulus is parked for
operator requeue. Deferral is not failure. Cancellation preserves pending work.
Only a normal session outcome advances the live watermark; deferred inputs
are carried in the same transaction.

Historical replay records one completion receipt for a normally ended session,
with the exact inputs it observed. Resume checks those source identities and
hashes, independently of statement writes. A failed or bounded replay writes
no completion receipt. The historical lane keeps its own failure accounting
and never takes a live slot. Memory updates at wake time; strong cues can wake
quickly and quiet rooms are revisited after settling.

## 4. One vocabulary

Every Notebook kind (note, topic, lesson, profile, decision; memo as a note's
kept phase) sits on the one entry machinery (#379), so every kind gets the
same five verbs, all already in ADR 0015's verb list:

| verb | means | replaces |
|---|---|---|
| `<kind>_create` | a new entry | `note_create`, `memo_write`, `lesson_write` without a `lesson_id` |
| `<kind>_revise` | a new revision of the latest version (`stale_base`) | `note_update`, `memo_revise`, `event_revise`, `standing_revise`, `lesson_write` with a `lesson_id` |
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
| `memory_work_item` as the intake of understanding | stimuli are the intake; a Register write links itself in its own session (27 §7a); retries and parking move to the wake (§3) |
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

## 8. The proposal contract

`memory_propose` accepts a `statements` list: relations, corrections and readings.
Endpoints are shown `@kind:id` handles or inline `{new: …}` descriptors. Admission
derives relation persons from cited sources. Each statement is saved or refused
independently, with a primary refusal reason and repair guidance. A refusal does
not withhold valid siblings and makes no claim about whether the session has
finished. Session outcomes, not proposals, determine stimulus consumption.
