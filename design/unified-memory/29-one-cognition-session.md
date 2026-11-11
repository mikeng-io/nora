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
  - {dst: 50-learning-from-acts, rel: depends_on, provenance: asserted}
---

# One Cognition session

One stimulus-response session per wake comprehends, connects and decides as
one continuous act. This replaces separate arrival/comprehension, reshape and
act sessions. The [memory map](15-memory-map.md) owns the objects; the
[graph contract](27-the-memory-is-a-graph.md) owns admission and traversal;
[revision and learning](50-learning-from-acts.md) owns what new evidence changes.
This is the design contract, not a claim of live activation.

## 1. What the session answers

What changed? What does it connect to? Does it change an existing understanding,
a profile, an open affair or Nora's position? Is there a reason to act or return?
Understanding can change without a send, reminder, new task or new lesson.

Stimuli accumulate under the existing settled, strong-cue, batch, max-age,
time and self-wake rules. Memory updates at wake time. Code supplies related
state before the session; the model can read further through existing scoped
tools. A lone item is interpreted in its conversation, not in a second isolated
per-item model pass.

## 2. The session

One wake opens one session: observe what arrived and what remains open;
orient by searching memory and the notebook; decide whether anything is worth
doing, for whom and when; act through the offered tools, or remain silent.
Memory, profiles, notebook layers, Standing, reminders and self-wakes are
outputs of the same continuing judgment.

| Phase | Responsibility | Result |
|---|---|---|
| Assemble | Fixed agenda sources, clock, members, related profiles, graph and open/current state | One declared context-assembly occasion |
| Understand and connect | Read, resolve, enrich where useful, propose supported statements and corrections | Code admits each statement and returns saved/refused results plus connected subgraph |
| Maintain understanding | Read affected held profiles before revising; weigh new evidence against existing understanding | Governed profile/Notebook revisions or honest no-change/unknown |
| Decide | Consider grounded open work, current situation and own position | Permitted acts, return scheduling, or no act |

[The Cognition context](../context-assembly/05-the-cognition-context.md) owns
block selection, shared renderers, room binding and adaptive budgets. Every
model block explains its subject, author, time and state; empty and unavailable
remain distinct. The graph preview includes structural anchors and resolution
candidates within the current scope and budget, with truncation stated.

All offered tools are available at the start. `memory_search` and `memory_read`
connect the new input to held state before `memory_propose` saves statements.
Relations come first; a reading is the fallback when no relation can hold the
fact, and a correction addresses a contradicted shown relation. Chatter that
adds nothing beyond the recorded chat act needs no statement. Proposals contain
only statements, with per-statement saved/refused results and connected memory.
They do not declare input coverage or unlock acts. The relation and entity
vocabularies are derived from admission constants in the tool schema.

Only source bodies actually rendered, after clipping/dropping, or explicitly
returned by reads are citable. A graph label alone grants neither the underlying
source body nor correction authority over an unseen relation. Each saved
statement keeps its evidence; each refused statement returns a primary reason
without suppressing valid siblings. A proposal's evidence scope is the union of
its named inputs; completion remains per input.

Act verbs are available independently of proposals. Their validators, disclosure
checks, provenance, idempotency and budgets still apply. Profile reads and
revisions connect changed relations to held accounts in the same session.
Proposed in this same session, with held profiles read first and shown
evidence/premises retained. Relations changing about a person may warrant
reconsidering their profile and relevant room/unit context; an outcome of Nora's
act may warrant revising her profile or practice. Relevance is judged from
actual connections, age, evidence and open state, not a model-invented score.
The [profile-surface handoff](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31)
owns promotion, writer retirement and acceptance; shadow receipts are not live
profile changes.
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

Each Notebook kind uses `create`, `revise`, `set_status`, `read`, `search`.
Revision names the current base (`stale_base` refuses an old one); lifecycle
moves retain history. Existing distinct acts such as `event_propose`,
`event_rsvp` and `profile_dispute` keep their contracts. Legacy verbs/stores
remain only until their explicit migration; a documentation rewrite does not
rename runtime tools.

| Cross-kind tool | Responsibility |
|---|---|
| `memory_search(query, depth=2, kinds?, person?, since?, until?)` | Find seeds and return the connected scoped subgraph |
| `memory_read(kind, id, depth=1)` | Read connections; depth zero reads full eligible fields/evidence |
| `memory_propose(...)` | Admit understanding, report refusals and connected state |

Store-specific tools perform their typed reads and operations. All session-built
names fall under the same naming policy as registry-built tools. Tools retain
the graph depth/node/fan-out limits and explicit partial/unavailable results.

## 5. What is retired, and what stays

The per-item comprehend driver/intake, separate reshape session and
comprehend-on-arrival schedule are retired design paths. Stimuli and wakes own
intake/retries. Stores, Rollup, projection, reviewed vocabularies, admission,
source provenance and identity mapping remain. [One Standing](../notebook-memory-types/20-standing.md)
organizes completed Records and canonical results automatically; its updater
cannot invent decisions or take over Cognition's judgement authority.

## 6. The spike, re-scoped

[Build and acceptance](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31)
owns the integrated-session acceptance and delivery order. [Replay evidence](34-the-replay-records-why.md)
owns exact requests, tool trajectories, persistence evidence and judge inputs.
Evaluate supported understanding, later retrieval/correction and cost on the
same frozen chronological journey. Missing evidence is ERROR/unavailable, never
a semantic pass. Historical separate-step quotas are not new switch conditions.

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

Instruction wording and runtime tool migrations require their own impact-based
validation. This document grants no new authority, model spend or deployment.
An integrated session measured worse than a separate comprehension step on the
same frozen sample reopens that design choice; argument alone does not.
