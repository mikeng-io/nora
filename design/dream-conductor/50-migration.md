---
type: design
title: "Migration — five steps, each shipped and read back alone"
description: "The order of the build, what each step measures before the next, what gets worse, and the positions left open (secrets in memos, the Strands version, Graph as a dispatcher)."
tags: [nora, dreamer, conductor, migration, shadow, eval]
timestamp: '2026-09-27'
edges:
  - {dst: 00-intent, rel: realizes, provenance: asserted}
---

# 50 · Migration

## 1. The steps

Each step ships alone, behind a flag that defaults off, and is read back live
(`nora-readback`) before the next starts.

| # | step | ships | the reading that lets the next step start |
|---|---|---|---|
| 0 | **Vocabulary** | a DEC row for memo / note / idea / gathering ([10](10-vocabulary.md)); GLOSSARY entries | the operator's answer on secrets (§3) |
| 1 | **Trigger, shadow** | the ingest cue stage and the per-unit pending set ([20](20-trigger.md)); it logs when a session WOULD fire and why; the clock still runs everything | a week of `nora_dream_wake_total{reason}`; defaults re-sized from it |
| 2 | **Conductor, shadow** | `wants()` on the five adaptors, the delta, the conductor's plan; logged beside what the clock actually ran | the coverage falsifier ([00](00-intent.md) §5) over ≥ 50 plans |
| 3 | **Cut over** | the session dispatches; the per-adaptor schedules are removed; the daily floor stays | latency and cost falsifiers; every existing dreamer eval suite green master vs branch |
| 4 | **Event specialist** | the candidate table, the specialist, its objective, its eval suite | stage/propose/evict precision on labelled rooms; no person-made gathering touched |
| 5 | **Memo tier + specialist** | the memo table, `memo_write`/`memo_read`, the `<memos>` block, the specialist, the curator's memo discharge, the «記低» routing change | `tool_selection` before and after (`nora-tool`); memo precision on labelled rooms |

Steps 4 and 5 are independent of each other and could land in either order
after 3. They could also land BEFORE 3 as ordinary clocked adaptors, if the
operator wants the capability before the refactor; the port change in
[40](40-specialists.md) §1 is small enough that they would move over with the
rest.

## 2. What gets worse

- **A new single point of judgement.** A plan that prunes wrongly drops a job
  every clocked adaptor would have run. The fallback (a refused plan runs the
  whole wanted set) and the daily floor bound it; the coverage metric
  measures it.
- **One more model call per busy session.** Paid only when two or more
  specialists want the change; step 2 measures whether the removed re-reads
  cover it.
- **Coupled failures.** Today a broken culture pass cannot delay the note
  pass. Under a session, jobs are isolated but share a unit's single flight:
  a slow job delays the next session for that unit. The per-job timeouts
  already in `AdaptorBudget` bound it.
- **A bigger cut-over.** Step 3 removes the schedule path in one change.
  Steps 1 and 2 exist so it is a switch, not a rewrite.

## 3. Open positions

| position | what would decide it |
|---|---|
| **secrets in memos** (refuse / flag / explicit «記低» only) | the operator; step 5 waits on it |
| **evict** of a person-made gathering | kept out here ([40](40-specialists.md) §2); reopen only with a labelled case where a note was the wrong answer |
| **Strands version.** The tree pins `strands-agents==1.50.2`; 1.57.1 is current (2026-09-25). Nothing in this bundle needs the newer one: `Graph`'s node input, `Swarm`'s hand-off and `as_tool` behave the same for this design's purposes, and 1.57's new delegation plugin is still agent-as-tool (text in). The upgrade touches the hooks `DreamGuard` and the factory use, so it is its own change with its own eval, not part of this one | a separate upgrade PR, measured before and after |
| **`Graph` as the dispatcher** | whether a custom node that renders from the stores (not from predecessors' text) keeps the guards; worth it only if resume or parallelism is needed beyond step 3's plain dispatch |
| **the Standing event fold as a specialist** | whether per-room Standing and the unit session would share a delta profitably; nothing here needs it |
| DEC numbers | taken at merge (`nora-decision`) |

## Status / Checkpoint

**2026-09-27 — proposed.** Operator chose D and the Strands SDK; the event
verdicts (stage / propose / evict) are the operator's. Nothing is built.
