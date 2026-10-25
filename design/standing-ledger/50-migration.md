---
type: design
title: "Migration: six steps, each shippable, each gated by a measurement"
description: "The order the ledger ships in: read the gaps, keep every turn's record, add the ledger beside today's Standing, switch the writers, retire the paragraph and the working set, then retire recall_lookups. What is superseded and what stays open."
tags: [nora, standing, migration, recall-lookups, steps]
timestamp: '2026-10-25'
edges:
  - {dst: 40-verification, rel: depends_on, provenance: asserted}
---

# 50 · Migration

Each step ships alone and leaves Nora working. Nothing is removed before its
replacement has measured at least as well.

| step | ships | gate |
|---|---|---|
| **0. Read the gaps** | no code: confirm what `tag_trace.messages` keeps (does any reasoning block survive the sanitiser; how often the narration cap binds), and the size of a typical turn's tool results | a written read-back in the step-1 PR |
| **1. Every turn keeps a record** | the fire path keeps its trace; `CognitionAgentRunner` returns its exchange and the session persists it; `ToolCallLog` logs every tool, not only `cacheable` ones; the turn-record view with per-item lineage; the handle on her own transcript rows ([30](30-turn-records.md) §3); the `turn(...)` cite in the validator | rows written for tag, fire and Cognition turns, read back on the deploy host; the per-item lineage negatives ([40](40-verification.md) §5); the handle on her rows changes every prompt that renders her rows, so the eval suite runs before and after it |
| **2. The ledger, beside today's Standing** | the decision, commitment and thread tables and their revision chains, room and unit scope, ops through the one validator with a required base and an atomic compare-and-append, the render ([20](20-the-ledger.md) §5); the `thread` argument on `load_transcript` and `room_read`; the old paragraph still rendered | retention test ([40](40-verification.md) §4) exact; the lineage and stale-write negatives (§5); `tool_selection` for the `thread` argument (§6) |
| **3. The writers switch** | the debrief emits ops instead of a paragraph (a doctrine change to `nora/dreamer/objectives/standing-debrief.md`, eval-gated); the tag-path op tool; Cognition's `standing_revise` emits ops | work recall and topic recall beat master (§1, §3); `tool_selection` for the op tool (§6) |
| **4. The paragraph and the working set retire** | `Where it stands` and `Found, not yet given` stop being written and rendered; existing rows still read back and are never rewritten ([12-schemas](../cognition-loop/12-schemas.md) §6's rule); `rebuild_state` deleted | the step-3 numbers hold with the paragraph gone |
| **5. `recall_lookups` retires** | the turn-record read tool ([30](30-turn-records.md) §5) ships; `recall_lookups` is removed; DEC-112 amended to name the ledger and the read as her evidence; `tool_call_log` stays as the turn record's source | being contradicted does not regress (§2); `tool_selection` for the read (§6) |

## Superseded and amended

- [standing-compact 30](../standing-compact/30-working-set.md): the working set
  becomes a decision's `Options` (step 4).
- [12-schemas](../cognition-loop/12-schemas.md) §5: room Standing's sections
  and the unit sections are replaced by the three entry kinds; person Standing
  is unchanged. A pointer goes into that node when step 2 lands.
- DEC-112 (step 5), by its own decision row at merge time
  (`design/09-decision-index.md`); no number is taken before then.

## Open

| question | who decides | when |
|---|---|---|
| the render budget per section, and the closed-decision horizon | measured: the first month of rows | after step 3 |
| whether the debrief keeps running on every tag turn once the in-turn op tool exists, or only when the turn wrote no op | measured: how often a turn that decided something wrote no op | after step 3 |
| retention of turn records beyond `tag_trace`'s | the operator | before step 5 |
| how an entry moves from room to unit scope: on the second room's touch automatically, or by the writer's op | design review of step 2 | step 2 |
