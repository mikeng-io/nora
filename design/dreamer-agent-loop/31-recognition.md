---
type: design
title: "Recognition — What Notices That Somebody Just Contradicted Her"
description: "Step 3 of the correction loop, the one step with no mechanism in 31 nodes. Targeting is deterministic and decides the blast radius; the dispute predicate is semantic and is offered ordinal handles. Two channels with different powers because they land at different points in the turn."
tags: [nora, dreamer, correction-loop, recognition, facts, disputed]
timestamp: '2026-08-28'
edges:
  - {dst: 30-review-response, rel: extends, provenance: asserted}
  - {dst: 16-open-items, rel: closes, provenance: asserted}
  - {dst: 12-self-correction, rel: depends_on, provenance: asserted}
  - {dst: 02-instrument-blindness, rel: motivated_by, provenance: asserted}
---

# Recognition

[30](30-review-response.md) §2.1.1 sequences the correction loop in nine steps.
Step 3 — `RECOGNISE a member contradicting a claim Nora just rendered` — is the
only one with no mechanism. `RECOGNISE` occurs exactly once in the bundle, as
that line. [16](16-open-items.md) §12.a states the consequence: **D25 is a
decision with no mechanism under it**, and by elimination the recogniser is the
reply-path model, which rests the primary trust loop on
[02](02-instrument-blindness.md)'s named pathology — *she recognises and does
not act*.

This node is that mechanism.

## 1. The decomposition — and why it is not one classifier

Recognition looks like one question (*is this a contradiction?*) and is two:

| | question | kind | if it is wrong |
|---|---|---|---|
| **TARGETING** | *which standing rows could this message be about?* | structural | the blast radius is wrong — the wrong row is suppressed, or the right one is unreachable |
| **DISPUTE** | *is this message contradicting one of them?* | semantic | a dispute is missed, or ordinary chat mutes a correct row |

**Targeting must be deterministic**, for two independent reasons. It decides
what gets suppressed, and a model choosing which rows to suppress would be
choosing them **by identifier** — the exact shape `dream_screen` drops silently
while the tier's counters stay healthy. Targeting produces a bounded set that
the model addresses by **ordinal handle**, never by id.

**The dispute predicate is genuinely semantic** and pretending otherwise is how
this step stays undesigned. A cue list alone cannot separate *"no, big walk is a
video game"* from *"no I can't make the big walk"*; the difference is that the
first asserts a competing object for the same subject.

So the answer to *"deterministic first, model second"* is not one channel
falling back to another. It is: **the deterministic half decides WHAT IS IN
SCOPE and can act alone only on evidence that needs no reading; the model half
decides IS THIS A DISPUTE over that scope, and it is measured on whether it
does.**

## 2. The precondition nothing has: a render ledger

Step 3's own words are *"a claim Nora just **rendered**"*. Nothing in the
codebase records what she rendered. `TagTrace` keeps `messages` and
`transcript`; it does not keep which `fact` or `room_lexeme` rows went into the
prefill. Recovering them by parsing the `<facts>` block back out of a stored
prefill would be **interpreting twice** — the spine's first rule, and the way
two copies drift.

So the render site records it, because that is the one place the row ids are
already in hand:

```
rendered_claim(
  conversation_id  text        not null,
  kind             text        not null,   -- 'fact' | 'lexeme'
  row_id           uuid        not null,   -- fact.id / room_lexeme's key
  surface          text        not null,   -- fact.subject / lexeme.canonical_form
  tag_id           text        not null,   -- provenance, not a join key (§3.1)
  rendered_at      timestamptz not null,
  primary key (conversation_id, kind, row_id, tag_id)
)
```

Written by the same code path that builds the block, in the same turn, from the
same tuple it renders. Read only by recognition. Pruned on the recognition
window, not retained — this is a working set, not an audit trail; the audit
trail is the Langfuse trace and the `tag_trace` row.

> **This table is also the measurement.** Without it, *"Nora rendered it"* is an
> assumption. With it, it is a read. That is the difference between a `measure`
> step and a proxy.

## 3. Channel A — deterministic targeting

Runs **on the tag path, before context assembly**. Two sub-channels, unioned.

### 3.1 T1 — the quote link

Both platforms carry quote-reply. `NormalizedMessage.quote.quoted_ts` resolves
to a parent row via `(conversation_id, ts)`; `messages.is_nora = 1` says the
parent is hers.

**Resolution is by conversation and window, not by exact turn.** `messages`
carries no `tag_id` and will not gain one for this: within one conversation the
same standing rows render on consecutive turns, so an exact turn join buys
essentially nothing over *"the rendered claims of this conversation inside the
window"*. `rendered_claim.tag_id` is therefore provenance — it says which turn
put the row in scope — and never a join key.

> Reversal condition: if targeting precision measures poorly and the misses are
> attributable to window slop rather than to the surface matcher, the exact
> join is a nullable `tag_id` on Nora's own `messages` rows, written in the same
> INSERT. Not before there is a reading that says so.

### 3.2 T2 — surface containment

For each `rendered_claim` in the conversation inside the window, test the
message body for the row's `surface`.

**The matcher is casefolded, whitespace-normalised containment — not
similarity.** Three reasons, and they are the whole argument:

- **The job is containment, not similarity.** *Does this message talk about
  `big walk`* is a substring question. `pg_trgm`'s `similarity()` answers a
  different one, and [25](25-knowledge-pipeline.md) §4.1 already measured its
  threshold failing on `Big Walk`/`Big Walks` at 0.833 against 0.9 — for the
  easier job of matching two entity names to each other.
- **A threshold nobody can calibrate is not a measurement.**
  [16](16-open-items.md) §11 says there is no graded corpus, so any number
  chosen here would be chosen blind and would then look like evidence.
- **Containment is collation-independent.** Every trigram and range path in this
  database is about to be reindexed under a different libc's `en_US.utf8`
  ordering. A recogniser that does not depend on collation is one fewer thing
  that can silently change meaning across that swap.

CJK needs no word boundary and gets containment directly. Latin surfaces take a
boundary check on both ends so `art` does not match `party`.

> Reversal condition: trigram as a **second** targeting sub-channel, once the
> graded corpus exists and can say what recall containment leaves on the table.
> It is additive — it can only widen the target set — so it never needs to
> displace T1/T2.

### 3.3 The two tiers of scope, and why only one may suppress

| tier | what puts a row in scope | effect |
|---|---|---|
| **direct** | rendered in this conversation inside the window, matched by T1 or T2 | eligible for **suppression** |
| **latent** | standing in this workspace, surface-matched, **not** recently rendered | eligible for a **priority candidate only** |

The cut is the step's own wording. *"A claim Nora just rendered"* is the thing a
member can be reacting to; a row she has never said is a row nobody in the room
knows she holds, so a message that happens to name its subject is not evidence
against it. Latent scope still matters — a correction can arrive before she ever
renders the row — so it proposes, at priority, and never mutes.

This keeps false suppression bounded by *what she actually said*, which is the
only bound available that is not a tuned number.

## 4. Channel B — the dispute predicate

Granted as a tool, **only on turns where the direct target set is non-empty** —
the same conditional-collaborator shape `build_tools` already uses for notes and
image generation. On every other turn it does not exist and costs nothing.

```
dispute_claim(handle: int, reason: str)
```

The target set renders into the prefill as ordinal handles, in
`fact-adjudication`'s existing shape:

```
- [1] big walk | means | a walking activity the group does, maybe at night
- [2] big walk | made_by | (unknown)
```

**No identifier is ever shown and none is ever accepted.** `handle` is an index
into a set the runtime built and holds; a model that echoes anything else is
refused, counted, and its turn is otherwise unaffected. This is the rule that
caught three adaptors: anything a model echoes back is an ordinal handle.

The tool **proposes**. It sets `disputed`, which is the one thing a proposal may
do synchronously, because *stopping* a wrong answer needs no adjudication
([30](30-review-response.md) §2.1). It cannot rewrite, cannot clear a dispute,
and cannot reach a row outside the set.

## 5. What acts alone, and the timing that makes the split real

The two channels land at different points in the turn, and that is not an
implementation detail — it is why they have different powers.

```
message arrives
  │
  ├─ CHANNEL A          deterministic, pre-prefill
  │     targeting  ──►  direct set + latent set
  │     RULE-1     ──►  suppress NOW  ── affects THIS turn's own prefill
  │
  ├─ context assembly / prefill  (disputed rows already excluded)
  │
  ├─ model turn
  │     CHANNEL B  ──►  dispute_claim(handle)  ── affects the NEXT turn
  │
  └─ counters at every step (§8)
```

> **Deterministic recognition is pre-prefill and suppresses this turn. Model
> recognition is in-turn and suppresses from the next turn.** Same column, same
> transaction shape; only the timing differs.

**RULE-1 — the case that must not wait for the model to notice.** A quote-reply
to one of Nora's own messages from a turn that rendered row R, where the reply
carries a negation cue and the quoted message or the reply names R's surface.
That is unambiguous, and it is exactly the case where *"she recognises and does
not act"* would be least forgivable. It fires without the model.

**RULE-2 — everything else in the direct set** requires `dispute_claim`.

The negation cue set is a named constant in code, narrow on purpose, and
**graded by the loop's own output**: an adjudication verdict is ground truth for
whether the cue that fired was a real contradiction, so the set is falsifiable
rather than asserted. It is not doctrine — a cue list in doctrine becomes a
closed set the model reads as exhaustive.

**Why this is not the pathology [16](16-open-items.md) §12.a names.** Three
properties, and it needs all three:

1. the model **cannot invent a target** — the set is deterministic and handled;
2. the model **cannot fail the unambiguous case** — RULE-1 does not consult it;
3. the model's **inaction is a series** — a non-empty target set with no
   `dispute_claim` call increments `nora_dispute_unacted_total`. *She recognises
   and does not act* stops being an invisible failure and becomes a reading, in
   two series, which is [02](02-instrument-blindness.md)'s own prescription.

## 6. Fan-out across both tiers

[30](30-review-response.md) §2.1.1 requires it: one correction contradicts a
lexeme row and a fact row at once.

> The target set is built **per entity**. A match on `big walk` targets every
> standing `fact` whose subject resolves to that `fact_entity` **and** every
> `room_lexeme` whose `canonical_form` resolves to it. Marking a target marks
> every row in its entity group, in both tiers, **in one transaction**.

This is why OPERATOR-2 put the loop **with** pass 3 rather than before it: the
fan-out is over entity ids, and entity ids are pass 3's output.

**Before an entity id exists**, fan-out degrades to exact surface equality —
and the degradation is **counted** (`nora_dispute_fanout_unresolved_total`), not
silent. A tier that fans out correctly and one that cannot see its siblings look
identical from the outside; the counter is the difference.

## 7. The `disputed` state machine

[30](30-review-response.md) §2.1.1's review names this unspecified. Columns on
`fact` and `room_lexeme`:

```
disputed_at      timestamptz null
disputed_by      text        null   -- person_id of the asserter
disputed_tag_id  text        null   -- which turn recognised it
```

| event | rule |
|---|---|
| **set** | CAS on `disputed_at IS NULL AND superseded_at IS NULL` |
| **dispute on an already-superseded row** | no-op, counted `already_superseded`. The row already stopped rendering; there is nothing to suppress |
| **second dispute on a disputed row** | no-op on the row, counted `already_disputed`. The second asserter is recorded on the **candidate** it proposes — `fact_asserter` is where asserters live, so this needs no new junction |
| **clear** | only inside adjudication's transaction, CAS on `disputed_at = <the value read>`. A second member's dispute landing mid-dismissal changes it, the CAS fails, the dismissal does **not** clear, and the adjudication becomes a candidate carrying both. Same shape as §4's supersession CAS |
| **dismissal** | requires an **affirmative contradiction**. `not_covered` never clears a dispute — [30](30-review-response.md) §2.1.1, and R-53 one level up |
| **supersession** | clears implicitly: a superseded row does not render. The successor row starts undisputed |
| **promotion rewiring the object** | does not touch `disputed_at`. Different column, different transaction, idempotent either way. Stated so it is not derived |
| **an aged, unresolved dispute** | **does not auto-clear.** It is counted, and its age is a reading |

That last row is the one worth arguing. A TTL that restores a disputed row is
*dismissal on silence* — the precise move R-53 exists to forbid, applied to the
correction loop instead of to a web page. An unresolved dispute means we do not
know, and R-5 says silence beats a wrong answer. So the backlog is made
**visible** (`nora_dispute_open`, plus age) rather than drained: a tier that
mutes itself under an adjudication backlog and one with nothing to say look
identical, and the gauge is what tells them apart.

## 8. Instruments — a counter at every step

[30](30-review-response.md) step 9, and [09](09-measurement.md)'s rule that a
change whose effect cannot be read back is not done. Label vocabularies are
`get_args` off their `Literal`, per CLAUDE.md.

| series | what it says |
|---|---|
| `nora_dispute_targets_total{tier,scope,channel}` | the target set was built. **The positive control** — every other series here is compatible with a recogniser that never ran |
| `nora_dispute_recognised_total{tier,channel}` | a dispute was raised, `deterministic` or `model` |
| `nora_dispute_unacted_total` | direct set non-empty, tool granted, no call. §5's third property |
| `nora_dispute_handle_refused_total` | the model echoed something that was not a handle |
| `nora_dispute_suppressed_total{tier,status}` | `ok` / `already_disputed` / `already_superseded` / `unavailable` — R-27's states plus the CAS outcomes |
| `nora_dispute_fanout_unresolved_total` | targeted by surface because no entity id existed (§6) |
| `nora_dispute_resolution_total{outcome}` | `superseded` / `dismissed` / `stands_unconfirmed` — step 7, including §2.1.1's room-internal rule |
| `nora_dispute_open` (gauge) + age | the backlog §7 refuses to drain silently |

## 9. What would reopen this

Written now so a later challenge is cheap to answer and a flip is visible as a
flip.

- **`targets_total` climbs, `recognised_total` stays flat.** The model is not
  the recogniser in practice; RULE-1 must widen and carry the loop alone.
- **`suppressed_total` climbs, `resolution_total{superseded}` stays at zero.**
  Recognition is producing false positives and the tier is muting itself.
  Tighten targeting before touching anything else.
- **`fanout_unresolved_total` does not fall after pass 3 lands.** Entity
  resolution is not reaching this path.
- **A graded corpus exists** ([16](16-open-items.md) §11). Re-measure
  containment against trigram, and re-measure the cue set against adjudication
  verdicts.

## 10. What this node does not decide

- **Step 6's research loop** rests on the transport question
  ([16](16-open-items.md) items 0 and 1). Recognition does not: both channels
  are single-shot and neither needs tool-choice reliability or reasoning
  carried between turns. The loop can close through *"the correction stands,
  unconfirmed"* ([30](30-review-response.md) §2.1.1) with no research loop at
  all, which is what takes recognition off item 12.b's critical path.
- **The priority queue's ordering** is [25](25-knowledge-pipeline.md)'s; this
  node only says a dispute enters it ahead of recurrence.

## Status / Checkpoint

2026-08-28 — written to close [16](16-open-items.md) §12.a. Design only; no code
in this commit. The three sequencing consequences: `rendered_claim` must exist
before the loop can be tested at all, the `disputed` columns ride pass 3's
migration, and fan-out is meaningless until entity ids land — which is
[30](30-review-response.md) OPERATOR-2's cut, arrived at independently from the
mechanism side.
