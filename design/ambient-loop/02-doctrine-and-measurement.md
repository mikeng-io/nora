---
type: analysis
title: "Inputs Per Objective, The Rule That Stays, And The Cases With Their Real Cost"
description: "Where the change actually is. One objective per stage stays. What each is TOLD changes — including one render (the debrief's outcome header) that #199 turns on. Design 33 §3's write-time test stays as the rule; version 1's replacement was circular. The seam cases are listed with the fixture each needs, because version 1 claimed they were free and none of them are. The positive control is #199 itself, being built as a journey case tonight."
tags: [nora, ambient, ooda, doctrine, objectives, evals, measurement]
timestamp: '2026-09-11'
edges:
  - {dst: 01-stages-and-seams, rel: depends_on, provenance: asserted}
version: 4
---

# Inputs Per Objective, The Rule That Stays, The Cases With Their Cost

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


## 1. The change is what each stage is told, not what it is instructed

`AGENTS.md`: **when a model judges badly, read its INPUT before you rewrite
its instruction.** Measured three times on 2026-09-09/10; measured a fourth
time on 2026-09-11 in the rarest form — the input **did** carry the truth
(her sent row, two lines above the tag) and a header that did not say what it
was scoped to beat it. So this node specifies renders. Instruction wording is
the operator's, and the one place this node touches a render that the
objective reads is the debrief's outcome header, because that is the defect.

## 2. What each objective is told

| objective | reads today | adds | why |
|---|---|---|---|
| `standing-debrief.md` | the exchange, prior, `<what_happened>` | the header **scoped to the turn**; a `Nora:` row after the tag marked as a delivered reply | #199 — a turn outcome became a room position (`01 §2.2`) |
| `standing-verdict.md` (deleted 2026-09-24 with the verdict and this check, `design/standing-compact/`) | the exchange, reasoning, outcome | a deterministic check beside the model: *no reply was sent* + a `Nora:` row after the tag → `unsupported` | the verdict has never once returned false (#189); here it should have |
| `participation.md` (gate) | transcript with ages, Standing | **nothing.** The gate stays a pure function of two blocks | design 36 §6. v2 put a `<decisions>` block here; a pending proactive row carries a note title, so that was the forbidden question |
| the participation **turn** (`fire.py`) | everything, incl. `<notes>` when `[notes] enabled` | proactive rows pending or fired for this **unit**, last 24h — writer, verdict, room, age, note id; no title | a row says she has scheduled herself to speak and when: the loop's own commitment, not a fact about the world. No title, so on a deployment with notes off it carries no note-derived content |
| `proactive.md` (decide) | notes, Standing, roster, own log, self card | participation's and the tag path's verdicts for the unit, last 24h, with note ids; later, a bounded recent window for any room a note references | `01 §2.1`, `§2.3` — #199's decider had nothing that could contradict a false note |
| `note-candidates.md` | batch, notebook | the room's label and operator description | `01 §2.4` |
| `note-curation.md` | notebook, per-note window (`is_nora` excluded) | her own sent row admissible as a **closing citation only**, never as discharge grounding — conditional on A4 (`01 §2.2`) | `01 §2.2` |

Every addition is a render block over state that exists or a store that lands
tonight. The rule for every block, restated because it was missing a fourth
time: **a block says what it is, how old it is, which way it reads — and what
it is scoped to.**

## 3. The rule that stays

Version 1 proposed *"what the loop holds is what the loop would act on."* The
review showed it circular — note existence depending on gate verdicts
depending on note existence — and inert at cold start: with 0 of 33 windows
admitted it licenses holding nothing.

Design 33 §3's test is intrinsic and decidable at write time, and it stays:

> A thing with no closing condition is not a note. Forward-lookingness: the
> entry has not finished its job.

What v1 was reaching for does **not** survive as a retirement signal either.
Version 2 proposed counting decliner verdicts against a note; 39 D6's build
audit already derives why that is wrong — «a `decline` charges no strike…
the curator already wakes every due note daily, so waking on every look would
strike a quiet dateless note past the ceiling in two days». A count of
declines is that same ratchet through a different door, and a quiet week
would retire a live note. Dropped. `strikes` on a wake she actually made
(D6) remains the only decliner-side signal, and it already exists.

## 4. The cases, with what each actually needs

Version 1 said all six were "expressible with the fixtures the suites already
have". None are. The honest table:

| case | spans | needs | status |
|---|---|---|---|
| **S1 · Cyberport** (#199) | Observe → Act → Orient → Decide, twice | a multi-stage harness that admits participation (one optional `participation_gate` kwarg on `offline.py`), a fake clock, a scripted `APIError` | **built — PR #201.** Measured red on master: `--runxfail` → 5 failed / 5 passed, the five stage-3 assertions, `xfail(strict=True)` |
| **S2 · she already spoke** | Decide ↔ Decide | `DecideCase` has no participation input; `eval_support.py:1448` forces every `log:` row to `kind="proactive"` — needs the shared log in the fixture | after D lands |
| **S3 · the race** | tag ↔ participation | the tag turn reads the shared log; a scripted participation send one second before the tag | after D lands; S1 already drives the shape |
| **S4 · her act moved the thread** | Act → Orient | a curator suite — **there is none** (`evals/run.py` `SUITES`); and `record_wake` already stamps this, so the case asserts a *judgement* (retire / hold), not rendered text | new suite |
| **S5 · a pending row overtaken** | Decide ↔ Decide | the cancel half is already covered model-free by `a02_note_moved_on_the_tag_path`; the other half is the participation **turn** (not the gate) seeing the pending row via the shared log and cancelling it | after D lands |
| **S6 · same people, different room** | Observe → Orient | `BatchMessage` has no room field; on one fire it is true by construction and on two the anchor-date dedupe catches it — needs a **different-anchor** twin to be falsifiable | fixture + twin |

Two disciplines the participation suite already states and these must
inherit: *a case that cannot flip measures nothing* (every positive needs a
twin), and *asserting rendered text measures the renderer* — S4 and S6 as v1
wrote them were tautologies.

## 5. The positive control

One scenario through the real stages, twice around. It is #199, as it should
have gone:

> A human row lands in a Signal room, untagged (**Observe**). The gate admits;
> the participation turn answers (**Decide → Act**). The user tags one second
> later; the tag turn sees participation's answer in the shared log and does
> not re-derive it (**Decide ↔ Decide**) — or, if it runs and dies, the
> debrief writes Standing that knows a reply was sent, proposes no note, and
> the verdict would have caught it if it had (**Act → Orient**). At +80 min
> the proactive decider reads the log, sees the question was answered, and
> schedules nothing (**Decide**). Nothing lands in any room after the first
> answer.

It is measured red on the current tree at the debrief step (PR #201, five
assertions). The clauses about the shared log — the tag turn *seeing*
participation's answer, the decider *reading the log* — are D-gated and are
not what turns it green: A1–A3 do, by the debrief no longer writing the false
position. The D-gated clauses become assertions S2/S3 when D lands, so the
case does not pass for a reason its prose does not state.
