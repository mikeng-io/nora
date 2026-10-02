---
type: proposal
title: "Decisions, Order, And Blast Radius"
description: "DEC-163 through DEC-172 with what would reopen each, the order the layers must land in and why, what is reversible, and what is deliberately left undecided."
tags: [nora, decisions, migration, risk]
timestamp: '2026-09-11'
version: 1
---

# Decisions, Order, And Blast Radius

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


## 1. Decisions

Each carries the evidence it rests on and the reading that would reopen it.
Numbers continue from DEC-162 (the proactive adaptor).

### DEC-163 — she keeps track between turns · **operator-only · gates the bundle**

`identity.md`'s «then you're done» becomes «stopping talking is not the same as
being finished». *Evidence:* `01 §1` — recall 0.34 against false-positive 0.02,
which is the doctrine executing as written; `01 §4.1`, where her own answer was
left unacted-on because the turn had closed. *Reopens if:* the operator judges
that an agent which holds threads is a worse thing to have in a friend group
than one that answers and stops — which is a values question, not a measurement.

### DEC-164 — silence keeps its permission and loses its quota

`silence.md` drops «Most turns nobody asked for end that way» and states the
late/early asymmetry instead (`02 §2.1`, `02 §4`). *Evidence:* d02 collapsing
5/5 → 1/5 under one added constraint (`01 §3`) — under pressure she resolves
toward silence. *Reopens if:* false-positive rises at all after it lands. That
number is the asset (`06 §5.1`).

### DEC-165 — a note is a live thread, in two shapes · **schema**

Question or state; `uncertainty`/`closing_condition` become shape-dependent
(`03 §1`). The Memory/Note line moves from **subject** to **expiry** (`03 §2`),
and `conduct.md`'s three-way record rule is re-cut to match (`02 §3.2`).
*Evidence:* `01 §4.3` and `01 §4.4`, neither of which the current shape can
hold. *Considered and rejected:* a separate state store — argument and four
counts at `03 §4.1`. *Reopens if:* consumers end up branching on `shape` in
more than the two renderers, which would mean the merge was cosmetic.

### DEC-166 — a note is owed separately from when the thing happens

Supersedes issue #159 (`03 §3`). A question-shaped note carries an owed date;
the scheduled look derives from the earlier of owed and anchor. *Evidence:* d03
failing 6/15 with the deadline present only as prose. *Reopens if:* the
note-candidates adaptor cannot reliably extract an owed date, in which case
this becomes a rendering problem rather than a schema one.

### DEC-167 — both renderers carry how long since a thread last moved

`04 §2`. Rendered always, not only when stale; "moved" means a tag-path
revision. *Evidence:* `01 §4.1` and `01 §4.2` are entirely this; the Relevance
Gate precedent, where rendering an age cut errors by two thirds. *Reopens if:*
a measured run shows it does not move the stall cases — which requires `06
§4.1`'s cases to exist first.

### DEC-168 — a state carries when it stops being true

`holds_until` (`04 §3`), and it, not the strike counter, retires a state. A
standing interest that recurs forever must not be retired for not being
answered. *Reopens if:* writers cannot propose a sane expiry, in which case
states need a default horizon and a review rather than an author's guess.

### DEC-169 — two exits, and the note is the channel

`when_it_comes_up` vs `choose_a_moment` as a note field (`04 §4`). *Evidence:*
`01 §4.4`, where the same sentence is care or intrusion depending only on the
exit; and the verified fact that both turns already read the same store.
*Reopens if:* `04 §4.2` proves out badly — a room that rarely trips the
Relevance Gate provides no openings, and `when_it_comes_up` there is a slow
drop rather than a wait.

### DEC-170 — the trigger is fixed before the shape is widened

`06 §2`. The note writer's false-positive rate is measured, then its trigger
fixed, and only then may it write states. *Evidence:* 「bath gathering tonight」
— a plan manufactured from a logistics exchange. *Does not reopen:* this is a
sequencing rule, and the cost of getting it wrong is wrong beliefs about people.

### DEC-171 — the corpus precedes the change

`06 §4`. No claim about `02`–`05` is accepted from the suite as it stands.
*Evidence:* two dated failures of exactly this in this repo (`06 §4`).
*Does not reopen.*

### DEC-172 — Standing stays a position

It does not become the accumulator (`05 §2`) and the per-thread split is
deferred pending a count of how many live threads a real room carries
(`05 §5`). *Reopens if:* that count comes back high.

## 2. Order, and why it is not negotiable

```
DEC-163  identity                    ← gates everything
   │
   ├── DEC-164  silence.md
   ├── DEC-165  conduct.md (with the schema)
   │
DEC-171  the corpus                  ← before anything is JUDGED
   │
DEC-167  last-moved renderers        ← additive, inert, measurable
   │
DEC-165/166/168  schema              ← additive, inert until written to
   │
DEC-169  two exits                   ← needs the schema and the corpus
   │
DEC-170  the writer                  ← last, and only after its own trigger fix
```

Two constraints do the work:

- **Identity first**, because `01 §1` is the demonstration of what happens
  otherwise: a night of downstream rewrites against a doctrine that says stop,
  and recall moved from 0.25 to 0.34, inside the noise.
- **The writer last**, because it is the only step that creates rows, and rows
  are the only thing here that is not trivially reversible.

## 3. Blast radius

| step | touches | reversible | live risk |
|---|---|---|---|
| DEC-163/164 doctrine | every occasion's prompt | yes — file revert, hot-reloads | **high**: changes what she says in every room, immediately |
| DEC-167 renderers | both note indexes | yes | low: adds one clause per line |
| DEC-165/166/168 schema | `notes` table | additive; the columns stay | **none until written to** — `03 §5` |
| DEC-169 exits | one field + participation reading | yes | low while no row sets it |
| DEC-170 writer | creates rows | **no** — rows persist | **highest** |

Note the shape of that table: the two ends are the dangerous ones and the
middle is nearly free. The doctrine change is high-risk because doctrine
hot-reloads on the deployment — a doctrine edit is a live deploy, with no
build step between the file and the room.

## 4. Deliberately undecided

- **Whether a state is a note shape or a third store.** `03 §4.1` argues the
  merge and records the counter-argument; if the branching spreads, revisit.
- **Whether Standing goes per-thread.** `05 §5`, pending a count.
- **The fire path's missing `TagTrace`**, which is why participation and
  proactive turns cannot write Standing (`05 §4`). Out of scope, owned
  elsewhere, and it bounds what DEC-167 can achieve.
- **What the note writer's false-positive rate actually is.** Unmeasured, and
  DEC-170 makes measuring it the first step rather than assuming a number.

## 5. What is parked

`mikeng-io/objective-v5-unmeasured` holds an instruction-level attempt at
DEC-166 and a narrowing of the pass's memory reads. Not merged: `01 §2` says
the suite cannot decide whether it helps. It is kept so the wording is not
lost, and it should be re-judged against the corpus DEC-171 requires — not
merged on the strength of the runs that produced it.
