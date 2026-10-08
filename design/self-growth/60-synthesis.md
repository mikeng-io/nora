---
type: design
title: "Synthesis: Character accounts that say what a group or person IS, not what they did lately"
description: "The operator (2026-10-09) read the culture and character accounts as compressions of recent events, compact but not synthesised. Measured on the live heads it holds: most of each account's text is specific instances, around 7-10 patterns each, and room accounts only grow. The causes are in the inputs and objectives, not the model. The fix is an objective rewrite to a re-synthesis with a few patterns and rare examples, a length target sized on measured output, and a before/after on the same instrument."
tags: [nora, character, culture, synthesis, measurement]
timestamp: '2026-10-09'
edges:
  - {dst: 10-character, rel: amends, provenance: asserted}
---

# 60 · Synthesis

## 1. The concern, and the measurement

Operator, 2026-10-09: the room and unit culture reads "more like a compression
of standing rather than a synthesis. It is compact, but not synthesised", and
the same across most character accounts.

**Measured on the live heads (2026-10-09, `scripts/character_synthesis_measure.py`,
judge `nora` at effort low, temperature 0, 3 draws, which agree exactly at
temperature 0 so they are not independent):** each whole account is scored for
`instance_share`, the fraction of its text spent on specific instances (verbatim
quotes, single exchanges, named items, prices, dates) rather than general
patterns and their reasons, and `patterns`, the distinct patterns it states.

| kind | n judged | mean instance_share | patterns per account |
|---|---|---|---|
| person | 12 (1 ERROR) | 0.68 | 7-10 |
| Nora | 2 (1 ERROR) | 0.60 | 6-8 |
| room | 7 (1 ERROR) | 0.48 (largest room 0.75, 19 patterns) | 4-19 |

A first instrument labelled SENTENCES episodic or characterological and read
the same rooms as 88% characterological: every sentence there opens on a trait
and then buries it under quotes and instances, which a per-sentence label cannot
see. The account-level instance share is the measure; ERROR (the judge cut off
at its ceiling) is reported, never averaged in.

## 2. Why (read from the inputs and objectives, 2026-10-09 study)

1. **"Continue it" with nothing that expires, and no consolidation.**
   `culture.md` "Building on what you wrote before", `character-person.md` and
   `character-unit.md` say keep what is still true and add what changed;
   nothing asks to re-judge or merge, so each pass appends this batch's
   instances. The largest room grew from 1,316 to 6,653 characters.
2. **Seven of eight room heads predate the citation-checked writer** and were
   written with `<standing>` and up to 30K of pre-fetched person views in their
   input: a compression of Standing, literally. Every later revision continues
   from them.
3. **Citations bind each revision to the newest batch** (`check_revision`,
   `nora/domain/culture.py`), and no session can read older evidence, so what is
   cited is what just happened.
4. **No length pressure.** `BODY_MAX_CHARS` is 20,000, a corruption ceiling,
   not a target.
5. **The person example is a role** ("the one who turns talk into a booking")
   and is copied nearly verbatim; each person session sees one person alone,
   so it cannot say what distinguishes them.
6. **The counter-example is Nora's**: "what holds across many episodes, never
   what one reply said" plus a three-episode floor gives the most synthetic
   accounts of the four.

## 3. The fix, in order

1. **Re-synthesis, not continuation** (objective rewrite, room, unit, person,
   Nora): the previous account is evidence to re-judge, not text to extend. Write
   the few patterns that most define them (the judgement of how many is the
   model's; the measure reports it), each with why, an example only where a
   pattern needs one and then a short one, no verbatim quotes beyond a word or a
   name the group uses. Drop what is only one occasion. Drop "where you stand"
   from the room account: that is Nora's own account's job.
2. **A length target in the objective** sized on measured output, never a
   refusal cap inside the emission range (a cap there is a wedge).
3. **Person sessions see the unit's other people's accounts**, so an account
   can say what distinguishes this person instead of a role every account
   shares.
4. **Measure before and after** on the same instrument, over the same
   accounts' rewrites (§4). Ship each objective only where instance share falls
   and nothing true is lost.

Not in this step: a two-stage observation ledger, or widening what a session can
read (record or rollup tools). Those come back if 1-3 do not move the measure.

## 4. The before/after

The live heads are the BEFORE. The AFTER is each account re-synthesised once by
its own session under the new objective (a forced rewrite of the head, the same
input production gives it), then scored by the same instrument. Pass: mean
instance share at or under 0.35 per kind and fewer patterns, with a human read
that nothing true and characteristic was dropped.

## 5. Measured, 2026-10-09

The live heads above, and a replay (`scripts/character_replay.py`): 26 sessions
(8 rooms with today's input rebuilt by the production adaptor against the live
database, `notes=None`; 15 person and 3 Nora sessions' inputs recorded in
Langfuse), each run on `nora-dreamer` at effort low with only the objective
varied, then scored by `scripts/character_synthesis_measure.py` (`nora`, effort
low, 1 draw; temperature 0 makes repeated draws identical). Inputs and outputs
stay under /tmp, never in git.

| kind | A: shipped objectives | B v1: re-synthesis | B v2: concise, three traits |
|---|---|---|---|
| room | 0.55 (n=2, 6 ERROR), 1,942 chars | 0.32 (n=7), 949 | **0.25** (n=8), 473 |
| person | 0.60 (n=9, 4 ERROR), 1,364 | 0.42 (n=12), 694 | **0.26** (n=13), 458 |
| Nora | 0.54 (n=2), 1,658 | 0.43 (n=3), 907 | **0.37** (n=3), 548 |

(mean instance share; median length.) The judge errors on arm A are its own
ceiling on the long accounts, so A's numbers are, if anything, flattering. Read by
hand: v2 rooms describe how a group talks, plans and treats each other; v2 persons
describe behaviour with few events; one in five still carries one episode, and
Nora's own account stays the most episodic. Two v2 bodies carried message
numbers; `ids_in_body` (below) refuses that live.

Shipped as v2: `culture` v6, `character-person` v3, `character-unit` v2,
`character-nora` v3, and `check_revision` refusing a body that carries a message
number the session was handed (`ids_in_body`). The legacy heads are rewritten by
a one-time watermark rewind after deploy, so a quiet room does not keep its
compressed account for ever, and the live heads are measured again after it.
