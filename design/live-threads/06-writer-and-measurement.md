---
type: proposal
title: "The Writer Must Be Fixed Before The Shape Is Widened, And The Instrument Before Either"
description: "note-candidates already creates notes nobody wanted; widening what it may create makes that worse. And the suite cannot currently decide a prompt change at all, nor fail on anything this bundle is about. Both are preconditions, not follow-ups."
tags: [nora, note-candidates, evals, measurement]
timestamp: '2026-09-11'
version: 1
---

# The Writer, And The Instrument

## 1. Everything above assumed notes exist

`03` and `04` describe what a note may hold and how it is rendered. Neither
creates one. The creator is the note-candidates adaptor, and it has a problem
that predates this bundle.

## 2. The writer already mis-triggers

Observed by the operator: a note titled **「bath gathering tonight」** was
created from an exchange that was, in substance, *someone asked when he was
coming online to play; he said he was going home to shower first; a third person
said they would be on at 10*. There was no gathering. There was nothing
unsettled. The adaptor manufactured a plan out of an ordinary logistics
exchange.

**This is the note-tier analogue of a false-positive**, and unlike the
proactive false-positive rate (0.02, `01 §1`) nothing measures it.

**The rule this bundle imposes on itself:**

> **The trigger is fixed before the shape is widened.**

Widening what a mis-triggering writer may create does not add a capability; it
adds a second way to be wrong. Under `03 §1` the adaptor would gain the ability
to assert *states* — "they are playing X", "his ankle is hurt" — and a writer
that invents a bath gathering will invent states about people, which is a worse
failure than inventing a plan: a wrong state is a wrong belief about a person
that then drives what she says to the room.

**Order, therefore:**

1. Measure the writer's false-positive rate at all. It has never been measured;
   `01 §5.2`'s point applies here too — a rate nothing reports is not zero.
2. Fix the trigger.
3. Only then teach it states, behind its own flag.

## 3. What the writer must learn, once it may

Stated now because `03 §5` step 5 depends on it, not because it should be built
now.

- **States are asserted, not inferred from tone.** "We are playing X" needs the
  room to have said so. The distinction the bath-gathering case failed is
  between *what was said* and *what would be a reasonable plan given what was
  said*.
- **A state needs `holds_until` at creation**, and the writer has to propose one.
  A state with no expiry is a memory that will be raised forever.
- **A recurring thread is revised, not recreated.** `01 §4.2` — what do we play
  next — should be one row that gets better each cycle. That means the writer's
  first question on a game conversation is *does a thread for this already
  exist*, and `note_search` is the verb.

## 4. The instrument is a precondition

`01 §5` records what the suite cannot currently do:

1. **Recall at n=5 is noise** — 0.20 and 0.50 on an identical prompt (#158).
2. **No case exists for anything in `01 §4`** — no dateless-but-live note, no
   state, no stall, no participation exit. The suite cannot fail on the subject
   of this bundle, so it cannot pass on it either.
3. **The quiet side is the one number that has held**, and it is what protects
   the room from her.

**This bundle therefore treats the corpus as work item zero.** The argument is
not general good practice; it is this repo's own record, twice:

- The Relevance Gate: three instruction rewrites judged on one live example
  each, one of which breached safety, before anyone ran the 40-case suite that
  already existed (`~/.claude/CMS.md`).
- 2026-09-10: three proactive objective variants compared at n=5 and two of
  them reported as improvements, on a measurement that swings by more than the
  effect (`01 §2`).

### 4.1 What must exist before any of `02`–`05` is judged

| case | shape | expectation |
|---|---|---|
| a thread stalled 10 days, moment a fortnight out | question | act — the wedding-dinner shape |
| the same thread, moved yesterday | question | do not act — proves it is the stall, not the age |
| a thread that stalled after **her own** answer | question | act — `02 §2.2` |
| a state, live, with an outside fact that fits it | state | act, `when_it_comes_up` |
| the same state, past `holds_until` | state | do not act, ever |
| a rejection-laden thread and a fresh proposal | question | act, and not repeat a rejected direction |
| the recommendation with no room trigger at all | state | act — `01 §4.3` |

The second and fifth rows are the important ones. **Cover the negative** — a
suite that only asks whether she acts will be passed by a system that always
acts, and the 0.02 false-positive rate is the asset most at risk from
everything in this bundle.

### 4.2 Sample counts

The quiet cases are settled at n=5 and have been all night. The acting cases
need enough samples that recall's spread is narrower than the effect being
looked for; n=15 was still coarse (`01 §1`). A per-expectation `--samples`
would stop paying for the stable half, and is named in #158.

## 5. Definition of done for this bundle

Not "the doctrine reads better". Three readings, in order:

1. **False-positive does not rise.** Non-negotiable, and measured on the same
   seven quiet cases at the same n.
2. **The new cases in §4.1 pass on a majority**, at a sample count where the
   result is stable across two runs of the identical prompt. Two runs, because
   one is what produced the wrong claims on 2026-09-10.
3. **Reverting the change turns a named test red** — the repo's own standard,
   and the one that separates a fix from a coincidence.
