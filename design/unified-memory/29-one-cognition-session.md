---
type: design
title: "One Cognition session: understand, connect, revise and decide"
description: "Current wake-session contract over the memory graph, profiles and Notebook; statement-shaped proposals, evidence custody, actions and retry ownership."
tags: [nora, cognition, memory-graph, cms, stimulus, tools, vocabulary]
timestamp: '2026-10-29'
edges:
  - {dst: 27-the-memory-is-a-graph, rel: supersedes, provenance: asserted}
  - {dst: ../notebook-memory-types/00-design.md, rel: amends, provenance: asserted}
  - {dst: ../cognition-loop/session.md, rel: amends, provenance: asserted}
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

**Proposal contract.** `memory_propose(items, statements, nothing_here)` uses
self-contained relation, correction or reading statements. Endpoints are shown
`@kind:id` handles or inline new descriptors; authored identity pointers are
never created by the model. Code allocates ids and derives persons/source ids
from cited sources. [The statement contract](33-orient-kind-table.md) owns
representational eligibility; the schema and admission consume the same types.

Only source bodies actually rendered, after clipping/dropping, or explicitly
returned by reads are citable. A graph label alone grants neither the underlying
source body nor correction authority over an unseen relation. Each saved
statement keeps its evidence; each refused statement returns a primary reason
without suppressing valid siblings. A proposal's evidence scope is the union of
its named inputs; completion remains per input.

An item is covered by a saved statement citing one of its shown rows or by an
explicit `nothing_here`. Refused or unaccounted inputs stay open. The model
repairs still-grounded rejected statements without changing meaning to satisfy
a validator. Act tools remain locked until understanding has been admitted in
this session; a refusal is not permission to act.

**Profiles.** Proposed in this same session, with held profiles read first and
shown evidence/premises retained. Relations changing about a person may warrant
reconsidering their profile and relevant room/unit context; an outcome of Nora's
act may warrant revising her profile or practice. Relevance is judged from
actual connections, age, evidence and open state, not a model-invented score.
The [profile-surface handoff](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31)
owns promotion, writer retirement and acceptance; shadow receipts are not live
profile changes.

## 3. Completeness, latency and the backfill

- An agenda budget may carry stimuli to a later wake; it cannot consume them
  uncomprehended. A failed/refused session leaves unresolved stimuli pending.
  Failures are counted per stimulus; after five failed attempts work is parked
  for explicit operator requeue, not discarded.
- Every kind of stimulus is covered, including social items, stays and plays.
  Edits and late media can change inputs and warrant reconsideration. Committed
  understanding survives a later action failure; the next wake reads its state.
- Each Register writer persists its selected associations and links its own row
  in the same session. Its row is the source of truth. A failed link retries
  against that same row/act key, never by creating a duplicate affair.
- Historical backfill uses this session in its own lane, in source-time order,
  with act tools withheld. Reads obey the evidence instant; future knowledge is
  not imported. History cannot send, schedule or create present-day obligations.
  The subsequent catch-up selects still-open work under the real clock.

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

Instruction wording and runtime tool migrations require their own impact-based
validation. This document grants no new authority, model spend or deployment.
An integrated session measured worse than a separate comprehension step on the
same frozen sample reopens that design choice; argument alone does not.
