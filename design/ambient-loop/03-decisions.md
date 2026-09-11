---
type: analysis
title: "Decisions, What They Supersede, The Order To Land Them, And The Issues Re-Filed As Seams"
description: "Five decisions with their evidence and reversal conditions. Numbers reserved from DEC-176 because the live-threads bundle (#173) holds DEC-163..175 and is the operator's. What in design 39, the GLOSSARY and ARCHITECTURE.md is superseded, the two schema changes, a seven-step migration each small enough to gate alone, and the open issues that are seams of this loop rather than defects of a component."
tags: [nora, ambient, ooda, decisions, migration]
timestamp: '2026-09-11'
edges:
  - {dst: 00-overview, rel: summarises, provenance: asserted}
version: 1
---

# Decisions, Order, And What Moves

## 1. Numbering

The live-threads bundle (#173) reserves **DEC-163..175** and is blocked on the
operator. This bundle takes **DEC-176 onward** so that answering #173 does not
renumber anything here, and vice versa. Per `CONTRIBUTING.md`'s numbering
discipline the index is re-read at merge; if 176 is taken by then, the
references move, not the number.

## 2. Decisions

### DEC-176 — Nora has two backbones, and the ambient loop is one of them

The tag path answers when called. The ambient loop is how she is present when
not called, and it is one observe–orient–decide–act loop whose stages are the
ingest path, the memory layer, two deciders, and the fire path. **The loop is
named as the system's**, not as the proactive adaptor's.

*Supersedes* design 39 §1 ("one loop, two entry points", where tag is a half
of the loop). Tag is the other backbone and a consumer of the loop's state,
not a stage of it. *Evidence:* `00 §3` — every measured failure is on a seam
between two stages and none is inside a stage. *Reopens if* a component-level
fix closes a seam failure without any cross-stage input — that would show the
seam framing was decoration.

### DEC-177 — Decide has two deciders, and they are the same stage

Participation ("this window closed — is it hers?") and proactive ("across the
workspace — anything worth raising, and when?") are one stage asked two
questions on two triggers. They read the same held state, speak through the
same path, and **must see each other's recent verdicts**.

*Supersedes* design 35's framing of participation as "the fifth Event" standing
beside the loop, and design 39's silence about it. *Evidence:* `01 §1` — the
two deciders' columns for each other are both empty; `01 §2.3` — the concrete
verdicts each cannot see. *Reopens if* a shared log is built and the seam
cases S2 and S5 (`02 §4`) stay green without it — i.e. the deciders turn out
not to need each other.

### DEC-178 — Memory is the medium: every stage that judges reads it

Honcho, the notebook and Standing are written at Observe and Act and read at
Orient and Decide. The participation decider and the note tier read memory.
Design 39 placed Honcho inside Observe; that is where it is *written*, not
where it is read.

*Evidence:* `01 §2.5` — memory reaches one stage of four today; #118 step 4
deferred enrichment as "only if bodies read as useless", and this node says
the note tier extracting without a peer card is a seam, not a polish.
*Reopens if* rendering peer cards into the gate and the note tier measurably
worsens either suite at n≥30 — the capability-list result on the gate (#144,
#147) is the precedent that more input can be worse, and it was measured.

### DEC-179 — Each seam has a contract, and two of them need a column

| seam | contract | needs |
|---|---|---|
| Observe → Orient | a row carries its **room** (label, and the operator's description if any), never the workspace alone | render change; `policy.toml` gains an optional `description` per room (#161, both levers) |
| Orient → Decide | held state reaches every decider with its age; the wire does not discard the decider's reasoning | render change; the gate off the SDK transport (#186) |
| Decide ↔ Decide | one decision log, two writers, both readers | a `writer` column and a cross-render |
| Act → Observe | an act that concerns a note stamps it *moved by her, when* — movement, never content | `note.last_moved_at` + `last_moved_by`; the brakes in 39 §1 stand |
| the medium | every judging stage reads memory | render change (`about`, `<people>`) |

*Evidence:* `01 §2`. *Reopens if* a stamped `last_moved_at` starts driving
retirement on its own and a note she has asked about twice with no answer
retires *faster* than one nobody touched — that would be the stamp becoming a
strike, which it is not.

### DEC-180 — Seam cases are a precondition for tuning any single stage

The six cases in `02 §4` and the end-to-end journey in `02 §5` land before
any objective is rewritten again. A suite that measures one stage against its
author's inputs cannot go red on a seam, and #158 already shows the decide
suite cannot separate a prompt change from noise at n=5.

*Evidence:* `01 §3` — four green suites, a loop that admitted zero windows.
*Reopens if* the seam cases turn out to be expressible only with new fixtures
the suites do not have; then the cost is real and the order is renegotiated,
not skipped.

## 3. What is superseded, and where

| where | what it says | becomes |
|---|---|---|
| design 39 §1 | "one loop, two entry points" — tag enters at Act, proactive at Decide | superseded by `00 §2`; 39 keeps its decisions D1–D13 and its brake table, which are about the proactive decider and stay correct |
| design 35 title | "the fifth Event … not a fork" | still true of the *mechanism*; the *role* is the participation decider of the loop |
| GLOSSARY *ambient* | "a message Nora records but does not answer" | still the meaning of an ambient *message*; a new entry **ambient loop** names the backbone |
| GLOSSARY *proactive* | "both run one OODA loop" — the loop as the adaptor's property | the loop is the system's; the entry points at *ambient loop* |
| ARCHITECTURE.md topology | five Events listed side by side (heartbeat, announce, compute, participation, proactive) | unchanged as a list of *triggers*; a sentence names which two are the loop's deciders |
| `nora/language/proactive.py:1`, `nora/app/fire.py:303` | "the DECIDE turn of one OODA loop", "the ACT turn of the OODA loop" | correct, and now point at this bundle rather than at 39 §1 |

Nothing in 39's decisions is reversed. What is reversed is *whose* loop it is.

## 4. Migration — seven steps, each gated alone

Ordered by blast radius, smallest first. Each is a branch with its own
mutation and its own fragment; none depends on the next.

1. **This bundle, plus the three doc touches in §3.** No code. Lands the
   definition.
2. **The gate off the SDK transport** (#186). One file. Orient → Decide wire.
   Measured before and after with the 40-case suite; the safety case is the
   gate for this step.
3. **The room reaches the note tier** (#161, both levers). ~5 lines plus a
   `policy.toml` field. Observe → Orient.
4. **The gate reads the open notes for its room.** Render change in
   `relevance_gate.py`. Case **S1** goes green. Orient → Decide.
5. **One decision log, two writers.** A `writer` column; both deciders render
   the other's last-24h rows. Cases **S2**, **S5**. Decide ↔ Decide.
6. **An act stamps the note.** `last_moved_at` / `last_moved_by`; the curator
   and the decide turn render it. Case **S4**. Act → Observe.
7. **Memory to the gate and the note tier.** `about` / `<people>` rendered.
   Cases **S3**, **S6**. The medium.

Then `02 §5`'s journey, which is red until step 6 and is the loop's positive
control from then on.

**What is deliberately not in the order:** DEC-163 (#173). Step 6 gives the
loop the *structural* half of "keeps track between turns" — the note knows she
acted. Whether she is *finished* when she has answered is a sentence in
`identity.md`, and it is the operator's. The order above does not wait on it
and does not decide it.

## 5. Open issues, re-read as seams

| issue | was filed as | is |
|---|---|---|
| #161 | note tier: not told the room | Observe → Orient (step 3) |
| #186 | relevance gate: unsafe transport | Orient → Decide, the wire (step 2) |
| #163 | participation: never admitted | the ambient Decide has no evidence yet; steps 4–5 are what would change its inputs, and the operator's read (quiet rooms) stands until then |
| #160 | proactive: reads memory 3–5 times | Decide not trusting Orient's output — the `<people>` block is rendered and she asks anyway; a per-pass budget in code is the fail-closed option and stays the fix |
| #159, #162 | note: one date | Act → Observe and Orient → Decide — a note's *owed* date is part of its last-move / closing state; folded into step 6's column rather than a second anchor |
| #173 (DEC-163) | identity: «then you're done» | Act → Observe, the wide half. Operator-only. Not moved by this bundle |
| #118 step 4 | note tier: enrichment "if needed" | the medium (step 7). Promoted from polish to seam |
| #189 | debrief render clips uncounted | Orient's fold judging a view it cannot know is cut — the block-explains-itself rule, unfixed in one place |

Nothing here is closed by this bundle. Each is re-pointed at the seam it sits
on, so the next person fixing one knows which two stages it joins.

## 6. What would show this bundle was wrong

- **A component fix that closes a seam failure with no cross-stage input.**
  Then the seam was a story and the loop framing decorative.
- **Seam cases that stay green when their crossing input is removed.** Then
  the input was not load-bearing and DEC-178/179 over-claimed.
- **The shared log making the deciders worse** — proactive holding on every
  room where participation ever spoke, or the gate admitting on every pending
  row. The contract is *see*, not *defer*; if seeing becomes deferring, the
  render is telling the model what to do instead of what it is reading, which
  is the failure `AGENTS.md` names.
