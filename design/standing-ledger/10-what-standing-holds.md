---
type: design
title: "What Standing holds: only what the Record cannot give back"
description: "Each thing Standing carries today, sorted by whether it can be re-derived from the Record, and where each one lives after this bundle. The re-derivable part becomes pointers; the rest becomes the ledger. Consistent with live-threads 05: Standing is never the accumulator of what the room said."
tags: [nora, standing, record, notes, memos, transcript]
timestamp: '2026-10-25'
edges:
  - {dst: 20-the-ledger, rel: motivates, provenance: asserted}
  - {dst: ../live-threads/05-standing.md, rel: extends, provenance: asserted}
---

# 10 · What Standing holds

## 1. The test

For each thing, ask: **if Standing were deleted, could a turn get it back from
the Record** (messages, notes, memos, events, the Rollup) **by reading?** If
yes, Standing holds a pointer at most. If no, Standing is its only home, and
losing it is losing it.

## 2. The sort

| thing | re-derivable? | home after this bundle |
|---|---|---|
| what the room is talking about, beyond the 20-row window | yes, by reading more transcript | **threads** in Standing: a pointer per topic (rooms, message range, last moved), never a restatement ([20](20-the-ledger.md) §4) |
| a question the group has open | yes, it is theirs | **note** (DEC-213), unchanged; a thread links it |
| a settled thing the group wants kept | yes | **memo**, unchanged |
| what she said | yes, it is a transcript row | nowhere new; a decision cites the row |
| what she is leaning toward and why ("Sai Kung, because it is on three of the four routes") | **no**, never said aloud, or said without its grounds | **decision** ([20](20-the-ledger.md) §2) |
| the options she weighed and how each scored | **no** | **decision**, citing the turn that scored them |
| what would change her mind | **no** | **decision** (`Reopen if`) |
| a decision she closed, and on what grounds | **no** | **decision**, closed, kept in its chain |
| what she owes ("I'll check the ferry times") | **no** | **commitment** ([20](20-the-ledger.md) §3) |
| something she found and has not yet given the room | **no** | an option inside a decision, or a commitment when she promised it; the free-standing working set retires |
| which tools ran, in what order | yes, from the turn record | **turn record** ([30](30-turn-records.md)); never in Standing |
| what a tool returned | yes, from the turn record | **turn record**; a decision cites it |

## 3. Why this is not the accumulator live-threads ruled out

[live-threads 05](../live-threads/05-standing.md) §2 found Standing cannot be
the accumulator of what the room SAID, and that "a chain of positions is a
history of what she *thought*". This bundle keeps that ruling and builds on its
second half. A decision is what she thought, with its grounds; the room's words
stay in the Record and the room's open questions stay in notes. The ledger
accumulates her judgements, which nothing else in the tree holds.

The same node's §3 found that a per-room paragraph hides which of five threads
moved. Threads here carry their own last-moved time, which is that finding's
fix on the Standing side; [live-threads 04](../live-threads/04-progress-and-exits.md)
§2 is the same fix on the note side, and a thread that has a note links it
instead of repeating its age.

## 4. The rule that ends the overlap with notes

**If the group would recognise it as their question, it is a note. If it is
her stance on it, it is a decision.** "Where are we eating on Halloween" is a
note. "I'd put Sai Kung first, because …" is her decision on that note, and
the decision names the note by id. One question never has two homes, which is
the same rule [12-schemas](../cognition-loop/12-schemas.md) §3 already applies
between a note's `Owed` (what members owe each other) and Standing's `I owe`
(what she owes).

## 5. What retires

- **`Where it stands` as a folded paragraph.** Its two jobs split: "what is
  going on" becomes threads, "what she thinks" becomes decisions. Nothing is
  folded onto an earlier fold again.
- **The free-standing working set** (`Found, not yet given`,
  [standing-compact 30](../standing-compact/30-working-set.md)). Its measured
  case (a search returned several restaurants, the reply sent two) is exactly
  a decision's `Options`, with the scores the working set had no room for.
- **`recall_lookups`**, once [30](30-turn-records.md) §5's read replaces the job
  DEC-112 gave it ([50](50-migration.md) step 5).
- **`rebuild_state`**, the never-implemented stub in
  `nora/domain/tag_trace_rederive.py`. It existed because the head was a lossy
  fold that might need rebuilding from traces; a head that is never folded has
  nothing to rebuild.
