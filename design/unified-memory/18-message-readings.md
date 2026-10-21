---
type: design
title: "Message readings — every Record item is perceived once on arrival"
description: "Read every message batch into the Record, decompose Honcho into the existing layers, restore Character as the bounded card, and gate the switch on answerability; record the operator’s decisions on Q1–Q5."
timestamp: '2026-10-21'
edges:
  - {dst: 00-intent, rel: extends, provenance: asserted}
  - {dst: 10-honcho-audit, rel: depends_on, provenance: asserted}
  - {dst: 45-persona-adaptor, rel: extends, provenance: asserted}
  - {dst: ../cognition-loop/05-data-model, rel: extends, provenance: asserted}
  - {dst: ../cognition-loop/25-rollup, rel: extends, provenance: asserted}
---

# Message readings

> 2026-10-21 (operator): the decisions below are settled design, not an implementation or switch receipt. Q1–Q5 are decided below. New text follows [ADR 0026](../../docs/adr/0026-the-character-tier-names-its-entry-parts-profile-traits-hypotheses.md): Notebook rows are judgements, Character is the tier, one row is an entry, and its parts are profile, traits and hypotheses. “Persona” is retired as vocabulary; existing code names stay in backticks.

## Context

The 2026-10-21 audit found that the existing `persona-*` path covered about
30% of Honcho's facts fully and about 40% partly. Across four people,
Honcho held 848–2,800 explicit self-observations per person, plus 25–119
deductive and 72–202 inductive observations; the current `persona-*` output
held 9–15 lines per person. Counts were by peer name across Honcho workspaces,
not de-duplicated: these show sizes, not exact unique populations.

Honcho v3.1.2 on norty (`~/workspace/honcho/src`) has three stages:

- **Deriver** (`src/deriver/prompts.py`): every message batch, a cheap model
  extracts all explicit atomic facts, with no cap.
- **Dreamer** (`src/dreamer/specialists.py`): tool-using deduction and
  induction specialists, 10–12 iterations, plus peer-card refresh. The
  deployed configuration is 50 new observations plus 60 minutes idle, at
  least 8 hours apart.
- **Dialectic**: a query-time agent over observations and messages.

Honcho has **no abduction and no code Rollup**. Nora had no equivalent to
its deriver. One `persona-*` session — one model call per 100 messages or
7 days, one `persona_write`, capped at 5 traits and 3 hypotheses — was doing
both derivation and synthesis. A capped synthesis was measured against an
uncapped log. The missing statements were an architectural gap, not proof
that the card needed to grow without bound.

[cognition-loop/05 §2](../cognition-loop/05-data-model.md#2-the-vocabulary-layer--tier--kind--entry)
said “per-person facts live in Memory (Honcho)”. Unified memory removed
Honcho without giving those statements a home; the `persona-*` identity
part became a de-facto fact store inside the Notebook. The revision gives
statements a Record home and leaves the Notebook to judge.

## The principle

**"Every Record item is perceived once on arrival; text is a medium like any other."**

## What a message reading holds

> 2026-10-22 (operator brief; proposed): [47 §2](47-character-dimensions.md#2-slot-templates-and-storage) makes each claim structured: about whom, what, event date/interval resolved against message time (unknown stays unknown), optional stated abstract place, speech act and source message ids. Assertion time and build time stay distinct from event time. This lands in phase 2; the source-tied lifecycle below stays.

A message reading is a **Record item**, kind **message reading**, next to
the messages it reads. It states what was said:

- **About whom** the statement is made.
- **The claim**, preserving what the source actually says.
- **How** it was said: stated by the person / said by another / planned /
  asked / reported.
- **Its message ids**, so every claim leads back to the original messages,
  their authors, rooms and assertion times. The reading's build time is not
  the time of the statement.

A reading is a reading of an item, **not a fact asserted as truth**. “Asked
whether”, “planned to” and “someone else said” do not become self-reports or
settled facts by being recorded. Original messages remain citable. A reading
is versioned like a Rollup build, rebuildable from the Record, and superseded
rather than deleted. Its projection is a node with an `attached_to` edge to
each message it reads ([25](25-memory-graph.md)); the index remains derived.

A message reading’s visibility is scoped to its source messages: a reader must be eligible to read every source message. When any source message is hidden (forget/delete) or edited, the reading’s live projection is invalidated: its node is retired, and the reading is re-read from eligible sources on the next build. A reading never outlives the eligibility of what it reads; retained versions are provenance, not live retrieval results.

## When it runs, and why this is not #115

Code runs the model reading **unconditionally on every message batch on
arrival**, exactly as media is read today. Saving a reading is never a
model's choice. “Once” describes one arrival perception, not a prohibition
on a versioned rebuild from the Record.

The deprecated knowledge tier (#115) left saving to the model: 5 rows in
11 days. Here, every batch gets a reading, and the result describes the
source statement with its provenance; it is not a separate store of facts
asserted as truth. The Record remains the source, and Character remains a
bounded judgement. This creates no new layer.

## Honcho, decomposed into our layers

Memory no longer sits beside the flow. See also
[10 · Honcho's architecture, mapped](10-honcho-audit.md#6-honchos-architecture-mapped).

| Honcho | ours | layer |
|---|---|---|
| deriver (explicit facts) | message readings | Record |
| (none) | Rollup | Rollup |
| dreamer deduction / induction (+ our abduction) | Character entries: traits, hypotheses | Notebook |
| peer card | Character entry profile | Notebook |
| dialectic | `memory_search` + pre-fetch over readings, Rollup and entries; Nora’s own turn does the reasoning Honcho’s dialectic agent does | Views |

Authored still fixes the rules. A Rollup may compute over readings because
they are Record items, but its computation remains **code only**
([cognition-loop/25](../cognition-loop/25-rollup.md)). A model reading does
not turn a Rollup into model-written synthesis.

## Character is the card; Views answer from everything

With statements in readings, a Character entry is again the **bounded
synthesis Nora sees in every prompt**, with profile, traits and hypotheses.
It is not the whole memory. The person session reads readings; messages
remain citable ([45 §4](45-persona-adaptor.md#4-the-adaptor)). Under Q2, the session is pushed readings since its last session, has read tools
over older readings and messages with bounded iterations, and ends in one
settle write (today `persona_write`); still one writer per tier, no new writer.

Answers come from **Views over readings, Rollup and entries**, through
`memory_search` + pre-fetch. Nora’s own turn does the reasoning Honcho’s
dialectic agent does; no new tool is introduced. This keeps source statements
available without requiring the Character card to repeat all of them. Notebook entries remain
judgements, never independent evidence.

## Phases, in order

The built history remains in [00 · Sequence](00-intent.md#sequence). The
forward work is:

1. **This design revision.**
2. **Message readings on new messages**, in the Record; nothing reads them
   yet. Claim slots and event-time resolution follow [47](47-character-dimensions.md) in this phase.
3. **History readings**, about two months; Honcho observations are measurement
   reference only for the first measure, with nothing imported. Decide any
   source-rechecked seed on that result (Q3).
4. **Character rework and the ADR 0026 rename in ONE migration**: one
   `character-*` writer family (person, room, unit, nora, verdict), reading
   readings and writing typed profile/traits/hypotheses lines. Prose
   adaptors retire at the switch. Trait dimensions, slots, support checks and hypothesis explanation slots from [47](47-character-dimensions.md) land in this phase.
5. **Answering**: `memory_search` + pre-fetch over readings + Rollup + entries,
   then the gates below.
6. **The switch** (`[memory_index] enabled`), the operator's call. Honcho's
   feed stops later ([70](70-honcho-cutover.md)).

Readings and backfill **do not wait for the rename**. New code uses ADR 0026
names only; this revision changes no existing code identifier.

## The switch gate

**Nora can answer what Honcho could.** Freeze a question set, have the new
Views and Honcho's dialectic answer it, and judge the answers blind. This
supersedes [45 §6 items 4–5](45-persona-adaptor.md#6-what-honcho-knew-is-re-derived-and-that-is-measured)
in part: Character-line coverage is no longer the switch gate. The old text
is retained. Under Q1, build and freeze the set before any run, with no editing
after the first answer is seen. Pass requires blind judgement that the new
Views are at least as good as Honcho’s dialectic on ≥80% of questions AND
an error rate <5% (answers asserting something their sources do not support).
Both must hold.

The **80-pair reply replay stays**, including its existing at-least-55%
preference bar (45 §6 item 6). Neither gate is run or claimed passed by this
docs revision.

## Decided questions

These decisions are also recorded in [_index.yaml](_index.yaml).

### Q1 — Answerability thresholds and question-set construction

**Decided (operator, 2026-10-21).** The question set is built and frozen **before any run**; it is not edited after the first answer is seen. Pass requires the new Views' answer to be judged blind as **at least as good as Honcho's dialectic on ≥80%** of questions, AND the new Views' **error rate <5%**. An error is an answer asserting something its sources do not support. Both must hold.

### Q2 — Character session shape

**Decided (operator, 2026-10-21).** The session follows Honcho's pattern: it is pushed the readings since its last session, has read tools over older readings and messages with a **bounded number of iterations**, and ends in **one** settle write (today `persona_write`). There is still one writer per tier; no new writer.

### Q3 — Honcho seed

**Decided (operator, 2026-10-21).** Honcho's observations are used **only as the measurement reference** for the first measure (the Q1 gate and coverage comparisons). Nothing is imported. Whether to seed, only with each observation re-checked against its source message, is decided on that first measure's result.

### Q4 — Reading model and budget

**Decided (operator, 2026-10-21).** The reading model is **DeepSeek V4.1 Flash**. The budget is not set now: the first deliverable of phase 2 is to measure real input/output tokens over **one day of live messages**, then fill in the existing formula C = (I × Pᵢ + O × Pₒ) / 1,000,000 and set the budget from that measurement.

A per-batch cost estimate must use live message volume and measured input
and output tokens. The available live-volume snapshot in
[10 §3](10-honcho-audit.md#3-what-honcho-holds), read 2026-10-14, is 7,337
main-workspace messages (08-05 → 10-14) and 433 unit-workspace messages
(08-05 → 10-11). As a rough historical planning rate over about 70 days,
7,770 messages imply **about 111 messages/day**; this is not a fresh volume
measurement or a de-duplicated count.

For mean **B messages/batch**, that rate estimates **111/B reading
calls/day**. If a batch uses **I input tokens**, **O output tokens**, and
the chosen route charges **Pᵢ / Pₒ per million tokens**, its estimated cost
is **C = (I × Pᵢ + O × Pₒ) / 1,000,000 per batch**, or **(111/B) × C per
day** at that historical rate. Batch granularity/context is decided in Q5; token measurements,
current live volume and route prices are not supplied by this brief, so a
numeric currency estimate and a budget remain **unmeasured**, not zero or
approved. Refresh those inputs for the per-batch estimate and set the budget
from the phase 2 one-day measurement.

### Q5 — Reading granularity

**Decided (operator, 2026-10-21).** Readings are **per batch, with the surrounding conversation as context**, as Honcho does: a reply like “me too” only means something in context, and this uses fewer calls. Every claim in a reading is still tied to its own source message ids, so per-message citation and the visibility retirement rule are unchanged.
