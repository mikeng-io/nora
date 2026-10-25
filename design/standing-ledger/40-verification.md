---
type: design
title: "Verification: four measurements, each against today's build, and the negatives"
description: "Work recall, topic recall, being contradicted, and retention across revisions, each run on master and on the branch with enough cases to separate a fix from noise; the lineage negatives; and the tool_selection run for the new read."
tags: [nora, standing, eval, verification, being-contradicted, lineage]
timestamp: '2026-10-25'
edges:
  - {dst: 20-the-ledger, rel: measures, provenance: asserted}
  - {dst: 30-turn-records, rel: measures, provenance: asserted}
---

# 40 · Verification

Every measurement below runs **on master and on the branch**, with a case
count that can separate a fix from noise: one example decides nothing (the
Relevance Gate's same 40 cases scored 0 to 2 run to run on one build). How a
corpus is run is `docs/eval-sop.md`; the numbers go in the PR.

## 1. Work recall (new corpus)

The operator's worry, measured directly. A multi-turn case: on turn N she
compares options with tools and states a preference; on turn N+k (k past the
prompt window) someone asks "which did you rank higher, and why?" or "what was
the other option?".

- **Pass:** the options, her ranking and its reasons come back as she had them
  on turn N, and match the turn's own tool results.
- **Fail:** a reconstruction, a different ranking, or "I don't know".
- Includes cases where turn N was a **fire** turn, since today those keep only
  their `cacheable` tool calls and no trace ([30](30-turn-records.md) §1).

## 2. Being contradicted (existing corpus)

`evals/corpora/being_contradicted.yaml`, which DEC-112's failure lives in. The
gate for retiring `recall_lookups`: the branch, with the tool gone and the
ledger and turn-record read in place, **does not regress** against master with
it present. The corpus must hold both kinds of challenged claim: a **decision**
(cited in the ledger) and an **ordinary checked answer** that never became an
entry, which is reached only through the handle on her own row
([30](30-turn-records.md) §3). Cases are added for the second kind if the
corpus lacks them. A regression on either keeps the tool until it is
understood.

## 3. Topic recall (new corpus)

A topic discussed 30 to 60 messages back, then asked about. Pass: she finds
the thread, opens it through the `thread` argument, and answers from it. Measures threads
([20](20-the-ledger.md) §4) against today's 20-row window plus a paragraph.

## 4. Retention across revisions (new, offline)

Plant N decisions and commitments, run k revisions of unrelated turns, then
read the ledger back. **Pass is exact:** every planted entry survives each
revision byte for byte unless an op named it. Today's fold is the comparison
arm, and its loss rate is the number that motivates the bundle; this measures
it rather than asserting it.

## 5. The negatives: what must NOT happen

- **Lineage.** A unit-scope entry whose lineage includes a room a listener is
  not in never renders there. A turn record opened from another room of the
  same unit returns only the items whose lineage passes there, and counts the
  rest; a mixed-room Cognition record is never returned whole on unit
  membership alone. A handle from another unit opens as unknown,
  byte-identical to a handle that names nothing (the `room_read` rule, D1 = B).
- **Stale writes.** Two rooms revise or close the same unit entry from the same
  base; exactly one commits and the other is refused `stale_base`. An op on an
  existing entry with no base is refused.
- **Laundering across units.** An entry is never scoped to a unit its cited
  evidence does not belong to.
- **No restatement.** A thread has no body, and a decision cites a transcript
  row rather than quoting the room at length; the validator refuses a line
  with no cite.
- **No fold.** No writer sends a whole Standing; a write that is not an op is
  refused.

## 6. Routing

The new read tool, the ledger-op tool and the `thread` argument on
`load_transcript` and `room_read` each get `tool_selection` cases with their
siblings (`standing_read`, `load_transcript`, `room_read`) named in
`not_tools`, run before and after (ADR 0004, ADR 0015).
