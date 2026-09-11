---
type: analysis
title: "Decisions, What They Supersede, What Is Landing Tonight, And The Issues Re-Read As Seams"
description: "Five decisions from DEC-176 (#173 holds 163..175). DEC-178 is demoted from version 1's decision to an open question with the experiment attached. What in design 39, 35, GLOSSARY and ARCHITECTURE is superseded — and what is not, including the two-layer gate that version 1 wrongly tried to reverse. The migration as the six branches actually in flight on 2026-09-11, and the open issues re-pointed at the seam each sits on."
tags: [nora, ambient, ooda, decisions, migration]
timestamp: '2026-09-11'
edges:
  - {dst: 00-overview, rel: summarises, provenance: asserted}
version: 2
---

# Decisions, Order, And What Moves

## 1. Numbering

The live-threads bundle (#173) reserves **DEC-163..175** and is blocked on
the operator. This bundle takes **DEC-176 onward**; the index is re-read at
merge per `CONTRIBUTING.md`.

## 2. Decisions

### DEC-176 — Nora has two backbones, and the ambient loop is one of them

The tag path answers when called. The ambient loop is how she is present when
not called — one observe–orient–decide–act loop over the ingest path, the
memory layer, two deciders and the fire path — and **it is named as the
system's**. The tag path is the other backbone and **feeds the loop**: its
debrief writes Standing and proposes notes.

*Revises* design 39 §1's framing (tag as the loop's passive half). *Evidence:*
#199 — five seams crossed in ninety minutes, none inside a component; `01 §1`.
*Reopens if* a seam failure is closed with no cross-stage input. That test
already sorted two of v1's claims out of the seam list (`01 §3`) and is the
reason the list is now honest.

### DEC-177 — Decide has two deciders, each two-layer, and they must see each other

Participation (gate → turn) and proactive (decide → act) are one stage asked
two questions on two triggers. **The gate's layer stays exactly as design 36
decided it:** cheap, transcript + Standing, blind to notes and memory; the
turn behind it holds everything. What the deciders gain is sight of each
other's recent verdicts — and so does the tag path.

*Evidence:* `01 §1`'s empty "other decider" column; #199's tag turn
re-deriving a route participation had sent one second earlier. *Reopens if*
the shared log lands and cases S2/S3/S5 stay green without it.

### DEC-178 — OPEN: does memory into the note tier help?

Version 1 decided this. It is demoted to a question because the one precedent
on adding input to a judging model here (#144/#147 on the gate) measured
*worse*, and nothing has been measured for the note tier. **The experiment:**
render `about` for the people in a batch into `note-candidates` on a branch,
run the 18-case suite at n≥5 both ways, and read precision on the negative
cases. Decided by that number, not by argument. Not landing tonight.

### DEC-179 — Each seam has a contract; one needs a store, one needs a header

| seam | contract | what it takes |
|---|---|---|
| Decide ↔ Decide | one decision log, written by the gate, the participation turn, the proactive decide and act turns, and the tag path; read by all of them for the last 24h of their room | **a new store** — table, port, SQL + in-memory, contract suite. v1 said a column on an existing table; there is none |
| Act → Orient | the debrief's outcome header is turn-scoped; a `Nora:` row after the tag reads as a delivered reply; the verdict can see the contradiction; a dead turn proposes no note; her own row may satisfy closure | renders, one deterministic check, one predicate — and reconciliation with `record_wake`/`strikes`, which already exist |
| Orient → Decide (proactive) | the decide turn can check a note against the record — the shared log first, a bounded window second | the store above, then a render |
| Observe → Orient | a batch names its room and carries the operator's description | a bug fix and a `policy.toml` field |
| the medium | DEC-178 | a measurement |

*Reopens if* a stamped closure by her own row starts driving retirement on
its own — that is the stamp becoming a strike, which `record_wake` already is.

### DEC-180 — The seam cases and the positive control are a precondition for tuning any single stage again

`02 §4`'s table states what each needs; none is free. S1 (#199) is being
built tonight and is red on master at the debrief step. #158 already shows
the decide suite cannot separate a prompt change from noise at n=5; a seam
that is unmeasured cannot be tuned at all.

## 3. What is superseded — and what is not

| where | says | becomes |
|---|---|---|
| design 39 §1 | "one loop, two entry points", tag at Act | framing revised by `00 §2`; D1–D13 and the brake table stand |
| design 39 D9 | `load_transcript` dropped from the decide turn | **stands, with its reversal condition now fired by #199** — the smallest answer is the shared log, not the transcript |
| design 36 decision 5 | the gate is blind to notes and memory | **stands.** v1 tried to reverse it and was wrong |
| design 35 | participation as the fifth Event | stands as the mechanism; the *role* is the participation decider |
| GLOSSARY *ambient*, *proactive* | a message she records; the loop as the adaptor's | an `ambient loop` entry names the backbone; *proactive* points at it |
| ARCHITECTURE "two paths, and a background tier" | | two backbones; the two deciders named among the seven occasions |

## 4. Migration — what is actually in flight, 2026-09-11 18:30

Six branches, each in its own worktree, each with an Opus reviewer before its
PR, merged in this order as they land:

| | branch | seam | issue |
|---|---|---|---|
| A | the debrief scopes the dead turn | Act → Orient | #199 |
| B | the gate off the SDK transport | inside the gate (`01 §3`) | #186 |
| C | the room reaches the note tier + `description` | Observe → Orient | #161 |
| E2 | a per-pass memory budget in code | inside the proactive decider | #160 |
| G1 | the #199 journey case | the positive control | — |
| D | the decision log store + both renders | Decide ↔ Decide | — |

**Not tonight:** E1 (the decide turn's bounded record window — D's log is
tried first), F1 (fire into the thread's room, not the home room — a
`policy.toml` trust grant, the operator's), G2 (the long cases in `02 §4`),
DEC-178's experiment, and DEC-163.

## 5. The incident, row by row

For re-derivation. All 2026-09-11 HKT; workspace `ridgeline`.

| when | stage | evidence |
|---|---|---|
| 16:42:43 | Observe | `messages`: Miles, Signal, untagged |
| 16:42:54 | Decide · gate | Langfuse `91c6ea6a` — admitted |
| 16:43:57 | Act | `messages` `is_nora=1 delivery_status=sent`; Langfuse `0b7fce09` |
| 16:43:57 | Observe | `messages`: `@Nora`, replying to 16:42 |
| 16:44:04–16:45:13 | tag path | Langfuse `0631322d`: `saved_places`, `route` ×2, six `reasoningContent` warnings, `APIError`, `llm_error` |
| 16:45:14 | Orient · fold | same trace, `engagement.debrief.fold`: input header unscoped, transcript row `[16:43] Nora:` present; output *«我今日冇出過任何回覆»* + note proposal |
| 16:45:14 | Orient · verdict | `grounded: true, unsupported: []` |
| 16:45 | Orient · note | `note 589863b9` *路線未覆*, closing condition *Nora 成功送出路線建議* |
| 18:03:33 | Decide · proactive | Langfuse `340373cd`: `note_read` ×3, `about`, `search_memory_workspace`, `ask_memory`, `remind` ×2; *«got no route at all»* |
| 18:11:33 | Act | Langfuse `4df78f31`; `reminders bf1333d4` done; Discord |
| 18:23 | mitigation | note retired by hand |

## 6. Open issues, re-read as seams

| issue | seam | landing |
|---|---|---|
| #199 | Act → Orient | A, tonight |
| #186 | inside the gate | B, tonight |
| #161 | Observe → Orient | C, tonight |
| #160 | inside the proactive decider | E2, tonight |
| #163 | the ambient Decide — **now live** (the gate admitted at 16:42:54); the timeout arm is #186's transport | parked; re-read after B |
| #159, #162 | Orient → Decide — a note's owed date is closing state | after D |
| #173 (DEC-163) | Act → Observe, the wide half | operator |
| #118 step 4 | the medium | DEC-178's experiment |
| #189 | Orient's verdict — #199 is a row where it should have been false | A2 is the deterministic half |
| **new** | the gate and the Standing fold name no room (`01 §1`) | filed from this node |

## 7. What would show this bundle was wrong

- A seam failure closed with no cross-stage input (DEC-176).
- S2/S3/S5 green without the shared log (DEC-177).
- The shared log making a decider *defer* rather than *see* — proactive
  holding on every room where participation ever spoke. The render tells the
  model what it is reading, never what to do; if that line moves, it is the
  failure `AGENTS.md` names.
- A1's scoped header making the fold worse on the existing debrief suite.
