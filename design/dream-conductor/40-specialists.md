---
type: design
title: "Specialists — the adaptors kept whole, two added"
description: "Line by line, what the DreamAdaptor port keeps and what it changes (it is the runtime that is refactored, not the adaptors); the event specialist's three verdicts (stage, propose, evict) and their limits; the memo specialist; the curator's memo discharge."
tags: [nora, dreamer, adaptor, specialist, gathering, event, memo, note-candidates]
timestamp: '2026-09-27'
edges:
  - {dst: 10-vocabulary, rel: depends_on, provenance: asserted}
  - {dst: ../dreamer-agent-loop/43-note-dreamers-are-research-loops.md, rel: extends, provenance: asserted}
---

# 40 · Specialists

## 1. Does D refactor the adaptors? The port, line by line

The answer is **no for the adaptors, yes for the runtime around them.** A
specialist is a `DreamAdaptor` (`nora/domain/ports.py`) run by
`DreamerRuntime.run` (`nora/agent/dreamer.py`), as today.

| part | today | under the conductor |
|---|---|---|
| `objective` (a file under `nora/dreamer/objectives/`) | per adaptor | **unchanged** |
| `AdaptorGrant`, `tools`, `tool_subset_faults`, room vs unit safety | per adaptor | **unchanged** |
| `AdaptorBudget` (`[dreamer.budgets.<name>]`), `DreamGuard` | per adaptor | **unchanged** |
| envelope validation, `screen` (R-18/R-28), `accept`, provenance stamp, `commit` | per adaptor | **unchanged** |
| eval suites under `evals/` | per adaptor | **unchanged**; the conductor gets its own ([50](50-migration.md)) |
| `inputs(key, batch) -> str` | reads the batch from its own watermark; `""` means "not me" | **gains `focus`** (validated ids from the plan); the batch comes from the delta |
| trigger | `AdaptorSchedule` + `_dream_adaptor_once` + per-adaptor watermark | **replaced** by `wants(delta)` ([30](30-conductor.md) §2) and the unit session |
| `key_kind` (`scope` / `unit`) | the runtime iterates every key every tick | **kept as a disclosure property**; keys come from `wants` |
| `uses_watermark` | per adaptor, per room | **removed**; one watermark per unit session |
| `samples`, `seed_at_head` | on the schedule | move to the specialist's own config |

So the refactor is `nora/app/runtime.py`'s dream half (`dream_loop`,
`_dream_adaptor_once`, `seed_watermarks`, the schedule clock store), the
composition that wires schedules, and one method added to the port. The five
adaptor modules under `nora/language/` change only in `inputs()`'s
signature and a new `wants()`, each a few lines.

`wants` is where each adaptor's current "nothing to reason about" check
moves:

| specialist | wants the delta when |
|---|---|
| note-candidates | a room has new human messages (per room) |
| note-curation | a time event: an anchor passed or a revisit due; or a message replies to, edits or deletes a message a live note cites |
| note-merge | note-candidates or the tag path created a note since the last merge |
| culture | a room's new messages since its last account pass `fold_batch`, or a day has passed |
| proactive | a note or Standing changed, or the daily floor (its current render rule, unchanged) |
| **event** | a date/time or RSVP cue, or a message touches a live gathering or a staged candidate |
| **memo** | a keep cue, a money or split cue, or a message touches a live memo |

## 2. The event specialist

**Keyed on the room** (a gathering has a `conversation_id`), so it holds the
room tools note-candidates holds, derived the same way (design 43 §2). It
reads the new messages, the room's gatherings (`list_events`) and staged
candidates, and returns one verdict per thing it found:

| verdict | writes | when |
|---|---|---|
| **stage** | a gathering candidate row (new table, shaped like `note_candidate`) with its cited messages | a gathering is being floated and is not yet agreed: "should we do hotpot Saturday?" |
| **propose** | a `Gathering` with status `PROPOSED`, `created_by` = the person whose message established it, cited | the room agreed a what and a when: "ok Sat 8pm hotpot, I'll book" |
| **evict** | retires a staged candidate, or cancels a PROPOSED gathering **this specialist created** | the room dropped it: "hotpot's off", or the date passed with nothing agreed |

The limits, each enforced in `accept`, not in the objective:

- **Never CONFIRMED.** Confirmation is a person's act on the tag path
  (`update_event`) or the web page.
- **Never touches a gathering a person created.** `evict` applies only to rows
  whose provenance is this specialist. A person-made gathering the room seems
  to have abandoned becomes a **note** ("is Saturday still on?"), not a
  cancellation.
- **Never creates a reminder.** When anything fires is proactive's decision,
  which it already makes over notes; it gains gatherings as an input.
- **Cites or is refused.** Every verdict names the messages it rests on, from
  the delta or from a tool result in this pass (design 43 §3's shown set).
  Nora's own rows cannot ground a verdict.
- **One gathering per thing.** A `propose` that matches a live gathering or a
  staged candidate by room, date and title is refused as a duplicate and
  counted; it should have been a revision.

A note and a gathering about the same plan link through the existing
`Note.event_id`. note-candidates keeps the open question ("venue not decided");
the event specialist keeps the dated thing people attend.

The attribution matters for the fire path: a gathering proposed by the
Dreamer is `created_by` a person, so any notice about it spends that person's
quota, never Nora's (design/dreamer-agent-loop/17-failure-classes.md §7).

## 3. The memo specialist

**Keyed on the room**, writing to the unit's memos ([10](10-vocabulary.md)
§4). Two verdicts: **keep** (a new memo, or a restated revision of a live
one) and **withdraw** (the room said it no longer holds). Its bar is higher
than the note pass's, because a memo does not close by itself:

- the thing is **settled** in the room's own words, not inferred;
- someone **will ask again**: a keep cue, a number, a place, an arrangement
  that outlives this week. "We had dinner at Yardbird last night" is not
  kept; "the deposit is $500, split four ways, Ken holds it" is;
- about the **speaker alone** it is refused (that is `remember`), and about a
  person's traits or tastes it is refused (that is Honcho's axis).

**The curator's discharge gains a memo.** When note-curation closes a note on
a settled answer, its verdict may carry a `memo` beside `commitment` and
`person_claim`, which the curator already emits
(`nora/dreamer/objectives/note-curation.md`). The memo cites the closing
message. That fills the gap the objective names: a settled fact "has nowhere
to go".

## 4. What does not become a specialist

- **The Standing debrief.** It runs inside the tag turn's reply slot, and its
  separation from the reply is for impartiality.
- **The Standing event fold.** Per room and about her position, not the
  unit's record. It could become a specialist later; nothing here needs it.
- **Participation.** A decider on the live path with its own seconds-scale
  window. It is the other half of the ambient loop's Decide (DEC-176), not
  background work.
