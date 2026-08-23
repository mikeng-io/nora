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

### 4.0 The cap: 1200 characters, and it is an invariant not a knob

The overflow *mechanism* was designed and **the number never was** — §4 says
refuse-and-consolidate, and nothing said what it refuses at. A cap mechanism
with no cap is a branch that never executes.

**`Standing.body` is capped at 1200 characters**, enforced in **two places**,
exactly as the note tier enforces its own injected fields:

- a **domain constant**, so a malformed fold is refused before the sink;
- a **DB `CHECK`**, so no writer can bypass it — the shape of
  `note_title_fits_the_index_check`, and named for the same reason: the cap
  exists *because* the field is injected.

**Why 1200.** Standing is injected on the volatile tail of **every turn**, so
its size is a per-turn tax, not a storage question. In English 1200 characters
is roughly 300 tokens; **in a CJK-heavy room it approaches 1200 tokens**, and
this room is mixed by default — the cap has to be read against the denser case,
not the average. For comparison the note index injects 260 characters per note
with no cap on the count. And a conversational position that cannot be stated in
1200 characters is a summary, not a position — the tier is *lossy by design*
(§8), so a cap that never binds would mean it had stopped being lossy.

**Why it is not a config key**, which is the part most easily got wrong:

> **Render caps are configuration; storage caps are invariants.**

`max_rendered_facts` and `max_rendered_lexemes` are config because they decide
**how much of a store to show**. This decides **what may exist in a row**.
Making it a knob means raising it silently changes every prompt in every
conversation with no migration and no gate — and the `CHECK` is what makes the
invariant true of rows written by any future writer, including one nobody has
thought of yet.

**The overflow path is the safety, not the cap.** When a fold would exceed
1200, the write is **refused**, the current inventory is returned, and the model
is told to consolidate now. That keeps the failure *model-resolvable* rather
than making the cap a cliff — and it is why the cap can be tight without being
brittle.

**Counted, never silent.** Overflow refusals are a metric (§6), for the reason
Phase 1 already established for the trace: a truncation nobody counts is a
`nothing_found` that was really an `unavailable`. Never firing means the cap is
not binding; firing constantly means it is too small. Both are readings.

### 4.0.1 What the trace cap already gets right, and Standing inherits

Phase 1's `cap_messages` truncates **assistant narration only and pins human
turns**, on the stated ground that *intent cannot be reconstructed from the work
that followed*. The same asymmetry applies to the fold: when Standing must
shrink, **what the room said outranks what Nora concluded about it.** A position
is re-derivable from the transcript; the transcript is not re-derivable from the
position.

### 4.1 The risk this shape carries, restated

Standing is update-in-place, so **a successful prompt injection persists** until
a later fold drops it. Every other untrusted block is rebuilt from the store
each turn, so an injection there lives one turn.

The design markets that persistence as the feature — it is the same property
that makes a position *hold*. Escaping is necessary and **not obviously
sufficient**. Accepted, with a named review point, and not solved.

## 4.2 What the debrief is actually shown — the rendering contract

The type carries everything; **nothing renders it yet**, and that gap is how the
fact tier failed — identity present on the row, dropped at the prompt, noticed
months later ([21-substrate-contract.md](21-substrate-contract.md) §7.1). The
contract is therefore stated here rather than discovered by whoever writes the
stage.

**What the fold receives:**

```
TagTrace
  tag_id · conversation_id · outcome · created_at
  messages    = agent.messages[baseline:]     the model exchange
  transcript  = (TranscriptRow, ...)          the room's own messages
prior: Standing                               the fold's SECOND argument
```

`outcome` is one of `send_reply · recovered · silent · vetoed · timeout ·
llm_error`, and `prior` is deliberately not a `TagTrace` field: the type is
built at the extraction boundary, which has no storage access.

**A worked render**, in the shape the stage must produce:

```
<standing_prior>
  They are planning dinner for the 9th; venue undecided between three.
  Grant is travelling and replies late.
</standing_prior>

<turn outcome="silent" at="2026-08-23 21:04 HKT">
  <transcript>
    [21:02] Miles: 邊個做嘅大步行?
    [21:03] Grant (replying to 21:02): House House 啊
    [21:04] Nora: (no reply sent — R-5)
  </transcript>
  <reasoning>
    ...the model exchange, tool calls and results...
  </reasoning>
</turn>
```

**Six mappings a renderer must not get wrong**, each one a defect this bundle
has already seen in another tier:

| field | the requirement | the failure it prevents |
|---|---|---|
| `ts` | rendered as **local wall-clock time**, never the raw bigint | the model reasoning about `1756012345`. *"You are not even including the timestamp"* is the fact tier's critique verbatim |
| `is_nora` | **always rendered**, as a distinct speaker | the fact tier's exact defect — a batch where nobody can tell who said what |
| `sender_name` / `person_name` | the **display name**, never `person_id` | §4.3 |
| `quoted_ts` | rendered as a reply relation | *"yeah that one"* becomes unresolvable, and the debrief mis-reads what a turn was about |
| `outcome` | **always**, including *why* silent | §2 makes silence the case most worth reviewing; a render that omits it defeats the stage's own purpose |
| `prior` | **always present**, even when empty | §4's non-negotiable. A fold that cannot see the prior position cannot correct it |

### 4.3 The trace holds identifiers, and Standing is injected forever

**The sanitizer strips synthetic artifacts and caps length. It does not redact
identifiers, coordinates or addresses** — and that is correct for a *record*.

But `messages` holds raw tool results, and a `where_is` result contains **a live
position**. So there is a path:

> `where_is` result → `tag_trace.messages` → the debrief prompt → `Standing` →
> **injected into every subsequent turn**

§4.1 records injection *persistence* as the carried risk. **This is a different
one and it was not recorded**: a coordinate or an identifier entering Standing
through the trace, and then persisting **by design** rather than by attack.

The guards do not cover it. R-18/R-28 screen **egress from a reply**; Standing
is not a reply, it is an **input**, re-injected indefinitely.

**Two rules follow, and they belong to this stage:**

- **The debrief's transcript render uses display names and never `person_id`.**
  The identifier is available for the code-side join; it has no reason to be in
  a prompt whose output persists.
- **`Standing` passes the disclosure subset before it is written**, not before
  it is read. Screening at read time is the mistake F3 already refuted for room
  access — the gate belongs where the content class changes, and that is the
  write.

This is the same shape as [11-disclosure.md](11-disclosure.md)'s finding, one
tier over: **a read that looks safe because something downstream screens it, and
the screen is on the wrong side of the persistence boundary.**

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
| **overflow refusals** | §4.0's refuse-and-consolidate path firing is healthy; never firing means the 1200-character cap is not binding, and firing constantly means it is too small |

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
