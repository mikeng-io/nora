---
type: design
title: "Building the one Cognition session: scenarios first, then the session, then the spike"
description: "The build brief for node 29. Scope and exclusions (the personalisation engine waits; its trigger and inputs are covered). Thirteen scenarios as the acceptance spec, split into eval cases anchored to real moments and code tests on generated data. Six pull requests in order: the scenario suite and its baseline, memory_search and memory_read on the new graph, the session with its propose-first gate, the vocabulary renames under the extended naming gate, measured prompt examples, and the re-scoped spike. What each one must measure before it merges. Docs only."
tags: [nora, cognition, memory-graph, build, evals, scenarios]
timestamp: '2026-10-29'
edges:
  - {dst: 29-one-cognition-session, rel: details, provenance: asserted}
  - {dst: 27-the-memory-is-a-graph, rel: depends_on, provenance: asserted}
  - {dst: ../personalisation/00-intent.md, rel: informs, provenance: asserted}
---

# 31 · Building the one Cognition session

Operator, 2026-10-29: scenarios before the build, so the session is measured
and not trusted. This brief orders the work for [29](29-one-cognition-session.md).
Nothing in production changes until phase 5's go-ahead: the graph's shadow
switches stay off and the backfill waits for the operator.

## 1. Scope

**In:** the one session per wake (29 §2), its propose-first gate,
`memory_propose` answering back, profile proposals inside the session,
`memory_search` and `memory_read` over the one graph, the vocabulary of 29 §4
with the naming gate extended, retries and parking under the wake (29 §3), and
the re-scoped spike.

**Out: the personalisation engine** ([personalisation](../personalisation/00-intent.md),
proposal, not built): ranking, recommendation types, feedback learning and
replay. Ranking and memory measured in one run could not tell a bad answer's
cause apart, and the engine reads what this build writes. Its **trigger** (an
open decision with options) and its **inputs** (interests and likes, recorded
once) are covered here as scenarios 11 and 12, and its no-reveal rule as 13.

## 2. The thirteen scenarios

Each scenario is either a **model judgement**, measured as an eval case, or a
**code guarantee**, measured as an ordinary test. Many are both: the code
guarantee holds whatever the model does, and the eval measures whether the
model takes the right path.

| # | Scenario | Must happen | Must not happen | Measured as |
|---|---|---|---|---|
| 1 | "I'm downstairs"; an open note says to hand the trip gifts over | propose the visit; the answer shows the note; act on it | act before proposing | eval; gate is a test |
| 2 | a lone "k" | an explicit empty proposal; no act | any link or note | eval |
| 3 | "k" just after "dinner at 7?" | a reading that the dinner is agreed; the plan linked | treating the "k" as nothing | eval |
| 4 | "Saturday's off, sorry" | a correction closes the Saturday plan; the open note updated | a new plan; deleting the old one | eval; no-delete is a test |
| 5 | an unannounced visit, the third time | `profile_revise` citing the three visits | a profile change after the first | eval |
| 6 | a concert ticket post, nothing after | `plans` → the concert | `attended` | eval (a ticket is not attendance: a judgement); the evidence's structural validation (present, shown, quoted span) is a test |
| 7 | talk about a person not in this room | the reading kept in this room's scope | a link exposing them beyond it | test (scope inside the walk) |
| 8 | a proposal with a wrong relation | the refusal read; re-proposed correctly or dropped | acting on the refused link | eval; the answer's refusal list is a test |
| 9 | the same batch replayed as backfill | readings and links recorded | any note, reminder or send | test (act tools withheld) |
| 10 | nothing open connects ("nice weather") | an admitted proposal; no act | an invented follow-up | eval |
| 11 | the group goes back and forth on Friday's dinner | an open decision with the options named | a recommendation | eval |
| 12 | someone keeps returning to racing, and names one game | `interested_in` → topic racing, weight grows with evidence; `likes` → that game | a profile line "likes racing" | eval; the weight count is a test |
| 13 | in a group room: "what does B like?" | answered from what B shared in this room | likes shared only elsewhere | test (scope); eval for the wording |

**Eval cases follow the suite's rule** (`evals/scenarios.py`): each names a
real room at a real moment in the deployment's clone, read at run time on the
host; nothing is committed but the reference. A scenario with no real
instance yet is **parked** with its reason and return condition, counted in
the census, never faked. Finding the anchors is private work on the host.

**Each case runs more than once** (a single run decides nothing; the
relevance-gate measurement), scored fail-closed: an empty or wrong path is
FAIL, a timeout ERROR, never a PASS. **A held-out set** of anchored cases is
never shown in the instruction, so the suite measures understanding, not
recall of its own examples.

## 3. Six pull requests, in order

Each is one integrated change with its own measurement. Builder and reviewer
are from different vendors (ORCHESTRATION.md).

| # | PR | Must show before merge |
|---|---|---|
| 1 | **The suite.** a new corpus, cognition_session, beside the others in `evals/corpora` (anchored cases, held-out marked), the code tests of §2 as failing or skipped placeholders named for their scenario, the scorer | the census (active, parked, held-out), and a **baseline run against today's system**: most cases fail, because the session does not exist; that is the number the build moves |
| 2 | **`memory_search` and `memory_read` on the one graph.** The registered `memory_search` seeds from the index's hybrid search, walks the new graph, and returns 27 §14's subgraph with depth (default 2, ceiling 4), caps and `partial`; `memory_read` is new; step 1's local `memory_search` is deleted | scope tests (unit, listed persons, presence, social opt-out) inside the walk; the walk's query plan uses its indexes; p95 at depth 3 under 200 ms on the projected dump; ADR 0015 `tool_selection` before and after |
| 3 | **The session.** The `graph` block joins Cognition's `_BLOCKS`; `memory_propose` (admission in the call; the answer: admitted, refused with reasons, connected subgraph); act tools refuse until a proposal is admitted; `profile_*` inside the session, in shadow; retries, parking and re-link by id (29 §3); stays and plays covered; the backfill lane with act tools withheld; the per-item runner and the separate reshape session deleted, **with their one other caller, the spike harness, moved onto the session in the same PR** | §2's code tests green; named tests that a stimulus is consumed only by an admitted proposal covering it or an explicit nothing-to-understand, that agenda carry and a strike keep it pending until comprehended, that a partial proposal consumes only what it covers, that a failed or refused session leaves its stimuli pending, that five failures park it with the operator requeue, and that a retried link write re-links the existing Register row by id; the gate test (an act before an admitted proposal is refused); scenario 9's no-acts test; the suite's eval cases run and reported against PR 1's baseline |
| 4 | **The vocabulary.** 29 §4's renames over every kind; `tests/architecture/test_tool_names.py` extended to session-built tools; Honcho-era names left for phase 8 | the naming gate fails on a seeded misnamed session tool (positive control); `tool_selection` before and after **per rename**, a rename that worsens routing does not ship |
| 5 | **Worked examples.** Three or four in the instruction, the shape of the reasoning (understand, propose, read the answer, decide or not), at least one "do nothing" | the suite before and after on the held-out cases; an example that lowers them is removed |
| 6 | **The spike, re-scoped.** On the harness PR 3 moved onto the session: it replays the snapshot's stimulus batches with act tools withheld; `--smoke` first from the projected dump with `SMOKE_MARKS`; then the 27 §11 rows with the judges `cx/gpt-6.1-sol` and `kmc/k3-256k` | smoke PASS before the full run; the full report: every §11 row, AGE adopt-or-not, cost per batch (the backfill budget) |

PR 4 may land before PR 3 if the session's own tools are named under 29 §4
from the start; the registered renames still need their routing numbers.

## 4. After the spike

The backfill (phase 5) runs only on the operator's go-ahead, with the spike's
cost per batch as its budget. The query and presentation work of phase 6 is
largely PR 2. Profile cards from the session (phase 7) and the switch, the
14-day hold and Honcho's removal (phase 8) follow as 27 §15 orders them.

**This holds unless** PR 1's anchoring finds most scenarios have no real
instance in the clone; then the suite is too thin to gate the build, and the
operator decides whether to wait for live data or accept parked scenarios.
