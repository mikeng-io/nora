---
type: contract
title: "Standing — A Second Model Reviewing the Finished Exchange"
description: "How Standing runs, how the roll works, and what it measures. A pipeline stage on the tag path — not a Dreamer adaptor — producing four outputs from TWO calls under one gather (issue #78 Phase 2: the position call and the impartial verdict call), with the per-conversation row revised in place and the per-turn row never rewritten."
tags: [nora, standing, debrief, rolling, tag-path, measurement]
timestamp: '2026-08-23'
edges:
  - {dst: 15-roadmap, rel: realizes, provenance: asserted}
  - {dst: 12-self-correction, rel: depends_on, provenance: asserted}
  - {dst: 19-between-tiers, rel: extends, provenance: asserted}
---

> **PARTLY DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") and the language tier have both been deprecated and their code deleted.** Every claim below about either tier is retained as a record of the reasoning; **none of it describes code that exists.** The note tier, Standing, the relevance gate and the Dreamer runtime itself are unaffected; **recognition** ([31](31-recognition.md)) went with the two tiers whose rows it marked.
> Their tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.


> **PARTLY SUPERSEDED 2026-09-24 by [design/standing-compact/](../standing-compact/00-intent.md).** The verdict call (§3's call B and `TurnVerdict`), both re-derivation triggers (§4's ungrounded watermark and the fold-count ceiling), and the §6 readings that counted them are deleted. The fold is one call, the prior is always shown and carries its age, and the turn's steps render as `<steps>`. Everything below about them is retained as the record of the reasoning; none of it describes code that exists.

# Standing

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


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

**It is why Nora's own tools cannot do this.** `note_create`/`note_revise` are
in-turn tools, and the reply guard cancels the next model call once the terminal
budget is spent — **nothing in her own loop runs after the reply.** Standing has
an after-the-reply path by construction; that is why the note tier's proposals
ride on it rather than getting a stage of their own. *(Reversed 2026-09-24 —
see §7: the debrief no longer proposes notes.)*

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

## 3. Four outputs, two calls under one gather

> **As of 2026-09-24 the stage produces ONE output, `Standing`, from one call.**
> The verdict call and `TurnVerdict` were deleted with `design/standing-compact/`,
> and note proposals were removed the same day (see §7). Fact proposals were
> never built.

| output | cardinality | rewritten? | call |
|---|---|---|---|
| **`Standing`** | one row per **conversation** | **yes — in place** | A (position) |
| **`TurnVerdict`** | one row per **turn** | **never** | B (verdict) |
| **fact proposals** | zero or more | append-only | A |
| **note proposals** | zero or more | append-only | A |

**The split between the first two is load-bearing and was found by a bug.** The
first draft put per-turn fields on the per-conversation row — and the
re-derivation trigger reads a flag that the *next turn had already overwritten*.
A per-turn fact on a row revised every turn is a fact with a lifetime of one
turn.

**The call split is the impartiality (issue #78 Phase 2).** The position call
(A) revises the standing row in the first person; the verdict call (B) judges
the turn's sourcing with the whole turn in view and the standing prior OUT of
it. One completion that both wrote the position and graded its own sourcing
produced 21/21 `grounded=true` — a model asked to judge itself in the same
breath does not convict itself. The two calls run concurrently under one
`asyncio.gather`; the fold's wall time is max(A, B), not A + B. The verdict
call is skipped entirely when no verdict store is wired (`store_verdict is
None`), and a verdict failure never costs the position.

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

**AS BUILT: the ungrounded half is keyed on an ACTED-ON WATERMARK, not on a
timestamp comparison** (`standing.consumed_verdict_ms`, migration 0037,
2026-08-28). `TurnVerdictStore.latest` answers the newest verdict of ANY age and
a verdict is written only when a fold fully succeeds, so verdict-less turns are
the ordinary state and the read has to cope with them. With no guard, one
ungrounded verdict latched the trigger on forever. Two comparisons were then
tried as the remedy and **both were wrong, in opposite directions**:

- `created_at > prior.updated_at` — one `now_ms` per turn stamps the position
  and the verdict identically, so `>` refused the only sequence it existed for.
  The counter read flat zero, which §6 reads as the trigger having degraded into
  a clock — the same false reading the defect produced, from the other side.
- `created_at >= prior.updated_at` — correct on that sequence and still a
  **proxy**: `updated_at` stands in for "a rebuild consumed this verdict", and a
  proxy is falsifiable by a path that moves it without doing the thing. If the
  verdict read fails for ONE turn the trigger correctly declines (R-27), the
  fold revises anyway and advances `updated_at` past the verdict, and the
  comparison is false from then on. The verdict is not deferred, it is **gone**.

**The READ is ungrounded-only too, and the watermark alone did not close the
drop.** `TurnVerdictStore.latest_ungrounded` REPLACED a `latest` of either kind
rather than joining it. That read answers a question coinciding with the right
one until a turn goes by without consuming an ungrounded verdict — and then a
later GROUNDED verdict buries the older one and nothing looks past it again.
The burying turn is the ordinary shape of a successful fold following one where
this read failed, so it was reachable rather than theoretical: measured at 0
re-derivations, watermark still null, the verdict still in the store, over three
turns. `idx_turn_verdict_ungrounded` had existed for exactly this query and was
serving nothing. The wrong read is now unrepresentable rather than documented,
the same move the port already makes by having no `write`.

So the property is recorded rather than inferred. The row carries the
`created_at` of the verdict a re-derivation acted on; it advances only when the
rebuild is WRITTEN — never on a refusal, so the retry survives, and never on a
`ceiling` rebuild, which read no verdict and must not claim to have consumed
one. **`nora_standing_rederivations_total` counts the trigger firing and
`nora_standing_rederivations_written_total` counts it landing**: on the first
alone, a fold that keeps failing is indistinguishable from a latched trigger,
which is how the original defect read as health.

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

**It is a config key, with the `CHECK` as an absolute ceiling** (operator
decision, 2026-08-24). An earlier draft of this node argued the opposite — that
a storage cap is an invariant and therefore must not be a knob. The operator
overrode it, and the reconciliation is better than either pure position:

| layer | value | role |
|---|---|---|
| **`[standing] max_chars`** | **1200** | the operating cap. Tunable without a migration, because the right size is a judgement about prompts, and judgements about prompts change |
| **DB `CHECK`** | **4000** | an absolute ceiling no writer can pass, config included. Not the operating value — a backstop against a config typo or a future writer nobody has thought of |

The `CHECK` cannot be a knob — a constraint is DDL — so the two layers do
different jobs rather than duplicating one. **Config must stay at or below the
ceiling**, and a config that exceeds it fails at startup rather than at the
first oversized fold: the refuse-to-start shape this repo already uses for
`policy.toml`.

This is the general rule, and it corrects §4.0's earlier framing across the
bundle: **a render cap is config; a storage cap is config *bounded by* a
constraint.** The invariant is that some ceiling exists and is enforced in the
database — not that the operator may never choose the working value.

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
shrink, **what Nora concluded outranks what the room said** (reversed
2026-08-29, issue #78 Phase 2 — see `design/standing-split-fold/20-ratchet-and-tiebreak.md`).
The original rule ran the opposite way — «what the room said outranks what Nora
concluded about it» — on the ground that *a position is re-derivable from the
transcript; the transcript is not re-derivable from the position*. That ground
is still true for the TRACE cap, which is why `cap_messages` still pins human
turns. It does not transfer to the POSITION: the position is Nora's own
synthesis, not a transcript, and a shrink that drops her synthesis first leaves
the tier carrying other people's words wearing her name — the recap-shaped row
that is the standing tier's failure mode. Room-said belongs in the transcript,
which is retained separately; the position's job is what Nora concludes.

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

**The numbers, reconciled against what the code enforces (2026-08-27).** An
earlier draft of this section reasoned about an 80-second worst case built on
`[llm] timeout_seconds = 60` — **a value that does not exist.** That field is
REQUIRED with no default and this deployment sets **120**, so the real bounded
worst case was 140s, not 80s. The design was arguing against a number nobody had
read back.

| | value | why |
|---|---|---|
| `[llm] timeout_seconds` | **120** | the tag turn's own bound, already required and set |
| `[standing] timeout_seconds` | **20** | the debrief's model call |
| `[standing] store_timeout_seconds` | **5**, counted **twice** | the fold reads the prior and writes the next; **both** are inside the bound |
| `[standing] max_slot_hold_seconds` | **150** shipped, **45** in code | the real bound is `20 + 5×5`; `[llm] timeout_seconds` is NOT a term. The shipped files are HELD at 150 for one deploy cycle because this file is bind-mounted while the validator ships in the image — see R-70. The old formula `120 + 20 + 2×5` also reached 150, and agreeing on the value by coincidence of a wrong formula is how it went unnoticed. **Checked at startup** |

**The store calls are inside the bound, and that is the correction this section
most needed.** An implementation shipped with one `asyncio.wait_for` around the
model call only, while `store.read` and `store.write` ran **outside it with the
slot held** — so a Postgres connection hanging mid-query wedged that conversation
indefinitely, on a turn that had already delivered its reply. Its own docstring
claimed it was *"bounded twice"*; only the model half ever was. **A bound that
does not cover the call that can hang is not a bound**, and a docstring asserting
otherwise is the self-attestation this bundle refuses everywhere else.

**A cross-section startup validator, not a comment.** The four values above are
asserted against each other when config loads, so the arithmetic cannot drift
back apart. Verified by falsification rather than by argument: fed the 80-second
figure this section originally reasoned about, **the validator refuses to boot**
— the 2× error that shipped would now be a startup failure rather than a
production hang.

**What this does NOT fix, and it is wider than Standing.** The tag path already
makes unbounded database reads during context assembly, so a hung Postgres wedges
the next turn whether or not the debrief runs. Standing bounded the hang point it
*introduced*; the class-level fix is a `command_timeout` on the engine, which
every store shares and which therefore needs its own decision and its own test.
Tracked separately — it must not ride on a Standing change.

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

> **Reversed 2026-09-24: the debrief creates no notes.** Its proposals were 52 of the 64 live notes; it never saw the unit's open notes and could only create, so every rewording became a new note that was never revised, and it wrote notes about Nora's own turns into the group's notebook (#199's class). Notes are created by people on a tag and by the `note-candidates` pass (#228), and Standing writes the position only. The text below is the original design, kept as a record.

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
refused. Without it the two paths argue with each other once per turn, forever.

**As built, that refusal is PRODUCER-SIDE, not at the sink** — this section said
"at the sink" and the code does it in the debrief, before `propose` is called.
`DreamSink.propose` takes no read set, so it could not enforce this if it wanted
to. Same behaviour for the one producer that exists; a **convention rather than
a guarantee** for the curator or any second producer, and moving it to the port
is what would make it a rule (built 2026-08-28).

**The read set also fails OPEN.** It is lifted off the turn's guard in the
pipeline's `finally`, past the outer handler, so any failure of that read
degrades to an empty set rather than raising and taking down a turn that has
already replied. An empty set refuses nothing, so the failure mode is a
re-proposed note — `unavailable` behaving as `nothing_found`, in the tier whose
discipline is refusing exactly that. Accepted because the blast radius is capped
by the note store's unique index and the alternative is a lost turn; recorded
here rather than argued away.

## 8. Where Standing sits against the other tiers

It is the only tier that is **a position rather than a record.** Facts are
claims with warrants; notes are questions with closing conditions; lexemes are
meanings. **Standing is what Nora currently thinks about a conversation**,
rewritten every turn and **lossy by design.**

That is why it must not be the place a wedding venue lives
([19-between-tiers.md](19-between-tiers.md)) — a lossy, rewritten row is the
wrong home for something that has to survive. Standing points at the tiers that
keep things; it does not keep them.

## 9. 2026-09-25 — the fold is not constrained on failure, and two things recorded rather than fixed

**Operator decision.** Standing records failures as part of Nora's own
self-cognition. The fold is **not** constrained on how it records a failed or
partial attempt — there is no clean line between a normal failure ("the
search came back empty") and a harmful one ("I told the room a promise I
never kept"), and a rule drawing that line would be guessing at a boundary
rather than measuring one. `self_card`'s semantic recall — the distilled-
episode injection DEC-96 accepted onto `<self_card>` — stays exactly as it
is; nothing here narrows what it may recall.

**Evidence.** PR #480 proposed constraining the fold's failure-recording and
was closed after measurement showed the fix was not where the defect was.

- The synthetic `self_claim` corpus cases already passed on master — the
  failure this PR set out to fix was not visible to the eval suite that
  exists.
- Replays of the recorded LIVE fold calls, scored on how often the fold WROTE
  the failed-read line into `standing` — lower is better, since the line is
  the defect: v7 is live; v8 wrote the failed-read line in 3/24 draws as a
  general claim and 20/24 scoped; v8 + own-statement wrote it 24/24; F9
  carried it 23/24. None reduced it; the fold records what she said —
  the instruction was not the lever.
- A read-only study found the root cause was the **tool surface** (since
  fixed by #471/#484/#486), amplified by a **self-attestation loop**: an
  untested sentence the model wrote reaches the fold, the fold writes it into
  `standing`, `standing` reaches Honcho, and `self_card`'s semantic recall
  then hands the same untested claim back on the next similar question —
  three tiers each doing their own job correctly, compounding one unverified
  sentence into something that reads as remembered fact.

**This holds unless** a future measurement finds the fold itself — not the
tool surface, not the self-attestation loop — producing the failure; a
better-argued case for constraining it without that measurement is not
grounds to reopen this.

### The render's one age covers the whole block, not each clause

Known limitation, recorded rather than fixed. `<standing>`'s single `written`
attribute (§4.2) times the fold as a whole; a room whose position has sat
unchanged for hours but which just picked up one fresh clause renders that
clause with the same age as the days-old ones beside it — read live, a
six-hour-old clause rendered as "just now" because the FOLD that touched it
was recent, even though the clause itself was not. Standing-compact's dated
age (`design/standing-compact/10-changes.md` §2) fixed "the whole position
carries no time"; it did not give each CLAUSE its own. Splitting the age per
clause is a real property, not yet built — the reading that would take this
up is a measurement showing a stale clause riding in on a fresh fold's age
often enough to matter.

### The manual-edit marker

`standing_revision` gained a `source` column (`'fold' | 'operator'`, CHECK-
constrained, default `'fold'`) — every row before this column existed was a
fold, so the default is also the correct backfill. `SqlStandingStore.write`
keeps writing `'fold'` for all three of its callers (`debrief.py`,
`standing_event.py`, and the eval sandbox's `standing_fold.py`) unchanged;
the new `SqlStandingStore.write_if_unchanged` is the one path that writes
`'operator'`, and it refuses — writes nothing — unless the row is exactly
what was read. `scripts/standing_edit.py` is the operator entry point: it
carries `fold_count` and the row-id watermark through unchanged, because
correcting a position's TEXT is not a claim that a fold ran or that the
conversation was read any further than it already was. This closes the gap
an operator-approved manual purge hit on 2026-09-24, where two rows edited
through the ordinary `write` path recorded as folds with no way to tell them
apart from the model's own work afterward.

### A fold can still overwrite an operator edit — the compare-and-set is one-sided

`write_if_unchanged` protects the OPERATOR'S write: it refuses if a fold
landed since the operator read the row. It does nothing to protect a fold
FROM an operator's edit, and it cannot, because the fold's own write is
`SqlStandingStore.write` — a blind upsert with no compare-and-set at all, on
purpose (§9 above, "why this is a second method rather than a flag on
`write`"). A fold's shape is: read the prior, call the model (the slow step,
seconds to tens of seconds), then upsert whatever the model returned. If an
operator's edit lands inside that window — after the fold's read, before its
write — the fold's write still succeeds (nothing about it checks the row it
is about to overwrite), the operator's correction is gone, and by default
NOTHING reports it: the fold does not know an edit happened, and (before the
recheck below) the operator's own script had already printed `ok` and
exited. This is not hypothetical against the read-then-model-call gap every
fold has; it is the
same shape as the fold-vs-fold race `write_if_unchanged` closes, just on the
side nothing can close without giving the FOLD a retry path it does not have
and was never designed to need (a stalled or dropped fold is not something a
production turn can wait on). `scripts/standing_edit.py` narrows the window
it can be caught in — it re-reads the head a few seconds after its own write
and warns if the row has moved again — but a fold landing after that
re-read, or one already past its model call when the edit lands, is not
caught by anything. Recorded as a known gap, not fixed: closing it would mean
either giving the fold a compare-and-set retry (a real behaviour change to
the hot path, for an edit that happens rarely and by hand) or serializing
every write against a lock the fold does not currently take.

## Status / Checkpoint

2026-08-23 — written after the operator asked how Standing runs, rolls and is
measured, and the first answer described build status rather than design.
Standing had a **roadmap** in this bundle ([15](15-roadmap.md) §6) and no design
node; this is that node. Phase 1 (recording) is complete on an unmerged branch;
Phase 2 (this) has zero code, which is why every decision above is still free.
