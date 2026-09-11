---
type: analysis
title: "Inputs Per Objective, The Rule That Stays, And The Cases With Their Real Cost"
description: "Where the change actually is. One objective per stage stays. What each is TOLD changes — including one render (the debrief's outcome header) that #199 turns on. Design 33 §3's write-time test stays as the rule; version 1's replacement was circular. The seam cases are listed with the fixture each needs, because version 1 claimed they were free and none of them are. The positive control is #199 itself, being built as a journey case tonight."
tags: [nora, ambient, ooda, doctrine, objectives, evals, measurement]
timestamp: '2026-09-11'
edges:
  - {dst: 01-stages-and-seams, rel: depends_on, provenance: asserted}
version: 2
---

# Inputs Per Objective, The Rule That Stays, The Cases With Their Cost

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
| `standing-verdict.md` | the exchange, reasoning, outcome | a deterministic check beside the model: *no reply was sent* + a `Nora:` row after the tag → `unsupported` | the verdict has never once returned false (#189); here it should have |
| `participation.md` (gate) | transcript with ages, Standing | the loop's own decisions for this room, last 24h — proactive rows pending or fired | not notes, not memory: design 36 stands. The gate learns what the *loop* did, ≤ 300 tokens |
| `proactive.md` (decide) | notes, Standing, roster, own log, self card | participation's verdicts per room, last 24h; a bounded recent window for any room a note references | `01 §2.1`, `§2.3` — #199's decider had nothing that could contradict a false note |
| `note-candidates.md` | batch, notebook | the room's label and operator description | `01 §2.4` |
| `note-curation.md` | notebook, per-note window | her own sent rows as evidence a closing condition is met (closure, not content) | `01 §2.2` |

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

What v1 was reaching for survives as a **retirement** signal, not a write-time
rule: a note the deciders have declined N times across both paths is evidence
for the curator that it was never live — one input to `note-curation.md`,
once the shared log exists to count it. That is decidable, non-circular, and
cannot hold nothing.

## 4. The cases, with what each actually needs

Version 1 said all six were "expressible with the fixtures the suites already
have". None are. The honest table:

| case | spans | needs | status |
|---|---|---|---|
| **S1 · Cyberport** (#199) | Observe → Act → Orient → Decide, twice | a multi-stage harness that admits participation (`offline.py:290` removes it on purpose for the participation suite), a fake clock across ticks, a scripted `APIError` | **being built tonight** as the loop's positive control; red on master at the debrief step |
| **S2 · she already spoke** | Decide ↔ Decide | `DecideCase` has no participation input; every `log:` row is forced to `kind="proactive"` — needs the shared log in the fixture | after D lands |
| **S3 · the race** | tag ↔ participation | the tag turn reads the shared log; a scripted participation send one second before the tag | after D lands; S1 already drives the shape |
| **S4 · her act moved the thread** | Act → Orient | a curator suite — **there is none** (`evals/run.py` `SUITES`); and `record_wake` already stamps this, so the case asserts a *judgement* (retire / hold), not rendered text | new suite |
| **S5 · a pending row overtaken** | Decide ↔ Decide | the cancel half is already covered model-free by `a02_note_moved_on_the_tag_path`; the *admit* half needs the gate to see the pending row (shared log) | after D lands |
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

It is red on the current tree at the debrief step. It goes green when A1–A4
land, and stays the loop's acceptance test after that.
