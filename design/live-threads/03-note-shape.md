---
type: proposal
title: "What A Note Is — Shape, Boundary, Responsibility"
description: "A note stops being an unsettled question and becomes a live thread, which comes in two shapes: a question or a state. The Memory/Note line moves from subject to expiry. The tier's job becomes holding where each thread stands. Includes the case against, and the one date question #159 already opened."
tags: [nora, notes, schema, memory, boundary]
timestamp: '2026-09-11'
version: 1
---

# What A Note Is — Shape, Boundary, Responsibility

The operator named the three axes. This node takes them in that order.

## 1. Shape

**Today.** `Note` requires `uncertainty` and `closing_condition`. Those two
fields are what make a note a question, and `_required` refuses a note without
them. A note is, by construction, *a thing that is undecided plus the thing
that would decide it*.

**The problem.** `01 §4.3` and `01 §4.4` are live, valuable, and have neither
field. "They are playing X and are nearly done" has no uncertainty. "His ankle
is hurt" has no closing condition — it does not get *decided*, it gets
*better*.

**Proposed.** A note is **a live thread**, in one of two shapes:

| shape | what it is | ends when | example |
|---|---|---|---|
| **question** | something unsettled + what would settle it | it is settled | where to eat on the 27th |
| **state** | something true now + when it stops being true | it stops being true | his ankle is hurt; they are playing X, ~90% |

Everything downstream is indifferent to which: both accumulate revisions, both
appear in both indexes, both retire, both are read by the proactive pass and by
every tag and participation turn. The *only* thing that currently forces the
question shape is two required columns.

**Concretely:** `uncertainty` and `closing_condition` become nullable and
shape-dependent, and a `shape` discriminator plus a `holds_until` (see `04 §3`)
are added. `_required` moves from "these two fields" to "the fields this
shape needs".

## 2. Boundary — Memory vs Note

**Today** the line is drawn on **subject**: Memory is about a person, Note is
about the group's open questions. It is written into `conduct.md` (`02 §3.2`).

**The problem.** It puts `01 §4.4` in the wrong place. A sprained ankle *is* a
fact about a person, so the rule routes it to memory — where nothing expires,
so she knows it forever and is never prompted to use it, and would be equally
happy to ask about it in March.

**Proposed.** Draw the line on **permanence**:

- **Memory** — what stays true. Two cats. A new job. A dislike.
- **Note** — **what is true now and will stop being true.** Whether it is about
  the group or about one person is irrelevant.
- **Reminder** — a moment that must arrive.

Under this line, `01 §4.4` is a note with an expiry, and the fact that it
expires is the *reason* it is a note. `01 §4.2`'s accumulated refusals split
correctly too: *Ada doesn't play horror* is durable and belongs in memory; *we
are on X and nearly done* expires and belongs in the note.

**This is the load-bearing change.** `01 §4.3` — the recommendation — only
works because the state exists: without *they are playing X, nearly done*, an
outside fact about a new release is an advertisement. The state is what makes
the world relevant.

## 3. The date question (#159)

Already filed, and it is a subset of this node. `Note.anchor_date` means *when
the thing happens*, and `default_revisit_at` derives the tier's scheduled look
from that same date. So every clock a note has counts down to the anchor.

`01 §3` (d03) is the failure: Ben owes an answer today, the party is on the
25th, and the index line reads `about Fri 25 Sep, in 14 days`. The day that
matters is prose in the description, beside a number the model weights more.

Under §1 this is naturally expressed rather than bolted on: a question-shaped
note can carry **when it is owed** separately from **when the thing happens**,
and the tier's scheduled look derives from the earlier of the two. A
state-shaped note carries `holds_until` instead and has no anchor at all.

An instruction-level workaround was attempted (objective v5: "the date on a note
line is when the thing happens, and a note can be owed well before that"). It is
parked, unmeasured, on `mikeng-io/objective-v5-unmeasured`. It cannot be shown
to work (#158) and it puts the day nowhere a deterministic check can read it,
which is the definition of a non-fix.

## 4. Responsibility — and the argument against this whole node

**Proposed.** The tier's job moves from *track uncertainty until it settles* to
**be the record of where each live thread stands**, which means carrying three
things it does not carry now:

1. **when it last moved** — `04 §2`
2. **when it stops being true** — `holds_until`, §1
3. **whether it is worth waiting for an opening or worth choosing a moment** —
   `04 §4`

### 4.1 The case against: this should be a third store

A state is not a question, and merging them means every consumer branches on
`shape`. A separate store would keep each one honest and would let states have
their own retention, their own writer, and their own index.

**Why the merge wins anyway**, on four counts, all verifiable in the tree:

- Every mechanism a state needs already exists on notes: accumulating revisions
  (`append_revision`), retirement as a status rather than a delete, a
  per-workspace index, both a strike counter and a scheduled revisit.
- **Both turns already read notes.** `prefill._notes_block` → `render_notes`
  for the tag and participation path; the proactive pass builds its own index
  off the same store. A third store means wiring both again, and means the two
  can drift.
- A thread genuinely changes shape. "What do we play next" (question) resolves
  into "we are playing X" (state) which decays into "we are nearly done"
  (question again, and the next cycle's opening). One row that changes shape
  keeps the history; two stores means a handoff, and handoffs lose things.
- The retirement machinery is the same machinery. A settled question and an
  expired state both stop being live, and `retired_at` / `retire_reason`
  already say so.

**Recorded as DEC-165 with this argument attached**, so a later reader who
reaches for the third store can see it was considered rather than missed.

### 4.2 What this does NOT relax

A state does not license speaking. `00 §8`: the test is whether she holds
something the room does not, that closing would change what they do. A state
she has already used is closed by the same rule that closes an answered
question — the anti-nagging rules are unchanged and are, under this framing,
*derived* rather than enumerated.

## 5. Migration shape

Additive first, and reversible until the writer changes:

1. `shape` column, defaulted to `question`, and every existing row is a
   question — which is true, since that is all the tier could hold.
2. `uncertainty` / `closing_condition` become nullable. No existing row is
   affected; nothing yet writes a null.
3. `holds_until` added, null everywhere.
4. Renderers learn the second shape but nothing produces one yet (`04 §2`).
5. Only then does the writer learn to create states (`06 §2`), behind its own
   flag, after its trigger is fixed.

Steps 1–4 are inert in production: with no state-shaped row in the database,
every prompt renders exactly as it does today. That is the property that makes
this safe to land before DEC-163 is answered — though `06 §4` argues it should
not be *judged* before then.
