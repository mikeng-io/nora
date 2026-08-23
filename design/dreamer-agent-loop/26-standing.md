---
type: contract
title: "Standing — A Second Model Reviewing the Finished Exchange"
description: "How Standing runs, how the roll works, and what it measures. A pipeline stage on the tag path — not a Dreamer adaptor — producing four outputs from one call, with the per-conversation row revised in place and the per-turn row never rewritten."
tags: [nora, standing, debrief, rolling, tag-path, measurement]
timestamp: '2026-08-23'
edges:
  - {dst: 15-roadmap, rel: realizes, provenance: asserted}
  - {dst: 12-self-correction, rel: depends_on, provenance: asserted}
  - {dst: 19-between-tiers, rel: extends, provenance: asserted}
---

# Standing

## 1. It is a stage, not an adaptor — and that is the whole shape

Everything else in this bundle is a **Dreamer adaptor**: background, unattended,
on its own clock. **Standing is not.** The debrief is a **pipeline stage on the
tag path**, running inside the same slot as the turn it reviews, immediately
after the reply is delivered.

Three consequences follow, and they explain every other decision here:

**It is a SECOND model reading a FINISHED exchange.** Not the model that
replied, reflecting — a different call, seeing the whole turn including its own
outcome. The argument is **impartiality, not cost**: a model asked to judge its
reply mid-turn is judging under social pressure, with the reply still to write.

**It is why Nora's own tools cannot do this.** `note_create`/`note_update` are
in-turn tools, and the reply guard cancels the next model call once the terminal
budget is spent — **nothing in her own loop runs after the reply.** Standing has
an after-the-reply path by construction; that is why the note tier's proposals
ride on it rather than getting a stage of their own.

**And it holds the slot.** Which makes latency a first-class design constraint
rather than an operational detail — §5.

## 2. It runs on EVERY completed turn

Not only the ones that sent something. Success, R-5 silence, a veto, a timeout —
all of them. `outcome` carries which kind.

The temptation is to inherit the feedback stage's *"if sent and there is a
tagger"* gate. **That would be wrong, and the reason is this bundle's own
subject:** a turn that deliberately said nothing **produced just as much
reasoning as one that replied**, and R-5 silence is the case most worth
reviewing — it is the decision most likely to be wrong in a way nobody
complains about.

A debrief that only reviews successful replies measures the path that already
works.

## 3. Four outputs, one call

| output | cardinality | rewritten? |
|---|---|---|
| **`Standing`** | one row per **conversation** | **yes — in place** |
| **`TurnVerdict`** | one row per **turn** | **never** |
| **fact proposals** | zero or more | append-only |
| **note proposals** | zero or more | append-only |

**The split between the first two is load-bearing and was found by a bug.** The
first draft put per-turn fields on the per-conversation row — and the
re-derivation trigger reads a flag that the *next turn had already overwritten*.
A per-turn fact on a row revised every turn is a fact with a lifetime of one
turn.

**Both proposal kinds go through a sink, never a tool.** The debrief *proposes*;
it never adjudicates. The argument against in-slot judgment applies to facts and
notes identically — a model deciding truth while the room waits is the shape
this whole bundle refuses.

## 4. The roll

**One row per conversation, revised in place, from a sliding window of the
transcript.**

**The window is `ctx.transcript`** — already assembled and rehydrated for the
reply. It is deliberately **not a watermark slice**, for two concrete reasons:
the record store's `since` excludes Nora's own rows in SQL, so a watermark
window would show a conversation with her half removed; and `tag_id` has no
ordering relation to message ids, so there is nothing to slice on coherently.

**The fold must read the current Standing before writing the next one.** This is
the non-negotiable ([15](15-roadmap.md) §6): without it the roll reproduces the
language tier's exact failure — two contradictory positions coexisting, neither
able to correct the other — which is the defect D19 reverses one tier over.
Shipping it twice would be a choice.

**Re-derivation fires when `grounded` is false, plus a fold-count ceiling**
(S2). Time alone re-derives healthy conversations for nothing; the forced case
catches the turn that actually went wrong, and the ceiling bounds accumulated
drift.

**Overflow is a model-resolvable error, never silent eviction.** When the row
will not fit, the write is **refused**, the inventory is returned, and the model
is told to consolidate now. Silently dropping the oldest position is how a
rolling summary quietly becomes a recent summary — the same defect as the
recency slice in [20-knowledge-tier.md](20-knowledge-tier.md) §6, one tier over.

**Injection is the volatile tail only** — escaped, marked untrusted, and never
part of the cached prefix. A row revised every turn inside a cached prefix would
invalidate the cache every turn.

### 4.1 The risk this shape carries, restated

Standing is update-in-place, so **a successful prompt injection persists** until
a later fold drops it. Every other untrusted block is rebuilt from the store
each turn, so an injection there lives one turn.

The design markets that persistence as the feature — it is the same property
that makes a position *hold*. Escaping is necessary and **not obviously
sufficient**. Accepted, with a named review point, and not solved.

## 5. Latency is a design constraint, not an operational one

Because the debrief holds the slot, its cost is **user-visible — but not to the
person it is reviewing.** It lands on the **second person queued behind a busy
conversation.** The first person already has their reply.

That is the reading that matters, and it is why the gate names the
second-queued-tag case explicitly rather than measuring an average.

- **`nora_debrief_seconds`, p95 ≤ 5s.**
- **Excluded from `nora_tag_latency_seconds`** — folding it in would move an
  existing SLO series by changing what it measures, which is a broken gate
  wearing a passing number.
- The debrief gets **its own timeout**, and `tag_timeout + debrief_timeout` must
  fit inside the maximum acceptable slot hold.

**Two failure shapes the stage must not create**, both of which turn a working
turn into a false alarm:

- mark the turn **completed before** awaiting the debrief — otherwise a failure
  after successful delivery reports a partial reply on a turn that sent a
  complete one;
- catch `BaseException`, never re-raise — cancellation is not an `Exception`, so
  `except Exception` leaves the cancel path open and reports a silent failure
  that did not happen.

## 6. What it measures

[09-measurement.md](09-measurement.md)'s rule, and §6.2b of
[15-roadmap.md](15-roadmap.md) already records what Phase 1 is missing.

| reading | why |
|---|---|
| **`traces_written / tags_completed`** | the recording layer must be able to show that it recorded. Written in a `finally` on every exit path, so the expected ratio is **1.0** and any sustained departure is a defect, not a judgement |
| **outcome distribution** | the column is already written; counting it is a label on a counter that must exist anyway. It is also the only way to see that R-5 silences are being reviewed at all |
| **`nora_debrief_seconds`** | §5, with the second-queued case |
| **folds that read prior state** | the §4 non-negotiable, observable rather than assumed — a fold that read nothing is the failure D19 exists to prevent |
| **re-derivations, by trigger** | forced-on-ungrounded versus ceiling. If the ceiling dominates, the trigger is a clock wearing a condition |
| **overflow refusals** | §4's refuse-and-consolidate path firing is healthy; never firing means the cap is not binding, and firing constantly means it is too small |

`nora_tagtrace_truncations_total` already exists and is properly wired. It is
the only one that does.

## 7. The two sub-decisions the plan left open

**The note proposal payload: two shapes with a discriminant, not one shape with
optional fields.** A new note needs title, description, uncertainty, closing
condition and anchor; a revision needs body and a parent revision id. One type
covering both makes **every field optional**, which means neither shape is
validated and the sink cannot refuse a malformed proposal — the schema stops
being a gate.

**The debrief must not re-propose what the turn already rejected.** The tag path
may have read a note and decided against touching it; the debrief, reviewing the
same transcript, would propose it again. **The turn's note-read set is part of
what the debrief is given**, and a proposal against a note in that set is
refused at the sink. Without it the two paths argue with each other once per
turn, forever.

## 8. Where Standing sits against the other tiers

It is the only tier that is **a position rather than a record.** Facts are
claims with warrants; notes are questions with closing conditions; lexemes are
meanings. **Standing is what Nora currently thinks about a conversation**,
rewritten every turn and **lossy by design.**

That is why it must not be the place a wedding venue lives
([19-between-tiers.md](19-between-tiers.md)) — a lossy, rewritten row is the
wrong home for something that has to survive. Standing points at the tiers that
keep things; it does not keep them.

## Status / Checkpoint

2026-08-23 — written after the operator asked how Standing runs, rolls and is
measured, and the first answer described build status rather than design.
Standing had a **roadmap** in this bundle ([15](15-roadmap.md) §6) and no design
node; this is that node. Phase 1 (recording) is complete on an unmerged branch;
Phase 2 (this) has zero code, which is why every decision above is still free.
