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
Current acceptance and delivery order are owned by [§6](#6-bounded-cutover-acceptance-2026-10-31); earlier phase/gate wording below is qualified by that amendment.
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

**Eval cases replay recorded wakes from the eval snapshot** (operator, 2026-10-30).
Each names `metadata.wake: <cognition_wake id>` and a `room_*` alias.
The sandbox is restored as of that wake's time and the session receives the
wake's own stimulus batch through live agenda assembly, never a summary sentence.
A missing wake is ERROR. Unanchored scenarios remain parked until a recorded
wake demonstrates the scenario; no fake model input is substituted.

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
| 1 | **The suite.** a new corpus, cognition_session, beside the others in `evals/corpora` (anchored cases, held-out marked), the code tests of §2 as failing or skipped placeholders named for their scenario, the scorer | the census (active, parked, held-out), recorded-wake anchors; no baseline against the old system, whose required tools did not exist and therefore measured nothing |
| 2 | **`memory_search` and `memory_read` on the one graph.** The registered `memory_search` seeds from the index's hybrid search, walks the new graph, and returns 27 §14's subgraph with depth (default 2, ceiling 4), caps and `partial`; `memory_read` is new; step 1's local `memory_search` is deleted | scope tests (unit, listed persons, presence, social opt-out) inside the walk; the walk's query plan uses its indexes; p95 at depth 3 under 200 ms on the projected dump; ADR 0015 `tool_selection` before and after |
| 3 | **The session.** The `graph` block joins Cognition's `_BLOCKS`; `memory_propose` (admission in the call; the answer: admitted, refused with reasons, connected subgraph); act tools refuse until a proposal is admitted; `profile_*` inside the session, in shadow; retries, parking and re-link by id (29 §3); stays and plays covered; the backfill lane with act tools withheld; the per-item runner and the separate reshape session deleted, **with their one other caller, the spike harness, moved onto the session in the same PR** | §2's code tests green; named tests that a stimulus is consumed only by an admitted proposal covering it or an explicit nothing-to-understand, that agenda carry and a strike keep it pending until comprehended, that a partial proposal consumes only what it covers, that a failed or refused session leaves its stimuli pending, that five failures park it with the operator requeue, and that a retried link write re-links the existing Register row by id; the gate test (an act before an admitted proposal is refused); scenario 9's no-acts test; recorded-wake eval cases run and reported, with unanchored cases parked |
| 4 | **The vocabulary.** 29 §4's renames over every kind; `tests/architecture/test_tool_names.py` extended to session-built tools; Honcho-era names left for phase 8 | the naming gate fails on a seeded misnamed session tool (positive control); `tool_selection` before and after **per rename**, a rename that worsens routing does not ship |
| 5 | **Worked examples.** Three or four in the instruction, the shape of the reasoning (understand, propose, read the answer, decide or not), at least one "do nothing" | the suite before and after on the held-out cases; an example that lowers them is removed |
| 6 | **The spike, re-scoped.** On the harness PR 3 moved onto the session: it replays the snapshot's stimulus batches with act tools withheld; `--smoke` first from the projected dump with `SMOKE_MARKS`; then the 27 §11 rows with the judges `cx/gpt-6.1-sol` and `kmc/k3-256k` | smoke PASS before the full run; the full report: every applicable §11 row, AGE adopt-or-not, token cost per wake/batch (the backfill budget). Retired: separate step 1/2 costs and step 2 session counts (one session now); per-work-item retries and lost-work states (worker retired; replaced by session completion and consumption without admission). Precision, recall, merges, enrichment, egress, zero acts and depth-3 latency remain. Missing evidence is ERROR/INSUFFICIENT, never PASS. `--compare` reports per-row deltas. |

PRs 4 (registered vocabulary) and 5 (worked examples) follow PR 3. The session's own new tools use 29 §4 names from the start.

## 4. After the spike

The backfill (phase 5) runs only on the operator's go-ahead, with the spike's
cost per batch as its budget. The query and presentation work of phase 6 is
largely PR 2. Profile cards from the session (phase 7) and the switch, the
14-day hold and Honcho's removal (phase 8) follow as 27 §15 orders them.

**Historical condition, resolved by the final §5 ruling and superseded by §6:**
PR 1's anchoring finds most scenarios have no real
instance in the clone; then the suite is too thin to gate the build, and the
operator decides whether to wait for live data or accept parked scenarios.


## 5. Source-audited acceptance amendments (2026-10-30)

The operator requested that the investigation be carried in the design itself.
[27 §17](27-the-memory-is-a-graph.md#17-implementation-findings-and-longitudinal-understanding-2026-10-30)
contains the source baseline, reproduced gaps and evidence limits. Incorporate
its F1–F6 acceptance into this build; this is not a second implementation track.

- **Session input:** preserve sender/mention/reply/source anchors; a current implicit
  reply must reach existing relevant context without spelling out its name. Inspect
  the actual assembled graph block, with scope/caps intact and a positive control.
- **Retrieval:** automatic prefetch and explicit tools must reach a new-graph-only
  semantic connection under equivalent scope, without transcript duplication or
  hiding partial/unavailable results.
- **Register:** a supported association to an existing matter survives creation and
  retry even when its literal name is absent. Retry re-links the same row.
- **Profiles:** shadow proposals remain shadow until the authorized surface switch.
  At that phase, read back both the live account and its appearance in a later turn;
  retain the existing separate adaptors until their planned replacement is proved.
- **Semantics:** assess the account and later answer across histories and new
  counterevidence, not merely successful admission or a tool name. Preserve §2's
  recorded-wake anchors, held-out cases and fail-closed scoring; generated code
  fixtures do not substitute for missing model-eval anchors.

[32](32-platform-observations.md) extends the source coverage design: collection,
projection and model exposure are distinct deliverables. Its contract journeys
belong in the eventual observation implementation; this amendment enables no
collector, profile promotion, extra model spending or deployment.

Operator, 2026-10-30: the anchored scenario suite is too thin to gate this build
(one confirmed match in 220 checks on the busiest wakes). The session spike
measures the session now and the worked examples before/after next. The operator
runs smoke first, then full, on the private host snapshot; no host data enters Git.

## 6. Bounded cutover acceptance (2026-10-31)

**Canonical acceptance owner.** The operator makes replacing Nora's Honcho
dependency the highest priority. Private friend-group traffic cannot supply
statistically conclusive long-term parity within a useful wait ("not possible
within a year"). This amends the fixed minimum populations and mandatory
population-parity switch gates in 00, 10, 18, 27, 45, 70 and 75; their earlier
wording remains historical. It extends §5's one confirmed match in 220 checks
and parked-scenarios ruling, not a claim that the model passed. The
[issue #435 clarification](https://github.com/mikeng-io/nora/issues/435)
keeps LongMemEval-S, GroupMemBench and Mem2ActBench (#429–#431) as parallel
quality work, with their own full-benchmark acceptance.

### What must hold

- **Engineering guarantees gate the replacement:** deterministic scope and
  disclosure, admission before action, no historical acts during backfill,
  preserved source/sender/mention/reply input, and no stuck or lost input.
  Failed/refused work stays pending or is explicitly parked and recoverable;
  retries preserve identity and do not duplicate the persisted matter.
  Wiring/instrument ERROR and known reachable correctness failures are repaired,
  never waived for low volume. Admission proves custody, not semantic truth.
- **Read the actual assembled input before changing instructions.** Verify
  source anchors, context age and authorship, scoped automatic recall and tool
  retrieval, semantic association persistence through write/retry/later read,
  and the profile/read-surface handoff. Before claiming replacement, read back
  the loaded configuration, persisted live profile/account and its use by a
  later turn; a shadow receipt is not that handoff. §5 and 27 §17 own the
  existing repair contracts.
- **Run the existing recorded-wake smoke, then one bounded full spike.** Smoke
  must pass before the full run. Bind reports to exact source revisions and
  snapshot boundaries. Freeze comparison populations before answers are seen;
  match source population, configuration and effective models/judges across
  arms, including blind different-vendor judgement, and record their actual
  resolved identities rather than trusting aliases. Report each applicable
  27 §11 row with its numerator/denominator (or measured quantity and population
  count for cost/latency), errors, exclusions and comparison deltas. Missing or
  failed instruments are ERROR; absent/too-small populations are INSUFFICIENT,
  never PASS. A replay beginning without prior session/carry is not evidence of
  full longitudinal understanding.
- **Quality remains measured and consequential.** Retain 27 §11's reading,
  relation, merge and enrichment targets on observed populations, and report
  answer grounding, reply preference and corrections on the available matched
  journeys. A missed target is a finding to investigate against assembled
  input, sources and later answers, with repair and remeasurement where it
  exposes a reachable correctness failure. Do not relabel every quality FAIL
  optional. The operator reviews the findings, dispositions and residual
  uncertainty before deciding cutover. Sample insufficiency alone neither
  blocks indefinitely nor proves population-wide parity; the former ≥80-pair,
  ≥80% answerability and <5% answer-error switch formula is superseded.
- **Use a small available set of complete real journeys:** retrieval →
  interpretation → persisted account → later answer or correction. Preserve
  the active/parked/held-out census and independent judgement; generated
  deterministic fixtures prove code guarantees, not real model anchors.
  Accumulate further real journeys during use. Any future extended-history or
  fragment-plus-context experiment is separately labelled, not already built
  or authorized to substitute for recorded-wake anchors. The operator can
  accept a bounded decision from these journeys and available-data evidence,
  with the limits explicit.

### Delivery and operational boundary

Practical core wiring (#441's scope), semantic links/profile handoff and the
planned backfill preparation come first; bounded validation supports the
operator's switch decision, followed by observation and removal. The pre-backfill
spike supplies **measured per-wake total tokens** for the backfill budget, not
retired step-1/step-2 estimates. No dollar budget is inferred without a billing
split and no cost is invented as free. Historical act tools stay withheld;
still-open catch-up follows [27 §10](27-the-memory-is-a-graph.md#10-one-backfill-built-in-time-order),
not a new historical-action lane.

Retain [70 §4](70-honcho-cutover.md#4-the-hold--14-days-00-sequence-step-9)'s
**14-day operational observation** after the authorized switch. Watch loaded
surfaces, input progress and projector/error readbacks; fix actual defects.
Low/no traffic proves no semantic quality and does not extend observation
forever solely to reach a sample quota. Report quality limitations separately.
Live switch, backfill and deployment still use their separate validated
procedure and concrete operator go-ahead; overall work authorization is not
an automatic switch. This docs amendment authorizes none of those operations.
Removal means Nora's dependency only; the Honcho server used by others stays.

Cross-session persistent judgements belong to the existing
[Notebook kinds](../notebook-memory-types/00-design.md): **note, topic, lesson,
profile, decision**, curated by the shared Cognition loop. Standing is the
common rendered concept, not a sixth store or a new agent. Preserve same-matter
identity and actual execution feedback across the unit; rooms retain source,
disclosure and destination. Coordinate overlapping contracts with platform
observations and bridge decoupling; their complete programs, full Notebook
migration, personalisation and all public benchmarks are not new cutover
prerequisites. This changes acceptance, not their implementation status.

**Reopen on evidence:** a reachable scope/input/persistence failure, an observed
unsupported interpretation or correction lost in a later answer, or operational
readback contradicting the replacement claim requires investigation and repair.
Sparse traffic alone does not reopen a demand for conclusive population parity.
